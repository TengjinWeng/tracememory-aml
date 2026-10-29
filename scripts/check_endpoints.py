"""Synthetic endpoint check. This is NOT an AML official Smoke/Full evaluation."""
import argparse
import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from test_flow import PNG


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url", help="service root, e.g. http://127.0.0.1:18086")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    key = os.environ["MEMORY_API_KEY"]

    def call(path, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
        with urlopen(Request(base + path, data=body, headers=headers), timeout=60) as response:
            assert response.status == 200
            return json.load(response)

    user = "synthetic:tracememory:" + uuid4().hex
    parts = [{"type": "text", "text": "Synthetic: Alex rode a blue bicycle beside a lake."},
             {"type": "image_url", "image_url": {"url": PNG}}]
    payload = {"request_id": user + ":write-0", "user_id": user, "session_id": user + ":session-0",
               "messages": [{"role": "user", "content": parts}]}
    assert call("/health")["status"] == "ok"
    assert call("/add", payload)["success"] is True
    assert call("/add", payload)["request_id"] == payload["request_id"]
    result = call("/search", {"user_id": user, "query": "blue bicycle", "top_k": 100})
    assert len(result["data"]) == 1
    assert result["data"][0]["content"] == parts
    assert call("/search", {"user_id": user + ":other", "query": "blue bicycle", "top_k": 100}) == {"data": []}
    print("PASS: health, authenticated Add, retry, Search, original image round-trip, user isolation")
    print("Synthetic check only. Official AML Smoke / Answer / Eval not yet run.")


if __name__ == "__main__":
    main()
