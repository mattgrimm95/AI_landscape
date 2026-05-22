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

    def test_save_overwrites_corpus(self):
        corpus.append(self.path, {"content_hash": "old"})
        corpus.save(self.path, [
            {"content_hash": "a", "title": "A", "raw_text": "x"},
            {"content_hash": "b", "title": "B", "raw_text": "y"},
        ])
        loaded = corpus.load(self.path)
        self.assertEqual([d["content_hash"] for d in loaded], ["a", "b"])
        self.assertEqual(loaded[0]["title"], "A")

    def test_save_projects_to_fixed_fields(self):
        corpus.save(self.path, [{"content_hash": "h", "extra": "dropped"}])
        loaded = corpus.load(self.path)[0]
        self.assertNotIn("extra", loaded)
        self.assertIn("raw_text", loaded)

    def test_metadata_round_trips(self):
        corpus.append(self.path, {
            "content_hash": "h",
            "metadata": {"data_source": "SBIR", "award_amount": 5.0},
        })
        loaded = corpus.load(self.path)[0]
        self.assertEqual(loaded["metadata"]["data_source"], "SBIR")
        self.assertEqual(loaded["metadata"]["award_amount"], 5.0)

    def test_load_defaults_metadata_for_older_lines(self):
        # A corpus line written before `metadata` existed still loads cleanly.
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write('{"content_hash": "h", "title": "T"}\n')
        loaded = corpus.load(self.path)[0]
        self.assertEqual(loaded["metadata"], {})

    def test_published_date_parses_formats(self):
        pd = corpus.published_date
        # RFC-822 (most RSS feeds), ISO-8601, bare date, and a bare year.
        self.assertEqual(
            pd({"published": "Wed, 21 May 2026 16:00:12 +0000"}), "2026-05-21"
        )
        self.assertEqual(
            pd({"published": "2026-05-21T16:00:12+00:00"}), "2026-05-21"
        )
        self.assertEqual(pd({"published": "2026-05-21"}), "2026-05-21")
        self.assertEqual(pd({"published": "2024"}), "2024-01-01")
        self.assertEqual(pd({"published": ""}), "")
        self.assertEqual(pd({}), "")


if __name__ == "__main__":
    unittest.main()
