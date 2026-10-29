"""Exercise the candidate with local text vectors on disposable, self-authored data."""
import json
from pathlib import Path
import sys
import tempfile
import threading
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from candidate.providers import unit
from candidate.store import CandidateStore

MODEL = "Qwen/Qwen3-Embedding-0.6B"


class LocalTextVectors:
    """Development-only provider: no LLM, no vision, no model ID substitution."""
    def __init__(self):
        self.context = threading.local()
        self.calls = 0

    def describe(self, user_id, content):
        return {}  # Explicitly no visual or LLM interpretation in this experiment.

    def embed(self, user_id, texts):
        if not user_id or self.calls >= 12:
            raise RuntimeError("local probe scope or call bound exceeded")
        self.calls += 1
        payload = {"model": MODEL, "input": texts,
                   "input_type": getattr(self.context, "input_type", "document")}
        request = Request("http://127.0.0.1:18088/v1/embeddings", data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=60) as response:
            data = json.load(response)
        rows = data["data"]
        if data["model"] != MODEL or [r["index"] for r in rows] != list(range(len(texts))):
            raise RuntimeError("local model response mismatch")
        vectors = [unit(row["embedding"]) for row in rows]
        if any(len(v) != 1024 for v in vectors):
            raise RuntimeError("local embedding dimension mismatch")
        return vectors


class DevelopmentStore(CandidateStore):
    def search(self, payload):
        prior = getattr(self.provider.context, "input_type", "document")
        self.provider.context.input_type = "query"
        try:
            return super().search(payload)
        finally:
            self.provider.context.input_type = prior


if __name__ == "__main__":
    temporary_root = ROOT / "tools/tmp-embedding"
    temporary_root.mkdir(parents=True, exist_ok=True)
    provider = LocalTextVectors()
    with tempfile.TemporaryDirectory(dir=temporary_root) as folder:
        store = DevelopmentStore(Path(folder)/"memory.db", provider=provider)
        texts = ["I parked my car beside the library.", "今天午饭吃了西红柿鸡蛋面。",
                 "The bicycle has a flat tire.", "周五的会议已从上午九点调整为下午三点。"]
        store.add({"request_id": "self-authored:local-embedding", "user_id": "development:alice",
                   "session_id": "development:session", "messages": [{"role": "user", "content": t} for t in texts]})
        checks = []
        for query, expected in [("Where was the automobile left?", texts[0]), ("周五几点开会？", texts[3])]:
            result = store.search({"user_id": "development:alice", "query": query, "top_k": 5})
            assert result["data"][0]["content"] == expected, result
            checks.append({"query": query, "expected_source_rank": 1})
        assert store.search({"user_id": "development:bob", "query": "Where was the automobile left?", "top_k": 5}) == {"data": []}
    report = {"scope": "two known self-authored diagnostics, not an AML score or generalization test",
              "model": MODEL, "vision_enabled": False, "checks": checks,
              "cross_user_isolation": "passed", "local_embedding_requests": provider.calls,
              "paid_model_api_calls": 0, "temporary_database_removed": True}
    (ROOT/"docs/local-embedding-memory-probe.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(report,ensure_ascii=False))
