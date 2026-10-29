import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import memory_service as service

# Synthetic one-pixel PNG; no official/private benchmark records are used.
PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZYQAAAAASUVORK5CYII="
PARTS = [{"type": "text", "text": "小王戴蓝色头盔骑行，后来推荐湖边咖啡馆。"},
         {"type": "image_url", "image_url": {"url": PNG}}]
KEY = "synthetic-test-key-not-for-deployment"


def add_payload(user="synthetic-user", request="synthetic-request", content=None):
    return {"request_id": request, "user_id": user, "session_id": "synthetic-session",
            "messages": [{"role": "user", "timestamp": 1704067200000,
                          "content": PARTS if content is None else content}]}


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "memory.sqlite3"
        self.store = service.MemoryStore(self.db)
        self.server = service.MemoryServer(("127.0.0.1", 0), self.store, KEY)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def call(self, path, payload=None, key=KEY):
        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + key}
        body = json.dumps(payload).encode() if payload is not None else None
        request = Request(self.base + path, data=body, headers=headers)
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as response:
            return response.code, json.load(response)

    def search(self, user="synthetic-user", query="蓝色头盔", k=100):
        return self.call("/search", {"query": query, "user_id": user, "top_k": k})

    def test_add_search_original_parts_and_timestamp(self):
        payload = add_payload()
        status, value = self.call("/add", payload)
        self.assertEqual(status, 200)
        self.assertEqual(value, dict(success=True, **{k: payload[k] for k in ("request_id", "user_id", "session_id")}))
        status, result = self.search()
        self.assertEqual(status, 200)
        self.assertEqual(result["data"][0]["content"], PARTS)
        self.assertEqual(result["data"][0]["created_at"], "2024-01-01T00:00:00+00:00")

    def test_cross_user_isolation(self):
        self.call("/add", add_payload())
        self.assertEqual(self.search(user="different-user")[1], {"data": []})

    def test_user_stats_and_ids_are_isolated(self):
        self.call("/add", add_payload())
        before = self.search()[1]
        self.call("/add", add_payload(user="other", content="蓝色头盔 蓝色头盔"))
        self.assertEqual(self.search()[1], before)

    def test_idempotent_retry(self):
        self.call("/add", add_payload())
        first = self.search()[1]
        self.assertEqual(self.call("/add", add_payload())[0], 200)
        self.assertEqual(self.search()[1], first)

    def test_conflicting_request_is_rejected(self):
        self.call("/add", add_payload())
        self.assertEqual(self.call("/add", add_payload(content="changed"))[0], 409)
        self.assertEqual(self.search()[1]["data"][0]["content"], PARTS)

    def test_invalid_batch_is_atomic(self):
        payload = add_payload()
        payload["messages"].append({"role": "system", "content": "invalid"})
        self.assertEqual(self.call("/add", payload)[0], 422)
        self.assertEqual(self.search()[1], {"data": []})

    def test_persistence_in_fresh_process(self):
        self.call("/add", add_payload())
        source = "from memory_service import MemoryStore; import sys; print(len(MemoryStore(sys.argv[1]).search({'user_id':'synthetic-user','query':'蓝色头盔','top_k':100})['data']))"
        value = subprocess.check_output([sys.executable, "-c", source, str(self.db)],
                                        cwd=str(Path(service.__file__).parent), text=True)
        self.assertEqual(value.strip(), "1")

    def test_top_k_bound(self):
        for i in range(4):
            self.call("/add", add_payload(request="request-%d" % i))
        self.assertEqual(len(self.search(k=2)[1]["data"]), 2)
        self.assertEqual(self.search(k=0)[1], {"data": []})
        self.assertEqual(self.search(k=101)[0], 422)
        self.assertEqual(self.search(k=True)[0], 422)

    def test_english_and_chinese_ranking(self):
        self.call("/add", add_payload(content="red apple fruit", request="apple"))
        self.call("/add", add_payload(content="blue bicycle helmet", request="bike"))
        self.assertEqual(self.search(query="bicycle")[1]["data"][0]["content"], "blue bicycle helmet")
        self.call("/add", add_payload())
        self.assertEqual(self.search()[1]["data"][0]["content"], PARTS)

    def test_multimodal_query_and_options(self):
        self.call("/add", add_payload())
        result = self.call("/search", {"query": PARTS, "user_id": "synthetic-user",
                                       "options": ["A", "B"], "top_k": 100})
        self.assertEqual(result[0], 200)
        self.assertEqual(result[1]["data"][0]["content"], PARTS)

    def test_empty_image_query_retrieves_exact_image(self):
        self.call("/add", add_payload(content=[PARTS[1]]))
        self.assertGreater(self.search(query=[PARTS[1]])[1]["data"][0]["score"], 0)

    def test_authentication(self):
        self.assertEqual(self.call("/add", add_payload(), key="wrong")[0], 401)
        self.assertEqual(self.call("/health")[0], 200)

    def test_invalid_remote_image(self):
        content = [{"type": "image_url", "image_url": {"url": "https://example.com/image.png"}}]
        self.assertEqual(self.call("/add", add_payload(content=content))[0], 422)

    def test_invalid_base64_and_signature(self):
        for url in ("data:image/png;base64,abc", "data:image/png;base64,aGVsbG8="):
            content = [{"type": "image_url", "image_url": {"url": url}}]
            self.assertEqual(self.call("/add", add_payload(content=content))[0], 422)

    def test_image_and_batch_size_limits(self):
        with patch.object(service, "IMAGE_LIMIT", 10):
            self.assertEqual(self.call("/add", add_payload())[0], 413)
        size = len(base64.b64decode(PNG.split(",")[1]))
        with patch.object(service, "MEDIA_LIMIT", size):
            payload = add_payload()
            payload["messages"].append(payload["messages"][0])
            self.assertEqual(self.call("/add", payload)[0], 413)
        self.assertEqual(self.search()[1], {"data": []})

    def test_search_response_media_budget(self):
        self.call("/add", add_payload())
        self.call("/add", add_payload(request="second"))
        size = len(base64.b64decode(PNG.split(",")[1]))
        with patch.object(service, "MEDIA_LIMIT", size):
            self.assertEqual(len(self.search()[1]["data"]), 1)

    def test_missing_fields_and_invalid_values(self):
        for field in ("user_id", "request_id", "session_id", "messages"):
            payload = add_payload()
            del payload[field]
            self.assertEqual(self.call("/add", payload)[0], 422)
        self.assertEqual(self.call("/add", ["wrong"])[0], 422)
        self.assertEqual(self.call("/add", add_payload(content=[]))[0], 422)
        payload = add_payload()
        payload["messages"][0]["timestamp"] = True
        self.assertEqual(self.call("/add", payload)[0], 422)

    def test_query_is_not_executed(self):
        self.call("/add", add_payload())
        self.assertEqual(self.search(query="'; DROP TABLE memories; --")[0], 200)
        self.assertEqual(len(self.search()[1]["data"]), 1)

    def test_parallel_add_retry_and_search(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.call("/add", add_payload())[0], range(8)))
        self.assertEqual(results, [200] * 8)
        self.assertEqual(len(self.search()[1]["data"]), 1)

    def test_unknown_route(self):
        self.assertEqual(self.call("/missing", {})[0], 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
