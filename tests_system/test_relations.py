"""Counterexamples for directional evidence, historical state, isolation and deletion."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from system_service.relations import spatial_agreement
from system_service.ranking import AlgorithmStore


class RelationTests(unittest.TestCase):
    def test_inverse_queries_preserve_subject_and_object(self):
        names=['red cup','green book']
        sections=['A red cup to the left of the green book.']
        self.assertGreater(spatial_agreement('green book to the right of the red cup',sections,names),0)
        self.assertLess(spatial_agreement('green book to the left of the red cup',sections,names),0)

    def test_absolute_layout_does_not_confuse_title_and_objects(self):
        names=['blue circle','orange triangle','title text']
        sections=['The title text is on the left. A blue circle on the right and an orange triangle on the left.']
        self.assertGreater(spatial_agreement('blue circle to the right of orange triangle',sections,names),0)
        self.assertLess(spatial_agreement('blue circle to the left of orange triangle',sections,names),0)

    def test_negation_and_uncertainty_do_not_create_positive_relations(self):
        q='the red cup is above the green book';names=['red cup','green book']
        for source in ('The red cup is not above the green book.','The red cup might be above the green book.'):
            self.assertEqual(spatial_agreement(q,[source],names),0)

    def test_different_color_entity_cannot_be_replaced_by_its_bare_noun(self):
        self.assertEqual(spatial_agreement('blue cup to the left of the green book',
                         ['A red cup to the left of the green book.'],['red cup','green book']),0)

    def test_relative_vertical_facts_match_plain_noun_queries(self):
        names=['purple square','golden-yellow five-pointed star']
        section=['A solid golden-yellow five-pointed star is centered horizontally below the square.']
        self.assertGreater(spatial_agreement('the star is below the square',section,names),0)


class Fake:
    identity='self-authored-state-control'
    def __init__(self):self.context=threading.local()
    def describe(self,*args):return {}
    def embed(self,user,texts):return [[1.,0.] for _ in texts]
    def delete_user(self,user):pass


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=AlgorithmStore(self.root/'memory.db',Fake(),Fake.identity)
        for request,text,stamp in [
            ('old','Nora currently lives in Paris. Her current home address is Paris.',1767225600000),
            ('new','Nora moved from Paris to Bern. Bern is her new home.',1780272000000),
            ('invoice','Nora saved a restaurant receipt for yesterday.',1790812800000),
            ('other','Milo currently lives in Bonn.',1790812800000),
        ]:
            self.store.add({'user_id':'dev:state','request_id':request,'session_id':'s',
                            'messages':[{'role':'user','content':text,'timestamp':stamp}]})
    def tearDown(self):self.temp.cleanup()
    def search(self,q,user='dev:state'):
        return self.store.search({'user_id':user,'query':q,'top_k':100})['data']

    def test_latest_state_ignores_newer_upload_and_other_person(self):
        results=self.search('Where does Nora currently live?')
        self.assertIn('new home',results[0]['content'])
        self.assertEqual(self.search('Where does Nora currently live?','dev:other'),[])

    def test_historical_query_does_not_apply_current_state_preference(self):
        result=self.search('Where did Nora live as of January 2026?')
        self.assertEqual(result[0]['content'],'Nora currently lives in Paris. Her current home address is Paris.')

    def test_auxiliary_evidence_is_deleted_with_raw_scope(self):
        self.search('Nora')
        with self.store.connect() as db:db.execute('UPDATE system_scopes SET last_seen=?',(time.time()-30*86400,))
        self.assertEqual(self.store.purge_expired(),1)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM ordered_evidence_v032').fetchone()[0],0)
        self.assertEqual(self.search('Nora'),[])

    def test_chinese_person_anchor_excludes_temporal_adverbs(self):
        for request,text,stamp in [
            ('zh-old','赵明现在住在南京。',1767225600000),
            ('zh-new','赵明搬家到了合肥，合肥是他的新住址。',1780272000000),
            ('zh-bill','赵明昨天保存了一张书店收据。',1790812800000),
        ]:
            self.store.add({'user_id':'dev:state','request_id':request,'session_id':'s',
                            'messages':[{'role':'user','content':text,'timestamp':stamp}]})
        self.assertIn('合肥',self.search('赵明现在住在哪里？')[0]['content'])

    def test_day_cutoff_does_not_include_later_updates_in_the_same_month(self):
        for request,text,stamp in [
            ('jan-late','Nora moved to Milan, which is her new home.',1768867200000),
        ]:
            self.store.add({'user_id':'dev:state','request_id':request,'session_id':'s',
                            'messages':[{'role':'user','content':text,'timestamp':stamp}]})
        result=self.search('Where did Nora live as of 2026-01-15?')
        self.assertIn('Paris',result[0]['content'])


if __name__=='__main__':unittest.main()
