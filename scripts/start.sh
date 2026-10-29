#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
if [ -f .env ]; then
    set -a
    . ./.env
    set +a
fi
exec python3 memory_service.py
