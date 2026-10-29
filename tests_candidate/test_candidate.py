import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from candidate.providers import ProviderError, SpecifiedModels
from candidate.store import CandidateStore
from candidate.vision import pixel_fingerprints
from memory_service import ContractError
from scripts.benchmark_baseline import image


class FakeProvider:
    """Controlled plumbing fixture, not evidence of real model accuracy."""
    def __init__(self):
        self.calls = 0
        self.fail = False

    def describe(self, user, content):
        self.calls += 1
        if self.fail:
            raise ProviderError("synthetic failure")
        return {"caption": "synthetic derived metadata", "ocr": "", "entities": [], "facts": []}

    def embed(self, user, texts):
        self.calls += 1
        return [[1.0, 0.0] for _ in texts]


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)
        self.store = CandidateStore(self.path / "memory.sqlite3")

    def tearDown(self):
        self.directory.cleanup()

    def add(self, content, request="r1", user="u1", timestamp=None, store=None):
        message = {"role": "user", "content": content}
        if timestamp is not None:
            message["timestamp"] = timestamp
        payload = {"user_id": user, "request_id": request, "session_id": "session-" + request, "messages": [message]}
        return (store or self.store).add(payload)

    def search(self, query, user="u1", k=100, store=None):
        return (store or self.store).search({"query": query, "user_id": user, "top_k": k})["data"]

    def test_reencoding_preserves_pixel_match_and_original_evidence(self):
        original = [image((20, 150, 40), 6)]
        encoded_again = [image((20, 150, 40), 0)]
        self.assertNotEqual(original, encoded_again)
        self.add(original)
        self.assertEqual(self.search(encoded_again)[0]["content"], original)

    def test_different_colors_do_not_match(self):
        self.add([image((255, 0, 0))])
        self.assertEqual(self.search([image((0, 255, 0))]), [])

    def test_pixel_identity_does_not_merge_distinct_colors(self):
        self.assertNotEqual(pixel_fingerprints([image((10, 20, 30))]), pixel_fingerprints([image((30, 20, 10))]))

    def test_two_hop_returns_both_source_sessions(self):
        self.add("The runner in the photo is Priya.", request="bridge")
        self.add("Priya prefers Darjeeling.", request="target")
        self.add("Unrelated inventory ledger.", request="noise")
        values = [row["content"] for row in self.search("Which beverage did the runner prefer?", k=5)]
        self.assertIn("The runner in the photo is Priya.", values)
        self.assertIn("Priya prefers Darjeeling.", values)

    def test_graph_never_expands_into_other_user(self):
        self.add("The runner is Priya.", request="bridge")
        self.add("Priya prefers Darjeeling.", request="secret", user="u2")
        self.assertNotIn("Priya prefers Darjeeling.", [row["content"] for row in self.search("runner")])

    def test_latest_state_prefers_newer_source_timestamp(self):
        self.add("Sam lives in Paris. Sam lives in Paris.", request="old", timestamp=1704067200000)
        self.add("Sam lives in Berlin.", request="new", timestamp=1735689600000)
        self.assertEqual(self.search("Where does Sam live now?")[0]["content"], "Sam lives in Berlin.")

    def test_no_recency_override_for_historical_query(self):
        self.add("Sam stayed in Paris in 2024.", request="old", timestamp=1704067200000)
        self.add("Sam moved to Berlin in 2025.", request="new", timestamp=1735689600000)
        self.assertEqual(self.search("Sam Paris 2024")[0]["content"], "Sam stayed in Paris in 2024.")

    def test_idempotency_skips_model_work(self):
        fake = FakeProvider()
        store = CandidateStore(self.path / "models.sqlite3", provider=fake)
        self.add("sample", store=store)
        before = fake.calls
        self.add("sample", store=store)
        self.assertEqual(fake.calls, before)

    def test_known_reencoded_image_query_does_not_call_models(self):
        fake = FakeProvider()
        store = CandidateStore(self.path / "known-image.sqlite3", provider=fake)
        self.add([image((10, 90, 120), 6)], store=store)
        before = fake.calls
        self.assertEqual(len(self.search([image((10, 90, 120), 0)], store=store)), 1)
        self.assertEqual(fake.calls, before)

    def test_conflict_does_not_call_model(self):
        fake = FakeProvider()
        store = CandidateStore(self.path / "models.sqlite3", provider=fake)
        self.add("sample", store=store)
        before = fake.calls
        with self.assertRaises(ContractError):
            self.add("changed payload", store=store)
        self.assertEqual(fake.calls, before)

    def test_failed_index_is_not_searchable_then_retry_completes(self):
        fake = FakeProvider()
        fake.fail = True
        store = CandidateStore(self.path / "models.sqlite3", provider=fake)
        with self.assertRaises(ProviderError):
            self.add("sample", store=store)
        self.assertEqual(self.search("sample", store=store), [])
        fake.fail = False
        self.add("sample", store=store)
        self.assertEqual(self.search("sample", store=store)[0]["content"], "sample")

    def test_derived_metadata_not_returned_as_source_evidence(self):
        store = CandidateStore(self.path / "models.sqlite3", provider=FakeProvider())
        self.add("original source only", store=store)
        self.assertEqual(self.search("derived metadata", store=store)[0]["content"], "original source only")

    def test_dense_route_can_retrieve_without_lexical_overlap(self):
        class ControlledVectors(FakeProvider):
            def embed(self, user, texts):
                return [[1.0, 0.0] if "automobile" in text or "car" in text else [0.0, 1.0] for text in texts]
        store = CandidateStore(self.path / "dense.sqlite3", provider=ControlledVectors())
        self.add("An automobile was purchased.", request="car", store=store)
        self.add("Bicycle inventory ledger.", request="bike", store=store)
        self.assertEqual(self.search("car", store=store)[0]["content"], "An automobile was purchased.")

    def test_image_budget_keeps_text_and_does_not_mutate_storage(self):
        store = CandidateStore(self.path / "budget.sqlite3", max_images=1)
        self.add([{"type": "text", "text": "picture red"}, image((255, 0, 0))], request="red", store=store)
        self.add([{"type": "text", "text": "picture green"}, image((0, 255, 0))], request="green", store=store)
        results = self.search("picture", store=store)
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(part["type"] == "image_url" for row in results for part in row["content"]), 1)
        with store.connect() as db:
            originals = [json.loads(row[0]) for row in db.execute("SELECT content FROM memories")]
        self.assertEqual(sum(part["type"] == "image_url" for content in originals for part in content), 2)

    def test_top_k_and_invalid_values(self):
        self.add("coffee one", request="a")
        self.add("coffee two", request="b")
        self.assertEqual(len(self.search("coffee", k=1)), 1)
        self.assertEqual(self.search("coffee", k=0), [])
        with self.assertRaises(ContractError):
            self.search("coffee", k=True)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "provider.sqlite3"
        self.config = {"OPENAI_API_KEY": "synthetic-key", "OPENAI_BASE_URL": "https://models.invalid/v1",
                       "DASHSCOPE_API_KEY": "synthetic-key", "DASHSCOPE_BASE_URL": "https://embeddings.invalid/v1",
                       "EMBEDDING_DIMENSIONS": "64", "MAX_LLM_REQUESTS": "10", "MAX_EMBEDDING_REQUESTS": "10"}
        self.calls = []

    def tearDown(self):
        self.directory.cleanup()

    def transport(self, url, key, payload):
        self.calls.append((url, payload))
        if "input" in payload:
            return {"data": [{"index": i, "embedding": [1.0] + [0.0] * 63} for i in reversed(range(len(payload["input"])))]}
        return {"choices": [{"message": {"content": json.dumps({"caption": "test", "ocr": "", "entities": [], "facts": []})}}]}

    def provider(self, transport=None):
        return SpecifiedModels(self.config, self.path, transport or self.transport)

    def test_cache_is_scoped_to_user(self):
        provider = self.provider()
        provider.describe("u1", "sample")
        provider.describe("u1", "sample")
        self.assertEqual(len(self.calls), 1)
        provider.describe("u2", "sample")
        self.assertEqual(len(self.calls), 2)

    def test_request_cap_survives_provider_restart(self):
        self.config["MAX_LLM_REQUESTS"] = "1"
        self.provider().describe("u1", "sample")
        with self.assertRaises(ProviderError):
            self.provider().describe("u1", "different")
        self.assertEqual(len(self.calls), 1)

    def test_failed_call_counts_toward_cap(self):
        self.config["MAX_LLM_REQUESTS"] = "1"
        def fail(*args):
            raise ProviderError("uncertain transport result")
        with self.assertRaises(ProviderError):
            self.provider(fail).describe("u1", "sample")
        with self.assertRaises(ProviderError):
            self.provider().describe("u1", "sample")
        self.assertEqual(self.calls, [])

    def test_invalid_model_response_is_not_cached(self):
        with self.assertRaises(ProviderError):
            self.provider(lambda *args: {"choices": []}).describe("u1", "sample")
        self.assertEqual(self.provider().describe("u1", "sample")["caption"], "test")
        self.assertEqual(len(self.calls), 1)

    def test_embeddings_batch_order_and_normalization(self):
        values = self.provider().embed("u1", ["text%d" % i for i in range(21)])
        self.assertEqual(len(values), 21)
        self.assertEqual([len(p[1]["input"]) for p in self.calls], [10, 10, 1])
        self.assertTrue(all(len(vector) == 64 and vector[0] == 1 for vector in values))

    def test_embedding_dimension_mismatch_rejected(self):
        with self.assertRaises(ProviderError):
            self.provider(lambda *args: {"data": [{"index": 0, "embedding": [1.0, 0.0]}]}).embed("u1", ["sample"])

    def test_missing_key_does_not_attempt_transport(self):
        self.config["OPENAI_API_KEY"] = ""
        with self.assertRaises(ProviderError):
            self.provider().describe("u1", "sample")
        self.assertEqual(self.calls, [])

    def test_other_model_names_are_not_silently_substituted(self):
        self.config["OPENAI_MODEL"] = "another-model"
        with self.assertRaises(ProviderError):
            self.provider()

    def test_provider_reported_wrong_model_is_rejected(self):
        def wrong_model(*args):
            result = self.transport(*args)
            result["model"] = "a-different-model"
            return result
        with self.assertRaises(ProviderError):
            self.provider(wrong_model).describe("u1", "sample")

    def test_http_endpoints_rejected_before_network(self):
        with self.assertRaises(ProviderError):
            self.provider()._http("http://models.invalid/v1", "synthetic-key", {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
