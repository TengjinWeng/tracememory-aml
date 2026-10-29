#!/bin/sh
set -u
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
trap 'exit 0' INT TERM
while :; do
    tools/ngrok http http://127.0.0.1:18086 --config secrets/ngrok-config.json --inspect=false >> ngrok.log 2>&1
    sleep 10
done
