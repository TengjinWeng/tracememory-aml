"""Collect a small, reproducible CC0 museum corpus without any model calls.

This is outside the official AML datasets. Only six public-domain photographs
are downloaded; other records are human-authored catalogue text distractors.
"""
import concurrent.futures
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'data-system/real-corpus-v1'
TERMS = ('teapot', 'violin', 'cat', 'helmet', 'chair', 'dragon', 'mandolin', 'flute')
FIELDS = ('objectID', 'objectName', 'title', 'medium', 'culture', 'objectDate',
          'classification', 'department', 'tags', 'objectURL', 'primaryImageSmall',
          'isPublicDomain')


def get_json(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'TraceMemory-research/0.3'})
    with urllib.request.urlopen(request, timeout=40) as response:
        return json.load(response)


def collect(term):
    query = urllib.parse.urlencode({'q': term, 'title': 'true', 'hasImages': 'true', 'limit': 18})
    url = 'https://collectionapi.metmuseum.org/public/collection/v1.1/search?' + query
    values = get_json(url)
    result = []
    for identity in values.get('objectIDs', []):
        path = DEST / 'metadata' / (str(identity) + '.json')
        obj = json.loads(path.read_text()) if path.exists() else get_json(
            'https://collectionapi.metmuseum.org/public/collection/v1/objects/' + str(identity))
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')
        if obj.get('isPublicDomain') and obj.get('primaryImageSmall'):
            row = {name: obj.get(name) for name in FIELDS}
            row['sampling_term'] = term
            row['metadata_url'] = 'https://collectionapi.metmuseum.org/public/collection/v1/objects/' + str(identity)
            result.append(row)
        if len(result) == 5:
            break
    if len(result) != 5:
        raise RuntimeError('Insufficient public-domain records for ' + term)
    return result


def main():
    (DEST / 'metadata').mkdir(parents=True, exist_ok=True)
    (DEST / 'images').mkdir(exist_ok=True)
    frozen = ROOT / 'docs/real-corpus-v1-manifest.json'
    if frozen.exists():
        data = json.loads(frozen.read_text())
        sources = [(r['image_file'], r['primaryImageSmall'], r['image_sha256'])
                   for r in data['records'] if r.get('image_file')]
        sources += [(r['query_image'], r['query_image_url'], r['query_image_sha256'])
                    for r in data['cases'] if r.get('query_image')]
        for name, url, expected in sources:
            path = DEST / 'images' / name
            if not path.exists():
                with urllib.request.urlopen(url, timeout=40) as response:
                    raw = response.read(6 * 1024 * 1024)
                if len(raw) >= 6 * 1024 * 1024:
                    raise RuntimeError('Image exceeds download limit')
                path.write_bytes(raw)
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise RuntimeError('Frozen image hash mismatch: ' + name)
        print(json.dumps({'frozen_images_verified': len(sources), 'model_calls': 0}))
        return
    manifest = DEST / 'catalogue.json'
    if manifest.exists():
        print('Frozen catalogue already exists; refusing to resample.')
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        groups = list(pool.map(collect, TERMS))
    records, seen = [], set()
    for group in groups:
        for row in group:
            if row['objectID'] in seen:
                raise RuntimeError('Overlapping catalogue objects; revise sampling before evaluation')
            seen.add(row['objectID'])
            records.append(row)
    result = {'created_at': datetime.now(timezone.utc).isoformat(),
              'source': 'The Metropolitan Museum of Art Open Access',
              'license': 'CC0', 'documentation': 'https://metmuseum.github.io/',
              'sampling': 'First five public-domain image-bearing objects in each of eight title searches; API order frozen in this file.',
              'records': records}
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + '\n'
    manifest.write_text(encoded)
    print(json.dumps({'objects': len(records), 'catalogue_sha256': hashlib.sha256(encoded.encode()).hexdigest()}))
    for row in records:
        print(row['sampling_term'], row['objectID'], row['title'], '|', row['objectName'])


if __name__ == '__main__':
    main()
