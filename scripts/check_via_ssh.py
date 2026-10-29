"""Run the public synthetic check using an in-memory SSH-fetched service key."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("base_url")
parser.add_argument("--host", default="gmlab_out_106")
args = parser.parse_args()
# Capture into process memory only. Neither credentials nor SSH stdout is printed.
result = subprocess.run([
    "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=12", args.host,
    "cd /data/wengtengjin/tracememory-aml && set -a && . ./.env && set +a && python3 -c 'import os; print(os.environ[\"MEMORY_API_KEY\"])'",
], capture_output=True, text=True, timeout=20)
if result.returncode:
    raise SystemExit("SSH could not retrieve the task-specific service credential")
key = result.stdout.strip()
if len(key) < 24 or "\n" in key:
    raise SystemExit("Unexpected SSH credential response; no value disclosed")
environment = dict(os.environ, MEMORY_API_KEY=key)
script = Path(__file__).with_name("check_endpoints.py")
raise SystemExit(subprocess.call([sys.executable, str(script), args.base_url], env=environment))
