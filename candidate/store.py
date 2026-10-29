"""Candidate retrieval: pixel identity + lexical/dense fusion + entity expansion + recency."""
import base64
import collections
from datetime import datetime
import json
import math
import re
import threading

from memory_service import ContractError, MemoryStore, canonical, required_string, tokenize, validate_content
from candidate.vision import pixel_fingerprints

STOP = set("a an the i we you he she it who what where when which how did does do is are was were to from for of in on at with and or now currently latest".split())


def entities(text):
    names = re.findall(r"\b[A-Z][A-Za-z0-9_-]{1,40}\b", text)
    return sorted({name.casefold() for name in names if name.casefold() not in STOP | {"synthetic", "source"}})


def cosine(a, b):
    if len(a) != len(b):
        raise ValueError("embedding dimensions differ")
    return math.fsum(x * y for x, y in zip(a, b))


def rrf(scores, ordered, weight=1.0):
    for rank, identity in enumerate(ordered, 1):
        scores[identity] += weight / (60 + rank)


class CandidateStore(MemoryStore):
    def __init__(self, path, provider=None, max_images=4, max_image_bytes=2 * 1024 * 1024):
        super().__init__(path)
        self.provider, self.max_images, self.max_image_bytes = provider, max_images, max_image_bytes
        self.ingest_lock = threading.RLock()
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS candidate_features (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, terms TEXT NOT NULL, entities TEXT NOT NULL, pixels TEXT NOT NULL, vectors TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS candidate_scope ON candidate_features(user_id)")

    def add(self, payload):
        with self.ingest_lock:
            # Base validation/atomic raw persistence occurs before any paid operation.
            # No successful acknowledgement is returned until every index entry commits.
            result = super().add(payload)
            with self.connect() as db:
                rows = db.execute("SELECT m.id,m.content FROM memories m LEFT JOIN candidate_features f ON m.id=f.id AND m.user_id=f.user_id WHERE m.user_id=? AND m.request_id=? AND f.id IS NULL ORDER BY m.position",
                                  (payload["user_id"], payload["request_id"])).fetchall()
            prepared = []
            for identity, encoded in rows:
                content = json.loads(encoded)
                text, _, _ = validate_content(content)
                try:
                    pixels = pixel_fingerprints(content)
                except Exception as error:
                    raise ContractError("candidate could not decode image", 422) from error
                description = self.provider.describe(payload["user_id"], content) if self.provider else {}
                derived = "\n".join([text, description.get("caption", ""), description.get("ocr", "")] + description.get("facts", []))[:16000]
                names = set(entities(derived)) | {x.casefold() for x in description.get("entities", []) if x.strip()}
                chunks = [derived[i:i + 1200] for i in range(0, len(derived), 1200) if derived[i:i + 1200].strip()]
                vectors = self.provider.embed(payload["user_id"], chunks) if self.provider and chunks else []
                prepared.append((identity, payload["user_id"], canonical(tokenize(derived)), canonical(sorted(names)), canonical(pixels), canonical(vectors)))
            with self.connect() as db:
                db.executemany("INSERT INTO candidate_features VALUES (?,?,?,?,?,?)", prepared)
            return result

    def search(self, payload):
        user = required_string(payload.get("user_id"), "user_id")
        k = payload.get("top_k")
        if type(k) is not int or not 0 <= k <= 100:
            raise ContractError("top_k must be an integer between 0 and 100")
        text, _, _ = validate_content(payload.get("query"))
        if "options" in payload and (not isinstance(payload["options"], list) or not all(isinstance(x, str) for x in payload["options"])):
            raise ContractError("options must be an array of strings")
        if not k:
            return {"data": []}
        query_pixels = set(pixel_fingerprints(payload["query"]))
        with self.connect() as db:
            rows = db.execute("SELECT m.id,m.created_at,f.terms,f.entities,f.pixels,f.vectors FROM memories m JOIN candidate_features f ON m.id=f.id AND m.user_id=f.user_id WHERE m.user_id=?", (user,)).fetchall()
        if not rows:
            return {"data": []}
        docs = {row[0]: {"created": row[1], "terms": json.loads(row[2]), "entities": set(json.loads(row[3])),
                         "pixels": set(json.loads(row[4])), "vectors": json.loads(row[5])} for row in rows}
        query = {t for t in tokenize(text) if t not in STOP}
        average = sum(sum(d["terms"].values()) for d in docs.values()) / len(docs) or 1
        df = {q: sum(q in d["terms"] for d in docs.values()) for q in query}
        lexical = {}
        for identity, doc in docs.items():
            length = sum(doc["terms"].values())
            score = 0.0
            for term in query:
                tf = doc["terms"].get(term, 0)
                if tf:
                    score += math.log(1 + (len(docs) - df[term] + .5) / (df[term] + .5)) * tf * 2.2 / (tf + 1.2 * (.25 + .75 * length / average))
            if score > 0:
                lexical[identity] = score
        fused = collections.defaultdict(float)
        rrf(fused, sorted(lexical, key=lambda x: (-lexical[x], x))[:128])
        matches = sorted(identity for identity, doc in docs.items() if query_pixels & doc["pixels"])
        rrf(fused, matches, 2.0)
        if self.provider:
            # Canonical pixel matches already identify known images; avoid another paid vision call.
            if query_pixels and not matches:
                description = self.provider.describe(user, payload["query"])
                text += "\n" + description.get("caption", "") + "\n" + description.get("ocr", "")
            if text.strip():
                vector = self.provider.embed(user, [text[:1200]])[0]
                dense = {identity: max((cosine(vector, v) for v in doc["vectors"]), default=-1) for identity, doc in docs.items()}
                dense = {identity: score for identity, score in dense.items() if score > .05}
                rrf(fused, sorted(dense, key=lambda x: (-dense[x], x))[:128])
        # Only rare source entities from actual top candidates can form second-hop links.
        entity_df = collections.Counter(name for doc in docs.values() for name in doc["entities"])
        seeds = sorted(fused, key=lambda x: (-fused[x], x))[:5]
        expanded = collections.defaultdict(float)
        for seed in seeds:
            rare = {name for name in docs[seed]["entities"] if entity_df[name] <= max(3, len(docs) // 20)}
            for identity, doc in docs.items():
                if identity not in seeds and rare & doc["entities"]:
                    expanded[identity] = max(expanded[identity], fused[seed] * .75)
        for identity, score in expanded.items():
            fused[identity] += score
        if re.search(r"\b(now|currently|latest|current)\b|现在|目前|最新", text, re.I):
            dated = {}
            for identity in fused:
                try:
                    dated[identity] = datetime.fromisoformat(docs[identity]["created"].replace("Z", "+00:00")).timestamp()
                except (ValueError, OverflowError):
                    continue
            if dated:
                minimum, maximum = min(dated.values()), max(dated.values())
                if maximum > minimum:
                    for identity, stamp in dated.items():
                        fused[identity] += .02 * (stamp - minimum) / (maximum - minimum)
        if not fused:
            return {"data": []}
        ordered = sorted(fused, key=lambda x: (-fused[x], x))
        results, image_count, image_bytes, seen_pixels = [], 0, 0, set()
        with self.connect() as db:
            for identity in ordered:
                raw, created = db.execute("SELECT content,created_at FROM memories WHERE user_id=? AND id=?", (user, identity)).fetchone()
                content = json.loads(raw)
                if isinstance(content, list):
                    kept = []
                    for part in content:
                        if part.get("type") == "text":
                            kept.append(part)
                            continue
                        pixels = pixel_fingerprints([part])[0]
                        size = len(base64.b64decode(part["image_url"]["url"].split(",", 1)[1]))
                        if pixels in seen_pixels or image_count >= self.max_images or image_bytes + size > self.max_image_bytes:
                            continue
                        kept.append(part)
                        seen_pixels.add(pixels)
                        image_count += 1
                        image_bytes += size
                    if not kept:
                        continue
                    content = kept
                results.append({"id": identity, "content": content, "score": fused[identity], "created_at": created})
                if len(results) >= k:
                    break
        return {"data": results}
