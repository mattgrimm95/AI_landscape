import os
import tempfile
import unittest

from ailandscape import corpus


class CorpusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "documents.jsonl")

    def test_load_missing_file_returns_empty(self):
        self.assertEqual(corpus.load(self.path), [])

    def test_append_and_load_roundtrip(self):
        doc = {
            "source": "S",
            "url": "u",
            "title": "T",
            "published": "p",
            "fetched_at": "2026-05-21T00:00:00",
            "content_hash": "h1",
            "raw_text": "body",
        }
        corpus.append(self.path, doc)
        loaded = corpus.load(self.path)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["content_hash"], "h1")
        self.assertEqual(loaded[0]["title"], "T")

    def test_append_projects_to_fixed_fields(self):
        corpus.append(self.path, {"content_hash": "h", "extra": "dropped"})
        loaded = corpus.load(self.path)[0]
        self.assertNotIn("extra", loaded)
        self.assertIn("raw_text", loaded)

    def test_hashes(self):
        corpus.append(self.path, {"content_hash": "a"})
        corpus.append(self.path, {"content_hash": "b"})
        self.assertEqual(corpus.hashes(self.path), {"a", "b"})

    def test_count(self):
        corpus.append(self.path, {"content_hash": "a"})
        corpus.append(self.path, {"content_hash": "b"})
        self.assertEqual(corpus.count(self.path), 2)


if __name__ == "__main__":
    unittest.main()
