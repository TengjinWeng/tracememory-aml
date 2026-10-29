#!/usr/bin/env bash
set -euo pipefail
project="$(cd "$(dirname "$0")/../.." && pwd)"
runtime="$project/.venv-embedding/bin/python"
uv="${EMBEDDING_UV:-/data/wengtengjin/.local/bin/uv}"
export UV_CACHE_DIR="$project/tools/uv-cache-embedding"
export PIP_CACHE_DIR="$project/tools/pip-cache-embedding"
mkdir -p "$project/tools"
export TMPDIR="$project/tools/tmp-embedding"
mkdir -p "$TMPDIR"
if [ ! -x "$runtime" ]; then
  "$uv" venv --python /usr/bin/python3 "$project/.venv-embedding"
fi
"$runtime" "$project/experiments/local_embedding/assets.py"
"$uv" pip install --python "$runtime" --no-cache "$project/tools/embedding-wheels/torch-2.6.0+cpu-cp310-cp310-linux_x86_64.whl"
"$uv" pip install --python "$runtime" --no-cache 'transformers==4.51.3' 'huggingface-hub==0.30.2' 'numpy==1.26.4' 'safetensors==0.5.3'
"$runtime" "$project/experiments/local_embedding/download.py"
