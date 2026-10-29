# v0.2 isolated candidate

Not the live reviewed 0.1.0-protocol service. The live service remains on 18086.
This candidate uses its own DB/cache under `data-candidate/` and port 18087.

## Implemented

- Decode/EXIF-normalize pixels for encoding-independent image identity; preserves original source images.
- BM25 plus optional specified-model chunk embeddings, combined using reciprocal rank fusion.
- Expand rare source entities from first-hop results to retrieve second-hop evidence.
- For current/latest questions only, use source timestamp as an additional ranking preference.
- Cap returned images at four and 2 MiB decoded by default; source DB is not modified.
  Identical pixels are not repeated. Some images may be omitted; this needs evaluation on real tasks.
- With model calls enabled, `gpt-4o-mini` extracts image/text retrieval facts and OCR;
  `text-embedding-v4` encodes 1200-character chunks at 1024 dimensions.
- Known image-only pixel matches avoid a fresh vision/embedding call.
- Per-user provider cache, durable call counters, no automatic retries or model substitution.
- Failed indexing is not acknowledged or exposed to candidate Search. A retry can finish pending raw data.
- Model extracts only affect indexing; Search returns source evidence, not generated final answers.

## Validation scope

The seven existing synthetic diagnostics improve from 3/7 to 5/7 Recall@1 and
4/7 to 6/7 Recall@5 in offline/no-model mode. The remaining semantic-paraphrase
case still fails without real embeddings. This is a tiny known diagnostic suite,
not a held-out evaluation, statistical quality estimate, or official result.
See `docs/candidate-comparison.json` and `tests_candidate/`.

Provider tests use controlled synthetic responses to validate batching, schema,
fusion, scope isolation and cost guards. **No real model API has been called.**
Real VLM/OCR/embedding quality, billing, concurrency and Full capacity remain unverified.

## Python 3.10 setup on the server

```sh
python3 -m venv .venv-candidate
.venv-candidate/bin/python -m pip install -r candidate/requirements.txt
.venv-candidate/bin/python -m unittest discover -s tests_candidate -v
.venv-candidate/bin/python scripts/compare_candidate.py --output docs/candidate-comparison.json
```

Private model configuration: `secrets/model-apis.env` (not tracked). Fill both
provider keys and their actual Base URLs. Check without billing:

```sh
.venv-candidate/bin/python scripts/check_model_apis.py
```

Only explicit `--call` contacts the providers, once per provider, using synthetic
data. Model enablement in the candidate service is separate and defaults off.
The development request counters default to LLM 20 / embedding 100 and persist
across process restarts. These are request-count caps, not monetary billing caps.
Do not reset them or raise limits without reviewing the agreed development budget.

Generate a separate service key once, then start an internal candidate:

```sh
.venv-candidate/bin/python candidate/init_service_env.py
sh candidate/start.sh
```

To enable actual models after small-batch verification and budget authorization,
set `CANDIDATE_ENABLE_MODELS=1` in the private candidate-service.env and restart
only the candidate. Do not replace the public endpoint or alter the reviewed
version until the candidate is evaluated and registered/bound correctly.

## Remaining limitations

Proper-name extraction without an LLM is English-oriented and heuristic; timestamps
do not fully resolve conflicting facts. The dense route scans per-user vectors;
large histories require a proper vector index. Single-process ingestion is serialized.
The API cache is scoped by user but source/query contents are retained privately;
apply the same official retention/deletion requirements as the evaluation memory.
Dependency/API failures return 503 without printing provider response bodies.
