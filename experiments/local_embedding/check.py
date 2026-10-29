"""Small, self-authored retrieval probe. Not an AML benchmark or score."""
import json
import math
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

MODEL = "Qwen/Qwen3-Embedding-0.6B"
BASE = "http://127.0.0.1:18088"
ROOT = Path(__file__).resolve().parents[2]


def post(payload):
    request = Request(BASE + "/v1/embeddings", data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=60) as response:
        return json.load(response)


if __name__ == "__main__":
    with urlopen(BASE + "/health", timeout=5) as response:
        health = json.load(response)
    assert health["model"] == MODEL and health["development_only"]
    documents = ["I parked my car beside the library.",
                 "今天午饭吃了西红柿鸡蛋面。",
                 "The bicycle has a flat tire.",
                 "周五的会议已从上午九点调整为下午三点。"]
    queries = ["Where was the automobile left?", "周五几点开会？"]
    started = time.perf_counter()
    result_docs = post({"model": MODEL, "input": documents, "input_type": "document"})
    result_queries = post({"model": MODEL, "input": queries, "input_type": "query"})
    assert result_docs["model"] == result_queries["model"] == MODEL
    docs = [r["embedding"] for r in result_docs["data"]]
    qs = [r["embedding"] for r in result_queries["data"]]
    for vector in docs + qs:
        assert len(vector) == 1024 and all(math.isfinite(x) for x in vector)
        assert abs(sum(x*x for x in vector) - 1) < 1e-4
    rankings = [sorted(range(len(docs)), key=lambda i: sum(x*y for x,y in zip(q,docs[i])), reverse=True) for q in qs]
    assert [r[0] for r in rankings] == [0,3], rankings
    for bad in [{"model": "text-embedding-v4", "input": "test"},
                {"model": MODEL, "input": []}, {"model": MODEL, "input": "test", "dimensions": 3}]:
        try:
            post(bad)
            raise AssertionError("invalid request was accepted")
        except HTTPError as error:
            assert error.code == 400
    report = {"scope": "self-authored development probe; not an official score",
              "health": health, "cases": 2, "correct_top1": 2, "rankings": rankings,
              "elapsed_seconds": round(time.perf_counter()-started,3),
              "invalid_requests_rejected": 3, "paid_model_api_calls": 0}
    (ROOT / "docs/local-embedding-check.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(report,ensure_ascii=False))
