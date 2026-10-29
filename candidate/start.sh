#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
set -a
. ./secrets/candidate-service.env
set +a
exec .venv-candidate/bin/python -m candidate.server
