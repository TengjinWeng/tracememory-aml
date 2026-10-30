"""Development algorithm: ordered visual evidence and topic-scoped state updates."""
import json
import threading

from candidate.store import entities
from candidate.vision import pixel_fingerprints
from memory_service import canonical, tokenize, validate_content, ContractError
from system_service.relations import spatial_agreement, state_adjustments
from system_service.modality import wants_image_evidence, prefer_image_evidence
from system_service.store import SystemStore


class AlgorithmStore(SystemStore):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.ranking_context=threading.local()
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS ordered_evidence_v032(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,sections TEXT NOT NULL,names TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS ordered_evidence_scope ON ordered_evidence_v032(user_id)')

    def feature_row(self,user,identity,content):
        text,_,_=validate_content(content)
        try:pixels=pixel_fingerprints(content)
        except Exception as error:raise ContractError('could not decode source image',422) from error
        description=self.provider.describe(user,content) if self.provider else {}
        derived,chunks=self.index_documents(text,description)
        names=set(entities(derived))|{x.casefold() for x in description.get('entities',[]) if x.strip()}
        vectors=self.provider.embed(user,chunks) if self.provider and chunks else []
        sections=[s for s in [text,description.get('caption',''),description.get('ocr','')]+description.get('facts',[]) if s.strip()]
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO ordered_evidence_v032 VALUES (?,?,?,?)',
                       (identity,user,canonical(sections),canonical(description.get('entities',[]))))
        return identity,user,canonical(tokenize(derived)),canonical(sorted(names)),canonical(pixels),canonical(vectors)

    def _search(self,payload):
        previous=getattr(self.ranking_context,'payload',None)
        self.ranking_context.payload=payload
        try:return super()._search(payload)
        finally:self.ranking_context.payload=previous

    def refine_scores(self,fused,dense,lexical,docs,query,matches):
        super().refine_scores(fused,dense,lexical,docs,query,matches)
        payload=getattr(self.ranking_context,'payload',None)
        if payload is None:return
        text,images,_=validate_content(payload['query'])
        if wants_image_evidence(text, bool(images)):
            prefer_image_evidence(fused, dense, docs)
        if images and not matches and self.provider:
            # CandidateStore already extracted this exact user/query; this lookup must hit its scoped cache.
            description=self.provider.describe(payload['user_id'],payload['query'])
            text+='\n'+'\n'.join([description.get('caption',''),description.get('ocr','')]+description.get('facts',[]))
        candidates=sorted(fused,key=lambda key:(-fused[key],key))[:128]
        if not candidates:return
        with self.connect() as db:
            rows=db.execute('SELECT id,sections,names FROM ordered_evidence_v032 WHERE user_id=? AND id IN ('+','.join('?' for _ in candidates)+')',
                            [payload['user_id']]+candidates).fetchall()
        evidence={identity:{'sections':json.loads(sections),'names':json.loads(names)} for identity,sections,names in rows if identity in docs}
        for identity in list(fused):
            item=evidence.get(identity)
            if item:fused[identity]+=.14*spatial_agreement(text,item['sections'],item['names'])
        for identity,adjustment in state_adjustments(text,docs,evidence,dense).items():
            if identity in fused:fused[identity]+=adjustment

    def purge_expired(self,*args,**kwargs):
        removed=super().purge_expired(*args,**kwargs)
        # Raw rows remain the retention authority, including failed/pending ingestion rows.
        with self.lifecycle.write(),self.connect() as db:
            db.execute('PRAGMA secure_delete=ON')
            cursor=db.execute('DELETE FROM ordered_evidence_v032 WHERE NOT EXISTS (SELECT 1 FROM memories m WHERE m.id=ordered_evidence_v032.id AND m.user_id=ordered_evidence_v032.user_id)')
            orphaned=cursor.rowcount
        if orphaned:
            with self.connect() as db:db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            with self.connect() as db:db.execute('VACUUM')
        return removed

    def status(self):
        report=super().status()
        with self.connect() as db:
            missing=db.execute('SELECT count(*) FROM candidate_features f LEFT JOIN ordered_evidence_v032 e ON f.id=e.id AND f.user_id=e.user_id WHERE e.id IS NULL').fetchone()[0]
        report.update(ranking_policy='ordered-spatial-scoped-state-modality-v2',ordered_evidence_missing=missing)
        return report
