"""Scoped matrix retrieval, explicit index identity, and ingestion-time retention."""
from collections import OrderedDict
import json
import os
import re
import threading
import time

import numpy as np
from candidate.store import CandidateStore
from memory_service import required_string
from system_service.concurrency import ActivityGate


class SystemStore(CandidateStore):
    def __init__(self,path,provider,identity):
        super().__init__(path,provider=provider,chunk_chars=384,max_images=8,max_image_bytes=28*1024*1024)
        self.matrix_lock=threading.RLock()
        self.matrices=OrderedDict()
        self.matrix_bytes=0
        self.identity=identity
        self.lifecycle=ActivityGate()
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS system_identity(identity TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS system_scopes(user_id TEXT PRIMARY KEY,last_seen REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS system_metrics(name TEXT PRIMARY KEY,value INTEGER NOT NULL);
            """)
            previous=db.execute("SELECT identity FROM system_identity").fetchone()
            if previous and previous[0]!=identity:
                raise RuntimeError("model/index identity changed; use a separate database")
            if not previous:
                db.execute("INSERT INTO system_identity VALUES (?)",(identity,))
        os.chmod(self.path,0o600)

    def index_documents(self, text, description):
        # Keep visual evidence separate from a potentially long source. Overlapping windows
        # preserve boundary phrases; neither source tails nor OCR are discarded by a prefix cap.
        sections = [text, description.get('caption', ''), description.get('ocr', '')] + description.get('facts', [])
        chunks, seen = [], set()
        step = self.chunk_chars - 64
        for section in sections:
            for start in range(0, len(section), step):
                chunk = section[start:start+self.chunk_chars]
                if chunk.strip() and chunk not in seen:
                    chunks.append(chunk); seen.add(chunk)
        return '\n'.join(sections), chunks

    def rebuild_development_features(self):
        # Explicit, offline maintenance. Raw sources, request hashes and model budgets survive.
        with self.lifecycle.write():
            with self.connect() as db:
                rows = db.execute('SELECT m.user_id,m.id,m.content FROM memories m JOIN candidate_features f ON m.id=f.id AND m.user_id=f.user_id ORDER BY m.user_id,m.id').fetchall()
            if any(not user.startswith('dev:') for user, _, _ in rows):
                raise ValueError('reindex maintenance accepts self-authored dev: scopes only')
            prepared = [self.feature_row(user, identity, json.loads(content)) for user, identity, content in rows]
            with self.connect() as db:
                db.executemany('INSERT OR REPLACE INTO candidate_features VALUES (?,?,?,?,?,?)', prepared)
            with self.matrix_lock:
                self.matrices.clear(); self.matrix_bytes = 0
            return len(prepared)

    def _touch(self,user,create=False):
        with self.connect() as db:
            if create:
                db.execute("INSERT INTO system_scopes VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET last_seen=excluded.last_seen",(user,time.time()))
            else:
                db.execute("UPDATE system_scopes SET last_seen=? WHERE user_id=?",(time.time(),user))

    def metric(self,name):
        with self.connect() as db:
            db.execute("INSERT INTO system_metrics VALUES (?,1) ON CONFLICT(name) DO UPDATE SET value=value+1",(name,))

    def add(self,payload):
        user=required_string(payload.get('user_id'),'user_id')
        with self.lifecycle.read(), self.lifecycle.user_lock(user):
            return self._add(payload)

    def _add(self,payload):
        # Failed indexing may have durably stored raw rows. Give those rows a retention scope too.
        try:
            result=self.index_payload(payload)
        finally:
            user=payload.get('user_id') if isinstance(payload,dict) else None
            if isinstance(user,str):
                with self.connect() as db:
                    exists=db.execute("SELECT 1 FROM memories WHERE user_id=? LIMIT 1",(user,)).fetchone()
                if exists:self._touch(user,create=True)
        with self.matrix_lock:
            old=self.matrices.pop(payload['user_id'],None)
            if old:self.matrix_bytes-=old[1].nbytes+old[2].nbytes
        return result

    def search(self,payload):
        with self.lifecycle.read():
            return self._search(payload)

    def _search(self,payload):
        context=getattr(self.provider,'context',None)
        previous=getattr(context,'input_type','document') if context else None
        if context:context.input_type='query'
        try:
            result=super().search(payload)
            self._touch(payload['user_id'])
            return result
        finally:
            if context:context.input_type=previous

    def rank_dense(self,user,docs,vector):
        keys=tuple(sorted(docs))
        with self.matrix_lock:
            cached=self.matrices.get(user)
            if not cached or cached[0]!=keys:
                if cached:self.matrix_bytes-=cached[1].nbytes+cached[2].nbytes
                values,owners=[],[]
                for index,key in enumerate(keys):
                    for row in docs[key]['vectors']:
                        if len(row)!=len(vector):raise ValueError('embedding dimensions differ')
                        values.append(row);owners.append(index)
                matrix=np.asarray(values,dtype=np.float32).reshape((-1,len(vector)))
                owner=np.asarray(owners,dtype=np.int64)
                cached=(keys,matrix,owner)
                self.matrices[user]=cached
                self.matrix_bytes+=matrix.nbytes+owner.nbytes
            self.matrices.move_to_end(user)
            while len(self.matrices)>8 or self.matrix_bytes>128*1024*1024:
                _,old=self.matrices.popitem(last=False)
                self.matrix_bytes-=old[1].nbytes+old[2].nbytes
        scores=np.full(len(keys),-1.,dtype=np.float32)
        if len(cached[1]):np.maximum.at(scores,cached[2],cached[1]@np.asarray(vector,dtype=np.float32))
        return {key:float(score) for key,score in zip(keys,scores) if score>.05}

    def purge_expired(self,now=None,retention_days=29):
        cutoff=(time.time() if now is None else now)-retention_days*86400
        removed=0
        with self.lifecycle.write(), self.connect() as db:
            db.execute('PRAGMA secure_delete=ON')
            users=[r[0] for r in db.execute('SELECT user_id FROM system_scopes WHERE last_seen<?',(cutoff,))]
            for user in users:
                if hasattr(self.provider,'delete_user'):
                    self.provider.delete_user(user)
                elif hasattr(self.provider,'db'):
                    with self.provider.db() as cache:
                        cache.execute('PRAGMA secure_delete=ON')
                        cache.execute('DELETE FROM api_cache WHERE user_id=?',(user,))
                for table in ('candidate_features','memories','requests','system_scopes'):
                    db.execute('DELETE FROM '+table+' WHERE user_id=?',(user,))
                with self.matrix_lock:
                    cached=self.matrices.pop(user,None)
                    if cached:self.matrix_bytes-=cached[1].nbytes+cached[2].nbytes
                removed+=1
            db.execute('PRAGMA secure_delete=ON')
        # WAL checkpoints and VACUUM remove deleted source bytes from active SQLite files.
        if removed:
            with self.connect() as db:db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            with self.connect() as db:db.execute('VACUUM')
            self.metric('retention_scopes_removed')
        return removed

    def refine_scores(self,fused,dense,lexical,docs,query,matches):
        # RRF flattens nearby ranks, particularly in small corpora. Retain semantic magnitude
        # so an incidental layout word cannot overwhelm a much stronger semantic match.
        identifiers={term for term in query if re.search(r'\d',term) and len(term)>=2}
        for identity in fused:
            fused[identity]+=.2*max(0.,dense.get(identity,0.))
            if identifiers:
                fused[identity]+=.1*len(identifiers & docs[identity]['terms'].keys())/len(identifiers)
            if identity in matches:fused[identity]+=.5

    def merge_expansion(self, fused, expanded):
        # Links can rescue missing evidence without adding a second full score to an
        # already weak candidate. Additive expansion promoted unrelated generic entities.
        for identity, score in expanded.items():
            fused[identity] = max(fused[identity], score)

    def status(self):
        with self.connect() as db:
            return {'metrics':dict(db.execute('SELECT name,value FROM system_metrics')),
                    'scopes':db.execute('SELECT count(*) FROM system_scopes').fetchone()[0],
                    'pending_records':db.execute('SELECT count(*) FROM memories m LEFT JOIN candidate_features f ON m.id=f.id AND m.user_id=f.user_id WHERE f.id IS NULL').fetchone()[0]}
