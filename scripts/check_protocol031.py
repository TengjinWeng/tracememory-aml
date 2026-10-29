"""Public self-authored routing and 16-client contract check; no AML or model requests."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from benchmark_baseline import image

ROOT = Path(__file__).resolve().parents[1]
PREFIX = '/v0.3.1-protocol'


def main():
    info = json.loads((ROOT/'docs/私人报名信息.json').read_text())
    key = json.loads((ROOT/'docs/评测申请.private.json').read_text())['memory_api_key']
    base = info['public_base_url'].rstrip('/')
    def call(path, body=None, auth=True):
        headers = {'Content-Type':'application/json'}
        if auth: headers['Authorization'] = 'Bearer '+key
        req = Request(base+path, data=None if body is None else json.dumps(body).encode(), headers=headers)
        try:
            with urlopen(req, timeout=60) as response: return response.status, json.load(response)
        except HTTPError as error: return error.code, json.load(error)
    assert call('/health')[1]['version'] == '0.3.1-protocol'
    assert call('/v0.1.0-protocol/health')[1]['version'] == '0.1.0-protocol'
    assert call(PREFIX+'/health')[1]['version'] == '0.3.1-protocol'
    scope = 'self-authored:protocol031:'+uuid4().hex
    def check(index):
        user = scope+':'+str(index)
        content = [{'type':'text','text':'Self-authored red marker reference '+str(index)},image((255,0,0))]
        payload = {'user_id':user,'session_id':'self-authored','request_id':'write','messages':[{'role':'user','content':content}]}
        code, ack = call('/add', payload)
        assert code == 200 and ack['success'] and ack['request_id'] == 'write'
        assert call('/add', payload) == (200, ack)
        query = {'user_id':user,'query':[image((255,0,0), level=0)],'top_k':1}
        code, result = call('/search', query)
        assert code == 200 and result['data'][0]['content'] == content
        return True
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=16) as pool: passed = list(pool.map(check, range(16)))
    query = {'user_id':scope+':0','query':'red marker','top_k':1}
    assert call('/v0.1.0-protocol/search', query) == (200, {'data':[]})
    assert call('/search', query, auth=False)[0] == 401
    query['user_id'] = scope+':absent'
    assert call('/search', query) == (200, {'data':[]})
    result = {'created_at':datetime.now(timezone.utc).isoformat(), 'version':'0.3.1-protocol',
              'root_version':'0.3.1-protocol','legacy_diagnostic_prefix_preserved':True,'parallel_clients':16,'successful_roundtrips':sum(passed),
              'idempotency':'passed','source_images_preserved':'passed','isolation':'passed','authentication':'passed',
              'elapsed_seconds':round(time.monotonic()-started,3),'models_called':False,'official_evaluation_started':False,
              'limitations':'Small self-authored public integration check, not Full-suite capacity certification.'}
    (ROOT/'docs/protocol031-public-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__': main()
