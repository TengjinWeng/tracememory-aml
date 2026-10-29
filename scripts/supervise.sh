#!/bin/sh
# Run in a dedicated tmux session on the deployment server.
set -u
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
trap 'exit 0' INT TERM
while :; do
    sh scripts/start.sh
    status=$?
    printf 'TraceMemory exited (%s); restarting in 5 seconds\n' "$status"
    sleep 5
done
