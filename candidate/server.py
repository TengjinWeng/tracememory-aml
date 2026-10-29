"""Separate candidate process/DB/port; never replaces the reviewed live service."""
import json
import os
from pathlib import Path
import threading
from http.server import ThreadingHTTPServer

from memory_service import ContractError, MemoryHandler
from candidate.config import read_config
from candidate.providers import ProviderError, SpecifiedModels
from candidate.store import CandidateStore


class Handler(MemoryHandler):
    def do_GET(self):
        if self.path != "/health":
            return super().do_GET()
        self.respond(200, {"status": "ok", "version": "0.2.0-candidate",
                           "mode": "specified-models" if self.server.store.provider else "offline-lexical-pixel-graph"})


class ServingStore:
    def __init__(self, store):
        self.store = store

    def __getattr__(self, name):
        return getattr(self.store, name)

    def add(self, payload):
        try:
            return self.store.add(payload)
        except ProviderError:
            raise ContractError("candidate model dependency unavailable; check private configuration/request cap", 503) from None

    def search(self, payload):
        try:
            return self.store.search(payload)
        except ProviderError:
            raise ContractError("candidate model dependency unavailable; check private configuration/request cap", 503) from None


def main():
    root = Path(__file__).resolve().parents[1]
    key = os.environ.get("CANDIDATE_API_KEY", "")
    if len(key) < 24:
        raise SystemExit("Set CANDIDATE_API_KEY to a private token of at least 24 characters")
    config = read_config(root / "secrets/model-apis.env")
    provider = None
    if os.environ.get("CANDIDATE_ENABLE_MODELS") == "1":
        if not all(config.get(k) for k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL")):
            raise SystemExit("Model keys/URLs are missing; no model request attempted")
        provider = SpecifiedModels(config, root / "data-candidate/provider-cache.sqlite3")
    store = CandidateStore(root / "data-candidate/memory.sqlite3", provider=provider)
    server = ThreadingHTTPServer(("127.0.0.1", 18087), Handler)
    server.store, server.key, server.slots = ServingStore(store), key, threading.BoundedSemaphore(4)
    print("Candidate listening on 127.0.0.1:18087; reviewed 18086 is separate", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
