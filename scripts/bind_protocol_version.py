"""Bind the disclosed model-free Smoke profile to the existing AML key, once."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from urllib.request import urlopen
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.aml_smoke import Client, public_health
from scripts.prepare_application import write_private

VERSION = '0.3.1-protocol'
PREFIX = '/v0.3.1-protocol'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind', action='store_true')
    parser.add_argument('--retry-reconciled', action='store_true', help='retry one prior attempt only after fresh version/job checks show no version')
    args = parser.parse_args()
    client = Client((ROOT/'secrets/aml-key.txt').read_text().strip())
    info = json.loads((ROOT/'docs/私人报名信息.json').read_text())
    base = info['public_base_url'].rstrip('/')
    current = client.api('/access/versions')
    versions = current.get('versions', [])
    existing = [v for v in versions if v.get('version_name') == VERSION]
    receipt_path = ROOT/'docs/新增协议版本.private.json'
    if existing:
        if len(existing) != 1: raise SystemExit('Version name is ambiguous; no binding attempted')
        write_private(receipt_path, {'state': 'bound', 'version': existing[0]})
        print(json.dumps({'state':'already_bound', 'version_id':existing[0]['id'], 'version_name':VERSION}))
        return
    if receipt_path.exists() and not args.retry_reconciled:
        raise SystemExit('A previous binding attempt has uncertain state; do not submit again')
    source = [v for v in versions if v.get('version_name') == '0.1.0-protocol' and v.get('status') == 'active']
    if len(source) != 1 or current.get('version_binding', {}).get('allowed') is False:
        raise SystemExit('No unique active source version or binding is blocked')
    jobs = client.api('/api/v1/evaluations?limit=100&offset=0')
    if jobs.get('next_offset') is not None or any(j.get('status') not in ('succeeded','failed','cancelled','canceled') for j in jobs.get('evaluations', [])):
        raise SystemExit('Job history is incomplete or a job remains active; no binding attempted')
    for path, expected in (('/v0.1.0-protocol/health', '0.1.0-protocol'), ('/health', VERSION)):
        health = public_health(base+path)
        if health.get('status') != 'ok' or health.get('version') != expected:
            raise SystemExit('Public version routing is not verified')
    # Live API rejects changed addresses even though the public UI offers new URL fields.
    payload = {'version_name':VERSION, 'add_endpoint':base+'/add',
               'search_endpoint':base+'/search', 'source_version_id':source[0]['id']}
    if not args.bind:
        print('Read-only preflight passed. No version bound; no evaluation started.')
        return
    receipt = {'state':'sending', 'attempted_at':datetime.now(timezone.utc).isoformat(), 'payload':payload,
               'profile':'model-free evidence protocol; no CLI/LLM/Embedding/Reranker; Smoke only',
               'source_commit': __import__('subprocess').check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()}
    if receipt_path.exists():
        receipt['previous_attempt'] = json.loads(receipt_path.read_text())
    write_private(receipt_path, receipt)
    try:
        result = client.api('/access/versions', payload)
    except HTTPError as error:
        receipt.update(state='rejected_http', http_status=error.code,
                       error_response=error.read(16000).decode('utf-8', errors='replace'))
        write_private(receipt_path, receipt)
        raise SystemExit('Version binding rejected with HTTP '+str(error.code)+'; diagnostic saved privately')
    except Exception as error:
        receipt.update(state='unknown', error_type=type(error).__name__, error_detail=str(error))
        write_private(receipt_path, receipt)
        raise SystemExit('Binding state uncertain. Inspect versions before any further mutation.')
    receipt.update(state='bound', response=result, version=result.get('version'))
    write_private(receipt_path, receipt)
    version = result.get('version', {})
    print(json.dumps({'state':'bound', 'version_id':version.get('id'), 'version_name':VERSION, 'evaluation_started':False}))


if __name__ == '__main__': main()
