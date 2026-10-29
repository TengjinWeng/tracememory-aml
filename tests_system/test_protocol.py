"""Verify version routing, auth and evidence isolation without calling model providers."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from memory_service import MemoryServer, MemoryStore
from system_service.protocol import make_server, PREFIX, VERSION
from scripts.benchmark_baseline import image


class ProtocolTests(unittest.TestCase):
    def test_legacy_version_and_new_version_keep_separate_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'secrets').mkdir()
            key = 'synthetic-protocol-test-key-123456789'
            (root/'secrets/protocol-service.env').write_text('MEMORY_API_KEY='+key+'\n')
            legacy = MemoryServer(('127.0.0.1', 0), MemoryStore(root/'legacy.sqlite3'), key)
            server = make_server(root, port=0, legacy_port=legacy.server_port)
            threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (legacy, server)]
            for t in threads: t.start()
            def call(path, body=None, token=key):
                req = Request('http://127.0.0.1:'+str(server.server_port)+path,
                              data=None if body is None else json.dumps(body).encode(),
                              headers={'Content-Type': 'application/json', 'Authorization': 'Bearer '+token})
                try:
                    with urlopen(req, timeout=10) as response: return response.status, json.load(response)
                except HTTPError as error: return error.code, json.load(error)
            try:
                self.assertEqual(call('/v0.1.0-protocol/health')[1]['version'], '0.1.0-protocol')
                self.assertEqual(call('/health')[1]['version'], VERSION)
                self.assertEqual(call(PREFIX+'/health')[1]['version'], VERSION)
                self.assertIsNone(call(PREFIX+'/health')[1]['llm'])
                source = [{'type': 'text', 'text': 'A source photograph of a red marker.'}, image((255,0,0))]
                payload = {'user_id': 'official-shaped-test-scope', 'request_id': 'r1', 'session_id': 's1',
                           'messages': [{'role': 'user', 'content': source}]}
                self.assertEqual(call(PREFIX+'/add', payload, token='wrong')[0], 401)
                self.assertEqual(call(PREFIX+'/add', payload)[0], 200)
                self.assertEqual(call(PREFIX+'/add', payload)[0], 200)
                query = {'user_id': payload['user_id'], 'query': [image((255,0,0), level=0)], 'top_k': 1}
                result = call(PREFIX+'/search', query)
                self.assertEqual(result[0], 200); self.assertEqual(result[1]['data'][0]['content'], source)
                self.assertEqual(call('/v0.1.0-protocol/search', query), (200, {'data': []}))
                self.assertEqual(call('/search', query)[1]['data'][0]['content'], source)
                query['user_id'] = 'different-scope'
                self.assertEqual(call(PREFIX+'/search', query), (200, {'data': []}))
                payload['user_id'] = 'legacy-scope'
                self.assertEqual(call('/v0.1.0-protocol/add', payload)[0], 200)
                self.assertEqual(len(legacy.store.search({'user_id':'legacy-scope','query':'red marker','top_k':1})['data']),1)
            finally:
                server.store.closed.set(); server.store.thread.join(timeout=3)
                for s in (server, legacy): s.shutdown(); s.server_close()
                for t in threads: t.join()


if __name__ == '__main__': unittest.main()
