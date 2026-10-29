"""Loopback-only text embedding experiment; never used by the official endpoint."""
import json
import math
import os
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import torch
from transformers import AutoModel, AutoTokenizer

MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"
REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = ROOT / "models-embedding/qwen3-0.6b"
INSTRUCTION = "Given a question, retrieve relevant past conversation passages that answer the question"


class Encoder:
    def __init__(self):
        torch.set_num_threads(4)
        self.lock = threading.Lock()
        self.tokenizer = AutoTokenizer.from_pretrained(
            MODEL_DIR, local_files_only=True, trust_remote_code=False, padding_side="left")
        self.model = AutoModel.from_pretrained(
            MODEL_DIR, local_files_only=True, trust_remote_code=False,
            use_safetensors=True, torch_dtype=torch.float32, attn_implementation="eager")
        self.model.eval()

    def encode(self, texts, input_type):
        if input_type == "query":
            texts = [f"Instruct: {INSTRUCTION}\nQuery: {text}" for text in texts]
        with self.lock, torch.inference_mode():
            batch = self.tokenizer(texts, padding=True, truncation=False, return_tensors="pt")
            if batch["input_ids"].shape[1] > 512:
                raise ValueError("development service limit is 512 tokens per input")
            # With left padding, the final position is the final real token.
            hidden = self.model(**batch).last_hidden_state[:, -1]
            vectors = torch.nn.functional.normalize(hidden, p=2, dim=1).tolist()
            if any(not math.isfinite(x) for row in vectors for x in row):
                raise RuntimeError("non-finite embedding")
            return vectors, int(batch["attention_mask"].sum())


class Handler(BaseHTTPRequestHandler):
    encoder = None
    slots = threading.BoundedSemaphore(2)

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, *args):
        pass  # Never log source text, vectors, or request bodies.

    def reply(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/health":
            return self.reply(404, {"error": "unknown endpoint"})
        self.reply(200, {"status": "ok", "model": MODEL_ID, "revision": REVISION,
                         "device": "cpu", "dimensions": 1024, "development_only": True})

    def do_POST(self):
        if self.path != "/v1/embeddings":
            return self.reply(404, {"error": "unknown endpoint"})
        if not self.slots.acquire(blocking=False):
            return self.reply(503, {"error": "development service busy"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 65536:
                raise ValueError("request body must be between 1 and 65536 bytes")
            request = json.loads(self.rfile.read(size))
            if not isinstance(request, dict) or request.get("model") != MODEL_ID:
                raise ValueError("model must be " + MODEL_ID)
            texts = request.get("input")
            if isinstance(texts, str):
                texts = [texts]
            if not isinstance(texts, list) or not 1 <= len(texts) <= 8 or any(
                    not isinstance(t, str) or not t.strip() or len(t) > 2000 for t in texts):
                raise ValueError("input must contain 1 to 8 nonempty texts, each at most 2000 characters")
            input_type = request.get("input_type", "document")
            if input_type not in ("query", "document"):
                raise ValueError("input_type must be query or document")
            if request.get("dimensions", 1024) != 1024 or request.get("encoding_format", "float") != "float":
                raise ValueError("this experiment returns 1024-dimensional float vectors")
            vectors, tokens = self.encoder.encode(texts, input_type)
            self.reply(200, {"object": "list", "model": MODEL_ID,
                             "data": [{"object": "embedding", "index": i, "embedding": row}
                                      for i, row in enumerate(vectors)],
                             "usage": {"prompt_tokens": tokens, "total_tokens": tokens}})
        except (ValueError, UnicodeError):
            self.reply(400, {"error": "invalid embedding request or input exceeds the development token limit"})
        except Exception:
            self.reply(500, {"error": "local inference failed"})
        finally:
            self.slots.release()


if __name__ == "__main__":
    Handler.encoder = Encoder()
    port = int(os.environ.get("LOCAL_EMBEDDING_PORT", "18088"))
    print(json.dumps({"event": "ready", "port": port, "model": MODEL_ID, "device": "cpu"}), flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
