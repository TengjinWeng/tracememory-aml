#!/bin/sh
set -u
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
trap 'exit 0' INT TERM
while :; do
    # Gateway preserves the frozen root APIs and routes the explicit new version prefix.
    tools/ngrok http http://127.0.0.1:18090 --config secrets/ngrok-config.json --inspect=false >> ngrok.log 2>&1
    sleep 10
done
