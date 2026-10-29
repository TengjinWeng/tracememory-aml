"""TraceMemory protocol baseline. Python standard library only; no model calls."""
import base64
import collections
from contextlib import contextmanager
import hashlib
import hmac
import json
import math
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = "0.1.0-protocol"
IMAGE_LIMIT = 10 * 1024 * 1024
MEDIA_LIMIT = 30 * 1024 * 1024
BODY_LIMIT = 48 * 1024 * 1024


class ContractError(Exception):
    def __init__(self, reason, status=422):
        self.reason, self.status = reason, status


def required_string(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ContractError(name + " must be a non-empty string (max 4096 chars)")
    return value


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def validate_content(content):
    """Preserve every source part; validate data URIs without remote requests."""
    if isinstance(content, str) and content.strip():
        return content, [], 0
    if not isinstance(content, list) or not content:
        raise ContractError("content must be a non-empty string or ordered ContentPart[]")
    texts, images, size = [], [], 0
    for part in content:
        if not isinstance(part, dict):
            raise ContractError("invalid content part")
        if part.get("type") == "text":
            value = part.get("text")
            if not isinstance(value, str) or not value.strip():
                raise ContractError("text part must be non-empty")
            texts.append(value)
        elif part.get("type") == "image_url":
            image = part.get("image_url")
            url = image.get("url") if isinstance(image, dict) else None
            match = re.fullmatch(r"data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/=]+)", url or "")
            if not match:
                raise ContractError("images must be inline JPEG/PNG/WebP Base64 data URIs")
            if len(match[2]) > ((IMAGE_LIMIT + 2) // 3) * 4:
                raise ContractError("image exceeds 10 MiB", 413)
            try:
                binary = base64.b64decode(match[2], validate=True)
            except ValueError:
                raise ContractError("invalid Base64 image")
            if not binary or len(binary) > IMAGE_LIMIT:
                raise ContractError("invalid image size", 413)
            kind = match[1]
            valid = (kind == "png" and binary.startswith(b"\x89PNG\r\n\x1a\n")) or (
                kind == "jpeg" and binary.startswith(b"\xff\xd8\xff")) or (
                kind == "webp" and binary.startswith(b"RIFF") and binary[8:12] == b"WEBP")
            if not valid:
                raise ContractError("image signature does not match media type")
            size += len(binary)
            images.append(hashlib.sha256(binary).hexdigest())
        else:
            raise ContractError("content part type must be text or image_url")
    if size > MEDIA_LIMIT:
        raise ContractError("media exceeds 30 MiB", 413)
    return "\n".join(texts), images, size


def tokenize(text):
    tokens = re.findall(r"[a-z0-9_]+", text.casefold())
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        tokens.extend(run)
        tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return collections.Counter(tokens)


class MemoryStore:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS requests (
                user_id TEXT NOT NULL, request_id TEXT NOT NULL, payload_hash TEXT NOT NULL,
                PRIMARY KEY(user_id, request_id));
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT NOT NULL,
                request_id TEXT NOT NULL, position INTEGER NOT NULL, role TEXT NOT NULL,
                content TEXT NOT NULL, terms TEXT NOT NULL, image_hashes TEXT NOT NULL,
                media_bytes INTEGER NOT NULL, created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS memory_scope ON memories(user_id);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, payload):
        user = required_string(payload.get("user_id"), "user_id")
        request = required_string(payload.get("request_id"), "request_id")
        session = required_string(payload.get("session_id"), "session_id")
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ContractError("messages must be a non-empty ordered array")
        rows, total = [], 0
        for position, message in enumerate(messages):
            if not isinstance(message, dict) or message.get("role") not in ("user", "assistant"):
                raise ContractError("message role must be user or assistant")
            text, images, size = validate_content(message.get("content"))
            total += size
            if total > MEDIA_LIMIT:
                raise ContractError("Add images exceed 30 MiB", 413)
            created = datetime.now(timezone.utc).isoformat()
            if "timestamp" in message:
                stamp = message["timestamp"]
                if type(stamp) not in (int, float) or not math.isfinite(stamp):
                    raise ContractError("timestamp must be finite Unix milliseconds")
                try:
                    created = datetime.fromtimestamp(stamp / 1000, timezone.utc).isoformat()
                except (ValueError, OverflowError, OSError):
                    raise ContractError("timestamp out of range")
            identity = "mem_" + digest([user, request, position])
            rows.append((identity, user, session, request, position, message["role"],
                         canonical(message["content"]), canonical(tokenize(text)),
                         canonical(images), size, created))
        payload_hash = digest(payload)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT payload_hash FROM requests WHERE user_id=? AND request_id=?",
                                  (user, request)).fetchone()
            if previous and previous[0] != payload_hash:
                raise ContractError("request_id already used with a different payload", 409)
            if not previous:
                db.executemany("INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
                db.execute("INSERT INTO requests VALUES (?,?,?)", (user, request, payload_hash))
        return dict(success=True, request_id=request, user_id=user, session_id=session)

    def search(self, payload):
        user = required_string(payload.get("user_id"), "user_id")
        k = payload.get("top_k")
        if type(k) is not int or not 0 <= k <= 100:
            raise ContractError("top_k must be an integer between 0 and 100")
        text, query_images, _ = validate_content(payload.get("query"))
        if "options" in payload and (not isinstance(payload["options"], list) or
                                    not all(isinstance(x, str) for x in payload["options"])):
            raise ContractError("options must be an array of strings")
        # Only the requested user's documents contribute to scores or corpus statistics.
        with self.connect() as db:
            documents = db.execute("SELECT id, session_id, terms, image_hashes FROM memories WHERE user_id=?",
                                   (user,)).fetchall()
            terms = [json.loads(row[2]) for row in documents]
            n = len(documents)
            if not n or not k:
                return {"data": []}
            query = tokenize(text)
            average = sum(sum(t.values()) for t in terms) / n or 1
            frequencies = {q: sum(q in t for t in terms) for q in query}
            scores = []
            for row, counts in zip(documents, terms):
                length = sum(counts.values())
                score = 0.0
                for term in query:
                    tf = counts.get(term, 0)
                    if tf:
                        df = frequencies[term]
                        idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                        score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / average))
                score += 20.0 * len(set(query_images) & set(json.loads(row[3])))
                scores.append((score, row[0]))
            # Preserve evidence even for image-only queries with no exact image match.
            # This baseline cannot understand unseen images; no fabricated captions.
            scores.sort(key=lambda item: (-item[0], item[1]))
            result, used = [], 0
            for score, identity in scores:
                content, size, created = db.execute(
                    "SELECT content, media_bytes, created_at FROM memories WHERE user_id=? AND id=?",
                    (user, identity)).fetchone()
                if used + size > MEDIA_LIMIT:
                    continue
                result.append(dict(id=identity, content=json.loads(content), score=score, created_at=created))
                used += size
                if len(result) >= k:
                    break
        return {"data": result}


class MemoryServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, store, key):
        self.store, self.key = store, key
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(address, MemoryHandler)


class MemoryHandler(BaseHTTPRequestHandler):
    server_version = "TraceMemory/" + VERSION

    def setup(self):
        super().setup()
        self.connection.settimeout(60)

    def log_message(self, fmt, *args):
        # Do not log query parameters, headers, bodies, credentials, or evidence.
        pass

    def respond(self, status, value):
        body = canonical(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.respond(200, {"status": "ok", "version": VERSION, "mode": "lexical-protocol-baseline"})
        else:
            self.respond(404, {"detail": {"reason": "not_found"}})

    def do_POST(self):
        acquired = False
        try:
            if self.path not in ("/add", "/search"):
                raise ContractError("not_found", 404)
            auth = self.headers.get("Authorization", "")
            if not hmac.compare_digest(auth, "Bearer " + self.server.key):
                raise ContractError("invalid authentication", 401)
            acquired = self.server.slots.acquire(blocking=False)
            if not acquired:
                raise ContractError("capacity exceeded; retry later", 429)
            if self.headers.get("Transfer-Encoding"):
                raise ContractError("chunked request encoding is unsupported", 400)
            if self.headers.get_content_type() != "application/json":
                raise ContractError("Content-Type must be application/json", 415)
            length = self.headers.get("Content-Length", "")
            if not length.isdecimal():
                raise ContractError("Content-Length required", 411)
            length = int(length)
            if not 0 < length <= BODY_LIMIT:
                raise ContractError("request exceeds 48 MiB or is empty", 413)
            body = self.rfile.read(length)
            if len(body) != length:
                raise ContractError("incomplete request body", 400)
            try:
                payload = json.loads(body, parse_constant=lambda _: None)
            except (ValueError, UnicodeDecodeError):
                raise ContractError("invalid JSON", 400)
            if not isinstance(payload, dict):
                raise ContractError("JSON object required")
            operation = self.server.store.add if self.path == "/add" else self.server.store.search
            self.respond(200, operation(payload))
        except ContractError as error:
            self.respond(error.status, {"detail": {"reason": error.reason}})
        except sqlite3.OperationalError:
            self.respond(503, {"detail": {"reason": "storage temporarily unavailable"}})
        except (TimeoutError, ConnectionError):
            self.close_connection = True
        except Exception:
            self.respond(500, {"detail": {"reason": "internal service error"}})
        finally:
            if acquired:
                self.server.slots.release()


def main():
    key = os.environ.get("MEMORY_API_KEY", "")
    if len(key) < 24:
        raise SystemExit("Set MEMORY_API_KEY to a private token with at least 24 characters")
    database = os.environ.get("MEMORY_DB", "data/memory.sqlite3")
    store = MemoryStore(database)
    host, port = os.environ.get("MEMORY_HOST", "127.0.0.1"), int(os.environ.get("MEMORY_PORT", "18086"))
    server = MemoryServer((host, port), store, key)
    print("TraceMemory %s listening on %s:%s" % (VERSION, host, port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
