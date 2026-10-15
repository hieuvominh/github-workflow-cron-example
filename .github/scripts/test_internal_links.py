import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from internal_links import load_computing_snapshot, rank_candidates


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, *_):
        return json.dumps(self.payload).encode()


class InternalLinkTests(unittest.TestCase):
    def setUp(self):
        self.candidates = [
            {
                "id": 1,
                "title": "AMD Ryzen desktop processors explained",
                "path": "/computing/amd-ryzen",
                "sourceUrl": "https://source.example/amd",
                "keywords": ["AMD Ryzen CPU"],
            },
            {
                "id": 2,
                "title": "Wi-Fi router setup guide",
                "path": "/computing/router-guide",
                "sourceUrl": "https://source.example/router",
                "keywords": ["home Wi-Fi"],
            },
        ]

    def test_snapshot_fetches_once_then_reuses_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "links.json"
            response = FakeResponse({"category": "computing", "candidates": self.candidates})
            with patch("internal_links.urllib.request.urlopen", return_value=response) as fetch:
                first = load_computing_snapshot(path, "https://cms.example", "secret", now=100)
                second = load_computing_snapshot(path, "https://cms.example", "secret", now=101)
            self.assertEqual(first, second)
            self.assertEqual(fetch.call_count, 1)

    def test_stale_snapshot_refreshes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "links.json"
            path.write_text(json.dumps({"fetchedAt": 1, "candidates": self.candidates}))
            response = FakeResponse({"category": "computing", "candidates": []})
            with patch("internal_links.urllib.request.urlopen", return_value=response) as fetch:
                result = load_computing_snapshot(path, "https://cms.example", "secret", now=90000)
            self.assertEqual(result, [])
            self.assertEqual(fetch.call_count, 1)

    def test_rejects_more_than_fifty_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            response = FakeResponse({"category": "computing", "candidates": self.candidates * 26})
            with patch("internal_links.urllib.request.urlopen", return_value=response):
                with self.assertRaisesRegex(RuntimeError, "invalid"):
                    load_computing_snapshot(Path(directory) / "links.json", "https://cms.example", "secret")

    def test_ranking_excludes_same_source_and_irrelevant_posts(self):
        result = rank_candidates(
            self.candidates,
            "New AMD Ryzen desktop processor",
            "The new AMD Ryzen CPU has faster cores.",
            source_url="https://source.example/router",
        )
        self.assertEqual([item["id"] for item in result], [1])


if __name__ == "__main__":
    unittest.main()
