from pathlib import Path
import os
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "rag" / "manifest.json"
requires_manifest = unittest.skipUnless(
    MANIFEST.exists(), "requires local rag/manifest.json"
)

from rag_gateway.rerank_api import ApiReranker, RerankApiError
from rag_gateway.search import CompanyRAG


class FakeApi:
    """Mimics ApiReranker for search-integration tests."""

    def __init__(self, ranked=None, error=None, fallback="rrf"):
        self.ranked = ranked or []
        self.error = error
        self.fallback = fallback
        self.max_doc_chars = 4000
        self.fetch_min_score = 0.05
        self.calls = []

    def rerank(self, query, documents, top_n=None):
        self.calls.append((query, list(documents), top_n))
        if self.error:
            raise RerankApiError(self.error)
        return self.ranked


class TestRerankApiModule(unittest.TestCase):
    def test_available_key_resolution(self):
        self.assertFalse(ApiReranker({}).available())
        self.assertFalse(ApiReranker({"enabled": True, "base_url": "https://x"}).available())
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8") as f:
            f.write("MY_TEST_KEY_XYZ=abc123\n")
            path = f.name
        try:
            r = ApiReranker({"enabled": True, "base_url": "https://x",
                             "key_var": "MY_TEST_KEY_XYZ", "key_file": path})
            self.assertTrue(r.available())
        finally:
            os.unlink(path)
        os.environ["MY_TEST_KEY_XYZ2"] = "env-key"
        try:
            r2 = ApiReranker({"enabled": True, "base_url": "https://x", "key_var": "MY_TEST_KEY_XYZ2"})
            self.assertTrue(r2.available())
        finally:
            os.environ.pop("MY_TEST_KEY_XYZ2", None)

    def test_rerank_parses_and_sorts(self):
        r = ApiReranker({"enabled": True, "base_url": "https://x"})
        r._post = lambda payload: {"output": {"results": [
            {"index": 1, "relevance_score": 0.2},
            {"index": 0, "relevance_score": 0.9},
        ]}}
        out = r.rerank("q", ["a", "b"], top_n=2)
        self.assertEqual([x["index"] for x in out], [0, 1])
        self.assertAlmostEqual(out[0]["relevance_score"], 0.9)
        # compatible-style response shape
        r._post = lambda payload: {"results": [{"index": 0, "relevance_score": 0.5}]}
        out2 = r.rerank("q", ["a"])
        self.assertEqual(out2[0]["index"], 0)

    def test_empty_docs(self):
        r = ApiReranker({"enabled": True, "base_url": "https://x"})
        self.assertEqual(r.rerank("q", []), [])


class TestApiRerankInSearch(unittest.TestCase):
    def _hits(self, rag, n=3):
        core_ids = [
            nid for nid in rag.manifest["nodes"]
            if rag.manifest["nodes"][nid].get("collection") == "company-core"
        ][:n]
        return [rag._to_hit(nid, 0.5 - i * 0.1, "qmd") for i, nid in enumerate(core_ids)]

    @requires_manifest
    def test_api_rerank_reorders_and_scores(self):
        rag = CompanyRAG(ROOT)
        hits = self._hits(rag, 3)
        fake = FakeApi(ranked=[
            {"index": 2, "relevance_score": 0.99},
            {"index": 0, "relevance_score": 0.4},
            {"index": 1, "relevance_score": 0.1},
        ])
        rag.rerank_api = fake
        out, mode = rag._api_rerank(
            "q", hits, keep=2, searches=[], collections=[],
            mcfg={"candidate_limit": 16, "min_score": 0.2})
        self.assertEqual(mode, "api")
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].node_id, hits[2].node_id)
        self.assertAlmostEqual(out[0].score, 0.99)
        self.assertEqual(fake.calls[0][2], 2)  # top_n == keep

    @requires_manifest
    def test_api_failure_falls_back_rrf(self):
        rag = CompanyRAG(ROOT)
        hits = self._hits(rag, 3)
        rag.rerank_api = FakeApi(error="boom", fallback="rrf")
        out, mode = rag._api_rerank(
            "q", hits, keep=2, searches=[], collections=[],
            mcfg={"candidate_limit": 16, "min_score": 0.2})
        self.assertEqual(mode, "rrf-fallback")
        self.assertEqual(len(out), 2)
        self.assertEqual(rag._last_rerank_error, "boom")


if __name__ == "__main__":
    unittest.main()
