"""Recheck known diagnostics after the modality change using existing caches only."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import sys
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from candidate.config import read_config
from memory_service import canonical
from system_service import benchmark_multimodal, benchmark_relations


def main():
    config = read_config(ROOT / 'secrets/system-service.env')
    if config['SYSTEM_MODE'] != 'development':
        raise SystemExit('Development mode required')
    def call(path, payload=None):
        request = Request('http://127.0.0.1:' + config['SYSTEM_PORT'] + path,
                          data=None if payload is None else canonical(payload).encode(),
                          headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + config['SYSTEM_API_KEY']})
        with urlopen(request, timeout=180) as response:
            return json.load(response)
    before = call('/status')
    if before['development_cli_attempts_remaining'] != 0:
        raise SystemExit('Run only with exhausted persistent cap, so a cache miss cannot launch the CLI')
    reports = []
    for module, prefix in [(benchmark_multimodal, 'multimodal-regression-v1:'),
                           (benchmark_relations, 'state-diagnostic-v2:')]:
        records, cases = module.fixture()
        ids = {name: 'mem_' + hashlib.sha256(canonical([module.USER, prefix + name, 0]).encode()).hexdigest() for name in records}
        rows = []
        for name, query, targets in cases:
            hits = call('/search', {'user_id': module.USER, 'query': query, 'top_k': 100})['data']
            ranks = [next((i + 1 for i, hit in enumerate(hits) if hit['id'] == ids[target]), None) for target in targets]
            rows.append({'case': name, 'ranks': ranks, 'all_at_5': all(r is not None and r <= 5 for r in ranks)})
        single = [r for r in rows if len(r['ranks']) == 1]
        reports.append({'corpus': prefix[:-1], 'cases': rows,
                        'single_source_recall_at_1': statistics.mean(r['ranks'] == [1] for r in single),
                        'all_required_sources_at_5': statistics.mean(r['all_at_5'] for r in rows)})
    after = call('/status')
    used = after['provider_requests']['vision'] - before['provider_requests']['vision']
    assert used == 0
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'version': after['version'],
              'reports': reports, 'cli_attempts': used,
              'meaning': 'Known self-authored regressions only; no independent or official score claim.'}
    (ROOT / 'docs/regression-033.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
