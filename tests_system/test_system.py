"""Controlled reliability tests; these mocks are not model-quality evidence."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from urllib.request import Request,urlopen

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from candidate.providers import ProviderError,SpecifiedModels
from scripts.benchmark_baseline import image
from system_service.app import make_server
from system_service.providers import ScopedCache,DevelopmentModels
from system_service.store import SystemStore
from system_service.legacy_retention import purge_due
from memory_service import MemoryStore
from scripts.aml_smoke import receipt_for_version


class Fake:
    identity='controlled-test-provider-v1'
    def __init__(self):
        self.context=threading.local();self.fail=False;self.deleted=[]
    def describe(self,*args):
        if self.fail:raise ProviderError('synthetic-private-error')
        return {'caption':'red receipt','ocr':'SYNTHETIC-X','entities':[],'facts':[]}
    def embed(self,user,texts):return [[1.,0.] for _ in texts]
    def delete_user(self,user):self.deleted.append(user)


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.provider=Fake();self.store=SystemStore(self.root/'memory.db',self.provider,self.provider.identity)
    def tearDown(self):self.temp.cleanup()
    def payload(self,content='historical source',user='dev:alice',request='r1'):
        return {'user_id':user,'request_id':request,'session_id':'s1','messages':[{'role':'user','content':content,'timestamp':1262304000000}]}
    def query(self,user='dev:alice'):
        return {'user_id':user,'query':'receipt','top_k':100}
    def test_persistence_idempotency_and_namespace(self):
        self.store.add(self.payload());self.store.add(self.payload())
        reopened=SystemStore(self.root/'memory.db',self.provider,self.provider.identity)
        self.assertEqual(len(reopened.search(self.query())['data']),1)
        with self.assertRaises(RuntimeError):SystemStore(self.root/'memory.db',self.provider,'different-model')
    def test_isolation_including_matrix_cache(self):
        self.store.add(self.payload());self.store.search(self.query())
        self.assertEqual(self.store.search(self.query('dev:bob')),{'data':[]})
        self.assertNotIn('dev:bob',self.store.matrices)
    def test_failed_index_unsearchable_then_recovers(self):
        self.provider.fail=True
        with self.assertRaises(ProviderError):self.store.add(self.payload([image((200,0,0))]))
        self.assertEqual(self.store.status()['pending_records'],1)
        self.assertEqual(self.store.search(self.query()),{'data':[]})
        self.provider.fail=False;self.store.add(self.payload([image((200,0,0))]))
        self.assertEqual(self.store.status()['pending_records'],0)
        self.assertEqual(len(self.store.search(self.query())['data']),1)
    def test_retention_uses_access_time_and_removes_cache_scope(self):
        self.store.add(self.payload());self.store.search(self.query())
        self.assertEqual(self.store.purge_expired(),0) # A 2010 source timestamp is not its retention clock.
        with self.store.connect() as db:db.execute('UPDATE system_scopes SET last_seen=?',(time.time()-30*86400,))
        self.assertEqual(self.store.purge_expired(),1)
        self.assertEqual(self.store.search(self.query()),{'data':[]})
        self.assertEqual(self.provider.deleted,['dev:alice'])
        self.assertEqual(self.store.matrix_bytes,0)
    def test_cache_scope_and_durable_budget(self):
        cache=ScopedCache(self.root/'cache.db',{'vision':2});calls=[]
        def compute():calls.append(1);return {'value':'derived'}
        cache.call('dev:a','vision',['model','source'],compute)
        cache.call('dev:a','vision',['model','source'],compute)
        cache.call('dev:b','vision',['model','source'],compute)
        self.assertEqual(len(calls),2)
        again=ScopedCache(self.root/'cache.db',{'vision':2})
        with self.assertRaises(ProviderError):again.call('dev:c','vision',['model','new'],compute)
        cache.delete_user('dev:a')
        with cache.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM cache WHERE user_id=?',('dev:a',)).fetchone()[0],0)
    def test_failed_attempt_counts_and_reports_unavailable(self):
        cache=ScopedCache(self.root/'cache.db',{'vision':1})
        def fail():raise ProviderError('controlled failure')
        with self.assertRaises(ProviderError):cache.call('dev:a','vision',['x'],fail)
        self.assertEqual(cache.usage(),{'vision':1})
        self.assertEqual(cache.states(),{'vision':'unavailable'})
    def test_text_does_not_launch_cli_and_image_cache_is_scoped(self):
        calls=[]
        def run(*args):calls.append(1);return {'caption':'red','ocr':'X1','entities':[],'facts':[]}
        provider=DevelopmentModels(self.root/'model',runner=run)
        self.assertEqual(provider.describe('dev:a','text only'),{})
        source=[image((255,0,0))]
        provider.describe('dev:a',source);provider.describe('dev:a',source);provider.describe('dev:b',source)
        self.assertEqual(len(calls),2)
        self.assertEqual(list((self.root/'model/cli-tmp').iterdir()),[])
    def test_semantic_magnitude_outweighs_incidental_layout_keyword(self):
        scores={'semantic-source':1/61,'incidental':1/61+1/62}
        docs={'semantic-source':{'terms':{}},'incidental':{'terms':{'left':3}}}
        self.store.refine_scores(scores,{'semantic-source':.59,'incidental':.29},{'incidental':1},docs,{'left','automobile'},[])
        self.assertGreater(scores['semantic-source'],scores['incidental'])
    def test_exact_source_image_and_identifiers_remain_prioritized(self):
        scores={'pixel-source':1/61,'other':1/61}
        docs={'pixel-source':{'terms':{'4827':1}},'other':{'terms':{}}}
        self.store.refine_scores(scores,{'pixel-source':.1,'other':.8},{},docs,{'4827'},['pixel-source'])
        self.assertGreater(scores['pixel-source'],scores['other'])
    def test_long_source_tail_and_visual_metadata_are_retrievable(self):
        source='Unrelated routine archive notes. '*650+' TailRef 61924'
        self.store.add(self.payload([{'type':'text','text':source},image((220,0,0))]))
        for query in ('TailRef 61924','SYNTHETIC-X'):
            result=self.store.search({'user_id':'dev:alice','query':query,'top_k':1})['data']
            self.assertEqual(len(result),1)
        with self.store.connect() as db:
            terms=json.loads(db.execute('SELECT terms FROM candidate_features').fetchone()[0])
        self.assertIn('61924',terms);self.assertIn('synthetic',terms)
    def test_entity_links_cannot_additively_overtake_their_source(self):
        scores={'source':.2,'weak-linked':.12}
        self.store.merge_expansion(scores,{'weak-linked':.15})
        self.assertLess(scores['weak-linked'],scores['source'])
        self.assertEqual(scores['weak-linked'],.15)
    def test_independent_users_index_concurrently(self):
        barrier=threading.Barrier(2)
        def describe(*args):barrier.wait(timeout=3);return {}
        self.provider.describe=describe
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(self.store.add,[self.payload(user='dev:a'),self.payload(user='dev:b')]))
        self.assertTrue(all(r['success'] for r in results))
    def test_concurrent_identical_writes_do_not_duplicate_indexing(self):
        calls=[]
        def describe(*args):calls.append(1);time.sleep(.03);return {}
        self.provider.describe=describe
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(self.store.add,[self.payload(),self.payload()]))
        self.assertEqual(len(calls),1)
    def specified_provider(self,transport):
        config={'DASHSCOPE_API_KEY':'synthetic-only','DASHSCOPE_BASE_URL':'https://example.invalid/v1'}
        return SpecifiedModels(config,self.root/'specified-cache.db',transport=transport)
    def test_specified_requests_for_different_users_do_not_hold_global_network_lock(self):
        barrier=threading.Barrier(2)
        def transport(*args):
            barrier.wait(timeout=3)
            return {'model':'text-embedding-v4','data':[{'index':0,'embedding':[1.]+[0.]*1023}]}
        provider=self.specified_provider(transport)
        with ThreadPoolExecutor(max_workers=2) as pool:
            result=list(pool.map(lambda user:provider.embed(user,['source']),['a','b']))
        self.assertEqual(len(result),2)
    def test_specified_same_user_same_payload_deduplicates_parallel_calls(self):
        calls=[]
        def transport(*args):
            calls.append(1);time.sleep(.03)
            return {'model':'text-embedding-v4','data':[{'index':0,'embedding':[1.]+[0.]*1023}]}
        provider=self.specified_provider(transport)
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:provider.embed('a',['source']),range(2)))
        self.assertEqual(len(calls),1)
    def test_completed_task_retention_waits_until_due_and_keeps_other_scopes(self):
        db=MemoryStore(self.root/'data/memory.sqlite3')
        db.add(self.payload(user='retire-this'))
        db.add(self.payload(user='keep-this'))
        (self.root/'docs').mkdir()
        plan={'jobs':[{'state':'pending','confirmed_completed':True,'delete_after':time.time()+100,'user_ids':['retire-this']}]}
        (self.root/'docs/legacy-retention.private.json').write_text(json.dumps(plan))
        self.assertEqual(purge_due(self.root),0)
        self.assertEqual(purge_due(self.root,now=time.time()+101),1)
        self.assertEqual(db.search(self.query('retire-this')),{'data':[]})
        self.assertEqual(len(db.search(self.query('keep-this'))['data']),1)
    def test_smoke_receipt_is_specific_to_version_and_cannot_escape_its_directory(self):
        path=self.root/'官方Smoke.private.json';path.write_text(json.dumps({'version_id':'version_old','evaluation_id':'old-job'}))
        self.assertEqual(receipt_for_version('version_old',path),path)
        self.assertNotEqual(receipt_for_version('version_new',path),path)
        self.assertEqual(receipt_for_version('version_new',path).parent,self.root)
        with self.assertRaises(ValueError):receipt_for_version('../../escape',path)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name);(root/'secrets').mkdir()
        self.key='synthetic-test-token-not-a-real-key'
        (root/'secrets/system-service.env').write_text('SYSTEM_MODE=development\nSYSTEM_API_KEY='+self.key+'\n')
        self.server=make_server(root,port=0,provider_override=Fake())
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.server.store.closed.set();self.server.store.thread.join(timeout=3)
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()
    def call(self,path,payload=None,auth=True):
        headers={'Content-Type':'application/json'}
        if auth:headers['Authorization']='Bearer '+self.key
        request=Request('http://127.0.0.1:'+str(self.server.server_port)+path,
                        data=None if payload is None else json.dumps(payload).encode(),headers=headers)
        try:
            with urlopen(request,timeout=5) as response:return response.status,json.load(response)
        except HTTPError as e:return e.code,json.load(e)
    def test_http_contract_auth_and_no_official_data_in_development(self):
        payload={'user_id':'dev:alice','request_id':'r1','session_id':'s1','messages':[{'role':'user','content':'source'}]}
        self.assertEqual(self.call('/add',payload)[0],200)
        self.assertEqual(self.call('/search',{'user_id':'dev:alice','query':'source','top_k':1})[1]['data'][0]['content'],'source')
        self.assertEqual(self.call('/status',auth=False)[0],401)
        self.assertEqual(self.call('/status')[0],200)
        self.assertEqual(self.call('/add',payload,auth=False)[0],401)
        payload['user_id']='eval:official:sample'
        self.assertEqual(self.call('/add',payload)[0],403)
        self.assertEqual(self.call('/health',auth=False)[1]['version'],'0.3.2-system')
    def test_dependency_failure_is_retryable_and_sanitized(self):
        self.server.store.store.provider.fail=True
        code,body=self.call('/add',{'user_id':'dev:alice','request_id':'r1','session_id':'s1','messages':[{'role':'user','content':[image((250,0,0))]}]})
        self.assertEqual(code,503)
        self.assertNotIn('synthetic-private-error',json.dumps(body))


if __name__=='__main__':unittest.main()
