# TraceMemory

Multimodal memory API protocol baseline for the AML challenge. Maintainer: wengtengjin.

Status: integration access requested; organizer review pending. **No official Smoke or Full result yet.**

## Run

Python 3.9+; no third-party packages, model keys or GPU required for this baseline.

```sh
python3 scripts/init_env.py
sh scripts/start.sh
```

The private `.env` is created once with permissions 0600. The default bind is
`127.0.0.1:18086`; put an authenticated HTTPS ingress in front of it for remote use.
For containers, persist `/app/data` and pass `MEMORY_API_KEY` as an environment secret.

```sh
python3 -m unittest discover -s tests -v
set -a
. ./.env
set +a
python3 scripts/check_endpoints.py http://127.0.0.1:18086
```

`check_endpoints.py` writes uniquely namespaced **synthetic** records. It does not
start an AML job, use official benchmark records, consume an evaluation quota,
generate a final answer, or claim a leaderboard score.

## Method

Add validates an ordered string or text/image content array, stores original
evidence in SQLite, and commits before acknowledgement. IDs are deterministic.
Retries are idempotent within a user; a reused request ID with changed contents
returns 409. All storage and retrieval use the exact `user_id` boundary.

Search performs user-local BM25 over English words and Chinese characters/bigrams.
An exact decoded-image SHA-256 match gives an additional retrieval signal.
Original image bytes, content-part order and source timestamps are retained.
Search returns ranked evidence, never answers or selected choice options.
Up to 100 records are returned within the 30 MiB decoded-media response budget.

This baseline **does not understand unseen images**, recognize objects, perform
OCR, compute semantic embeddings or multi-hop reasoning. It can carry images
through the protocol and retrieve them using accompanying text or exact-image
matches. It must not be presented as a competitive multimodal algorithm.
Search currently scans lightweight metadata for a single user; full-suite
scale and sustained concurrency have not been certified.

## Synthetic diagnostics

`python3 scripts/benchmark_baseline.py --output docs/baseline-benchmark.json`
runs isolated synthetic retrieval cases and loopback HTTP capacity checks. It
does not touch the deployed database, call model APIs, or consume AML quotas.
The current seven-case suite exposes failures in semantic paraphrases,
cross-session evidence expansion, latest-state ranking and re-encoded images.
It is a diagnostic fixture, not a statistically representative benchmark.

See [diagnostic report](docs/基线诊断与待办.md) for measured latency,
bandwidth estimates, limitations and unfinished algorithm work.

`scripts/aml_smoke.py` prepares a guarded Multimodal-only Smoke client based on
the observed official UI contract. Its default operation is read-only; it
requires a verified academic AML key and a matching admitted version before
starting any Smoke. It has no Full submission option. This client has not yet
been exercised against authenticated official APIs and must be checked against
the live contract once access is approved.

## API

An isolated [v0.2 candidate](candidate/README.md) adds decoded-pixel identity,
entity expansion, recency preferences and optional specified-model fusion. It
does not replace the reviewed service. See its README for test scope and the
clear distinction between offline improvements and unverified real-model quality.

- `GET /health`: liveness and version, no credential required.
- `POST /add`: `request_id`, `user_id`, `session_id`, ordered `messages`.
- `POST /search`: `user_id`, `query`, `top_k`; optional top-level `options`.
- Authentication: `Authorization: Bearer <MEMORY_API_KEY>`.
- JPEG/PNG/WebP inline Base64 images only, 10 MiB each and 30 MiB per Add.
- Maximum HTTP request body: 48 MiB; socket timeout: 60 seconds.
- Maximum concurrently processed requests: 8; overflow returns 429.
- Proposed initial platform concurrency: Add 1 / Search 1, pending capacity checks.
- SQLite durable disk and WAL; no request/evidence/credential logging.

## Rules and next gates

The official documentation retrieved on 2026-10-29 says Open-source Methods
embeddings must use `text-embedding-v4`, LLM components must use `gpt-4o-mini`,
and rerankers are unrestricted. This baseline calls **no models**. Acceptance of
a no-model protocol baseline has not been confirmed by AML; do not declare that
it has met all formal submission conditions. If model components are added,
use the required models and disclose providers and exact configuration.

Before an official Smoke: supply a public GitHub repository, fixed revision,
reachable HTTPS Add/Search URLs, contact details, capacity notes and a reviewed
AML evaluation key. Full evaluation must wait until Smoke succeeds and deployment,
capacity, rules, and frozen-version requirements are all checked.

Official references:

- https://agentmemoryleaderboard.ai/api-guide
- https://agentmemoryleaderboard.ai/rules
- https://agentmemoryleaderboard.ai/evaluation
- https://github.com/AML-memory/agent-memory-leaderboard

All implementation here was newly authored with AI assistance for this project.
BM25 is a standard ranking algorithm; no third-party memory implementation was
copied. Model/API/hosting charges are not included. No private evaluation data
or secrets belong in the public repository. AML evaluation data and derived
copies must be removed within 30 days after the task unless AML authorizes longer
retention. The owner must arrange that deletion before official evaluation.
