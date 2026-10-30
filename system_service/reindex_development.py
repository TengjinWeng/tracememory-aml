"""Explicit offline rebuild of self-authored development features; never changes raw records."""
import argparse
import json
from pathlib import Path
import sys
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from candidate.config import read_config
from system_service.providers import DevelopmentModels
from system_service.ranking import AlgorithmStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    config = read_config(ROOT/'secrets/system-service.env')
    if config['SYSTEM_MODE'] != 'development':
        raise SystemExit('Only self-authored development data can be rebuilt')
    if not args.apply:
        print('Stop the development API, then use --apply to rebuild existing indexed development rows.')
        return
    try:
        with urlopen('http://127.0.0.1:'+config['SYSTEM_PORT']+'/health', timeout=2):
            raise SystemExit('Stop the development API before reindexing')
    except URLError:
        pass
    directory = ROOT/'data-system/development'
    provider = DevelopmentModels(directory, cli_limit=int(config.get('MAX_CLI_REQUESTS', '20')),
                                 cli_model=config['CLI_MODEL'])
    before = provider.cache.usage()
    store = AlgorithmStore(directory/'memory.sqlite3', provider, provider.identity)
    count = store.rebuild_development_features()
    after = provider.cache.usage()
    result = {'reindexed_development_records': count,
              'cli_attempts_during_reindex': after.get('vision', 0)-before.get('vision', 0),
              'raw_sources_and_request_hashes': 'preserved', 'official_data_accessed': False}
    path = ROOT/'docs/development-reindex.private.json'
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n');path.chmod(0o600)
    print(json.dumps(result))


if __name__ == '__main__': main()
