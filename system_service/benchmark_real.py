"""Frozen public-domain real-image retrieval test; never accesses official data.

Use freeze, ingest, dev-before, dev-after, then holdout. All model calls go
through the running development service and its existing persistent budget.
The final held-out phase is write-once and requires an explicit frozen source.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import time
from urllib.request import Request, urlopen

from candidate.config import read_config
from memory_service import canonical

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data-system/real-corpus-v1'
MANIFEST = ROOT / 'docs/real-corpus-v1-manifest.json'
USER = 'dev:met-open-access-v1'
IMAGES = {13404: 'dev', 191965: 'dev', 500905: 'holdout',
          21975: 'holdout', 14430: 'holdout', 195357: 'holdout'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze():
    if MANIFEST.exists():
        raise SystemExit('Manifest already frozen; refusing to replace it')
    catalogue = json.loads((DATA / 'catalogue.json').read_text())
    cases = []
    queries = {
        13404: [
            'Find the original picture of the metal teapot.',
            'Find the photo with a curved spout on the right and a loop handle on the left.',
            '找那张银色、有盖茶壶的照片。'],
        191965: [
            'Find the original photo of the seated ceramic cat.',
            'Find the animal picture on a round base against a dark grey background.',
            '哪张照片是黄褐色釉面的猫雕像？'],
        500905: [
            'Find the original photograph of a violin.',
            'Find the image of the brown wooden string instrument with two f-shaped sound holes.',
            '找出棕色小提琴的原始图片。'],
        21975: [
            'Find the original photograph of a helmet.',
            'Find the photo of a conical metal headpiece with a very tall pointed spike.',
            '找出顶上有细长尖刺的金属头盔照片。'],
        14430: [
            'Find the original photograph of a chair.',
            'Find the picture of the dark wooden seat with floral upholstery and a tall slatted back.',
            '找那张带花纹软垫、黑色木框椅子的图片。'],
        195357: [
            'Find the original photograph of a dragon sculpture.',
            'Find the image of the winged creature standing on a small base with its mouth open.',
            '找出张嘴、长翅膀的龙形雕像照片。'],
    }
    for identity, texts in queries.items():
        for index, query in enumerate(texts):
            cases.append({'case': f'{identity}-{index}', 'split': IMAGES[identity],
                          'kind': 'text-to-image', 'query': query, 'expected_objects': [identity],
                          'language': 'zh' if index == 2 else 'en'})
    query_url = json.loads((DATA / 'metadata/500905.json').read_text())['additionalImages'][0].replace('/original/', '/web-large/')
    cases.append({'case': 'violin-unseen-back-view', 'split': 'holdout', 'kind': 'image-to-image',
                  'query_image': '500905-query.jpg', 'query_image_url': query_url,
                  'query_image_sha256': digest(DATA / 'images/500905-query.jpg'),
                  'expected_objects': [500905], 'language': 'image'})
    for name, query, expected in [
        ('two-instruments', 'Find both the original photograph of the violin and the catalogue record for a mandolin.', [500905, 500553]),
        ('two-original-images', 'Find the original photographs of the helmet and the dragon sculpture.', [21975, 195357]),
    ]:
        # The mandolin record ID is resolved below with all equivalent catalogue
        # mandolins as acceptable evidence; it is never embedded in the query.
        cases.append({'case': name, 'split': 'holdout', 'kind': 'multi-source',
                      'query': query, 'expected_objects': expected, 'language': 'en'})
    records = catalogue['records']
    for row in records:
        identity = row['objectID']
        row['input_modality'] = 'image-only' if identity in IMAGES else 'catalogue-text'
        if identity in IMAGES:
            row['image_file'] = f'{identity}.jpg'
            row['image_sha256'] = digest(DATA / 'images' / row['image_file'])
    # Equivalent human-labelled mandolin records are a relevance group, rather
    # than arbitrarily declaring only one catalogue entry correct.
    cases[-2]['relevance_groups'] = [[500905], [r['objectID'] for r in records if r['sampling_term'] == 'mandolin']]
    result = {'created_at': datetime.now(timezone.utc).isoformat(),
              'source': catalogue['source'], 'license': 'CC0', 'documentation': catalogue['documentation'],
              'catalogue_sha256': digest(DATA / 'catalogue.json'), 'records': records, 'cases': cases,
              'protocol': {'images': 6, 'catalogue_text_distractors': 34,
                           'development_image_objects': [13404, 191965],
                           'heldout_image_objects': [500905, 21975, 14430, 195357],
                           'development_queries': 6, 'heldout_queries': 15,
                           'labels': 'Human catalogue classes plus manually verified visible attributes; queries written before any model calls or retrieval scoring.',
                           'separation': 'No target catalogue text is indexed for the six image-only records. Images and query labels were newly introduced to this project; model pretraining exposure is unknown.',
                           'limitations': 'Small museum-object sample, not a daily-life/document/OCR benchmark or an official AML score. Multiple queries per image are correlated.'}}
    MANIFEST.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    print(json.dumps({'manifest_sha256': digest(MANIFEST), 'records': 40, 'image_records': 6,
                      'development_queries': 6, 'heldout_queries': 15}))


def image_part(path):
    return {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(path.read_bytes()).decode()}}


def catalogue_text(row):
    fields = ('title', 'objectName', 'medium', 'culture', 'objectDate', 'classification', 'department')
    text = '\n'.join(f'{key}: {row[key]}' for key in fields if row.get(key))
    return 'Museum catalogue record.\n' + text + '\nSubjects: ' + ', '.join(t['term'] for t in (row.get('tags') or []))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['freeze', 'ingest', 'dev-before', 'dev-after', 'holdout'])
    parser.add_argument('--frozen-commit')
    args = parser.parse_args()
    if args.phase == 'freeze':
        freeze()
        return
    manifest = json.loads(MANIFEST.read_text())
    for row in manifest['records']:
        if row.get('image_file') and digest(DATA / 'images' / row['image_file']) != row['image_sha256']:
            raise SystemExit('Image does not match frozen hash')
    config = read_config(ROOT / 'secrets/system-service.env')
    if config['SYSTEM_MODE'] != 'development':
        raise SystemExit('Development mode required')

    def call(path, payload=None):
        request = Request('http://127.0.0.1:' + config['SYSTEM_PORT'] + path,
                          data=None if payload is None else canonical(payload).encode(),
                          headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + config['SYSTEM_API_KEY']})
        with urlopen(request, timeout=300) as response:
            return json.load(response)

    before = call('/status')
    ids = {row['objectID']: 'mem_' + hashlib.sha256(canonical([USER, 'met-real-v1:' + str(row['objectID']), 0]).encode()).hexdigest()
           for row in manifest['records']}
    output = ROOT / 'docs' / ('real-corpus-v1-' + args.phase + '.json')
    if output.exists():
        raise SystemExit('Phase report already exists; refusing to silently repeat or overwrite it')
    started = time.monotonic()
    if args.phase == 'ingest':
        if before['development_cli_attempts_remaining'] < 7:
            raise SystemExit('Need six image attempts plus one reserved query inside the existing cap; no cap changes')
        times = []
        for row in manifest['records']:
            t = time.monotonic()
            content = [image_part(DATA / 'images' / row['image_file'])] if row.get('image_file') else catalogue_text(row)
            ack = call('/add', {'user_id': USER, 'request_id': 'met-real-v1:' + str(row['objectID']),
                                'session_id': 'met-open-access', 'messages': [{'role': 'user', 'content': content}]})
            if not ack.get('success'):
                raise SystemExit('Ingestion failed; no automatic retry')
            times.append({'object_id': row['objectID'], 'modality': row['input_modality'], 'seconds': round(time.monotonic() - t, 3)})
            print(json.dumps({'ingested': len(times), 'object_id': row['objectID'], 'seconds': times[-1]['seconds']}), flush=True)
        result = {'ingestion_timings': times}
    else:
        split = 'dev' if args.phase.startswith('dev') else 'holdout'
        if split == 'holdout':
            if not args.frozen_commit:
                raise SystemExit('A frozen source commit is required before held-out scoring')
            if not (ROOT / 'docs/real-corpus-v1-dev-after.json').exists():
                raise SystemExit('Finish development validation before accessing held-out scores')
            receipt = ROOT / 'docs/real-corpus-v1-source-freeze.json'
            frozen = json.loads(receipt.read_text())
            if frozen['commit'] != args.frozen_commit:
                raise SystemExit('Frozen source commit mismatch')
            for name, expected in frozen['source_sha256'].items():
                path = ROOT / name
                if path.resolve().is_relative_to(ROOT.resolve()) and digest(path) == expected:
                    continue
                raise SystemExit('Runtime source differs from the frozen commit: ' + name)
            # Lock held-out access before the first query, so an interrupted run
            # cannot silently become another chance at tuning the same sample.
            lock = DATA / 'holdout.started.json'
            with lock.open('x') as stream:
                json.dump({'commit': args.frozen_commit, 'manifest_sha256': digest(MANIFEST),
                           'source_receipt_sha256': digest(receipt)}, stream)
        rows = []
        for case in manifest['cases']:
            if case['split'] != split:
                continue
            query = case.get('query')
            if case.get('query_image'):
                path = DATA / 'images' / case['query_image']
                if digest(path) != case['query_image_sha256']:
                    raise SystemExit('Query image hash mismatch')
                query = [image_part(path)]
            t = time.monotonic()
            hits = call('/search', {'user_id': USER, 'query': query, 'top_k': 100})['data']
            ranks = {key: next((i + 1 for i, hit in enumerate(hits) if hit['id'] == value), None) for key, value in ids.items()}
            groups = case.get('relevance_groups', [[key] for key in case['expected_objects']])
            required = [min((ranks[key] for key in group if ranks.get(key) is not None), default=None) for group in groups]
            rows.append({'case': case['case'], 'kind': case['kind'], 'language': case['language'],
                         'required_group_ranks': required, 'all_sources_at_1': all(r == 1 for r in required),
                         'all_sources_at_5': all(r is not None and r <= 5 for r in required),
                         'latency_ms': round((time.monotonic() - t) * 1000, 2),
                         'top_objects': [next(key for key, value in ids.items() if value == hit['id']) for hit in hits[:5]],
                         'top_result_has_image': bool(hits and isinstance(hits[0]['content'], list) and any(p.get('type') == 'image_url' for p in hits[0]['content']))})
            print(json.dumps(rows[-1]), flush=True)
        assert call('/search', {'user_id': USER + ':absent', 'query': 'a teapot', 'top_k': 100}) == {'data': []}
        single = [r for r in rows if r['kind'] != 'multi-source']
        result = {'cases': rows, 'single_source_queries': len(single),
                  'single_source_recall_at_1': statistics.mean(r['all_sources_at_1'] for r in single),
                  'all_required_sources_at_5': statistics.mean(r['all_sources_at_5'] for r in rows),
                  'single_source_mrr': statistics.mean(1 / r['required_group_ranks'][0] if r['required_group_ranks'][0] else 0 for r in single),
                  'cross_user_isolation': 'passed'}
    after = call('/status')
    result.update({'created_at': datetime.now(timezone.utc).isoformat(), 'phase': args.phase,
                   'service_version': after['version'], 'manifest_sha256': digest(MANIFEST),
                   'frozen_commit': args.frozen_commit,
                   'vision_model': after['vision_model'], 'embedding_model': after['embedding_model'],
                   'cli_attempts_during_run': after['provider_requests'].get('vision', 0) - before['provider_requests'].get('vision', 0),
                   'cli_attempts_total': after['provider_requests'].get('vision', 0),
                   'elapsed_seconds': round(time.monotonic() - started, 2), 'official_score': False,
                   'limitations': manifest['protocol']['limitations']})
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: value for key, value in result.items() if key not in ('cases', 'ingestion_timings')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
