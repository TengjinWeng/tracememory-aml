from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from candidate.providers import ProviderError
from candidate.server import Handler, ServingStore
from candidate.store import CandidateStore
from scripts.benchmark_baseline import image


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = CandidateStore(Path(self.temp.name) / "memory.sqlite3")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.store = ServingStore(self.store)
        self.server.key = "synthetic-candidate-http-secret"
        self.server.slots = threading.BoundedSemaphore(4)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def call(self, path, payload=None, auth=True):
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = "Bearer " + self.server.key
        request = Request("http://127.0.0.1:%d%s" % (self.server.server_port, path),
                          data=None if payload is None else json.dumps(payload).encode(), headers=headers)
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def test_real_http_roundtrip_original_image_and_candidate_health(self):
        content = [{"type": "text", "text": "A green photo from last week."}, image((0, 128, 0))]
        payload = {"user_id": "user1", "request_id": "write1", "session_id": "session1",
                   "messages": [{"role": "user", "content": content}]}
        status, result = self.call("/add", payload)
        self.assertEqual(status, 200)
        self.assertIs(result["success"], True)
        result = self.call("/search", {"user_id": "user1", "query": [image((0, 128, 0), 0)], "top_k": 100})
        self.assertEqual(result[0], 200)
        self.assertEqual(result[1]["data"][0]["content"], content)
        self.assertEqual(self.call("/health")[1]["version"], "0.2.0-candidate")
        self.assertEqual(self.call("/search", {"user_id": "other", "query": "photo", "top_k": 100})[1], {"data": []})
        self.assertEqual(self.call("/search", {"user_id": "user1", "query": "photo", "top_k": 100}, auth=False)[0], 401)

    def test_dependency_error_is_retryable_without_disclosing_secret(self):
        class Unavailable:
            def describe(self, *args):
                raise ProviderError("synthetic-secret-should-not-be-exposed")
        self.store.provider = Unavailable()
        status, body = self.call("/add", {"user_id": "user1", "request_id": "write1", "session_id": "session1",
                                         "messages": [{"role": "user", "content": "source"}]})
        self.assertEqual(status, 503)
        self.assertNotIn("synthetic-secret", json.dumps(body))
