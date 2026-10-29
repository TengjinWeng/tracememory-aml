"""Synthetic localhost smoke for the separate candidate; no official evaluations."""
import json
from pathlib import Path
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from candidate.config import read_config
from scripts.benchmark_baseline import image

root = Path(__file__).resolve().parents[1]
config = read_config(root / "secrets/candidate-service.env")
key = config["CANDIDATE_API_KEY"]
base = "http://127.0.0.1:18087"


def call(path, payload=None):
    request = Request(base + path, data=None if payload is None else json.dumps(payload).encode(), headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + key})
    with urlopen(request, timeout=20) as response:
        return json.load(response)


for attempt in range(20):
    try:
        health = call("/health")
        break
    except URLError:
        time.sleep(.25)
else:
    raise SystemExit("Candidate health did not become ready; no Add/Search attempted")
assert health["version"] == "0.2.0-candidate"
assert health["mode"] == "offline-lexical-pixel-graph", "This checker must not accidentally trigger model billing"
user = "synthetic:candidate-service:" + uuid4().hex
original = [{"type": "text", "text": "A synthetic green image."}, image((10, 150, 20), 6)]
call("/add", {"user_id": user, "request_id": "write", "session_id": "sample",
              "messages": [{"role": "user", "content": original}]})
result = call("/search", {"user_id": user, "query": [image((10, 150, 20), 0)], "top_k": 100})
assert result["data"][0]["content"] == original
assert call("/search", {"user_id": user + ":other", "query": "green", "top_k": 100}) == {"data": []}
print("PASS: separate candidate HTTP health, re-encoded-image retrieval, original evidence and user isolation; model calls disabled")
