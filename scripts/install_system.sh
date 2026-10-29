#!/usr/bin/env bash
# Run inside the authorized server project; never prints credentials or resets call counters.
set -euo pipefail
project="$(cd "$(dirname "$0")/.." && pwd)"
uv="${SYSTEM_UV:-/data/wengtengjin/.local/bin/uv}"
mkdir -p "$project/tools/tmp-system"
export TMPDIR="$project/tools/tmp-system"
if [ ! -x "$project/.venv-candidate/bin/python" ]; then
  "$uv" venv --python /usr/bin/python3 "$project/.venv-candidate"
fi
"$uv" pip install --python "$project/.venv-candidate/bin/python" --cache-dir "$project/tools/system-uv-cache" -r "$project/system_service/requirements.txt"
EMBEDDING_UV="$uv" bash "$project/experiments/local_embedding/install.sh"
cd "$project"
exec .venv-candidate/bin/python -m system_service.manage up
