"""Tests for the daily-hype persistence layer.

`ailandscape.hype` owns the recent-document pick, the synthesis call, and
the JSON-file persistence shared by the CLI and the server endpoint.
"""

import datetime
import json
import os
import pathlib
import tempfile
import unittest
import urllib.request

from ailandscape import hype, synthesis


class FakeResponse:
    def __init__(self, text):
        self._text = text

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def read(self):
        return json.dumps(
            {"content": [{"type": "text", "text": self._text}]}
        ).encode("utf-8")


class RecentDocumentsTest(unittest.TestCase):
    def test_picks_documents_inside_the_window(self):
        today = datetime.date.today()
        docs = [
            {"title": "today", "published": today.isoformat()},
            {"title": "two days ago",
             "published": (today - datetime.timedelta(days=2)).isoformat()},
            {"title": "old",
             "published": (today - datetime.timedelta(days=30)).isoformat()},
        ]
        picks = hype._recent_documents(docs, days=1)
        titles = [d["title"] for d in picks]
        self.assertEqual(titles, ["today"])

    def test_soft_fallback_when_main_window_empty(self):
        # No docs in the 1-day window — fall back to the 3-day window
        # so a quiet news cycle still produces output.
        today = datetime.date.today()
        docs = [
            {"title": "two days ago",
             "published": (today - datetime.timedelta(days=2)).isoformat()},
        ]
        picks = hype._recent_documents(docs, days=1)
        self.assertEqual([d["title"] for d in picks], ["two days ago"])

    def test_falls_back_to_fetched_at_when_published_missing(self):
        today = datetime.date.today()
        docs = [
            {"title": "no published date",
             "fetched_at": today.isoformat() + "T12:00:00+00:00"},
        ]
        picks = hype._recent_documents(docs, days=1)
        self.assertEqual([d["title"] for d in picks], ["no published date"])

    def test_sorted_newest_first(self):
        today = datetime.date.today()
        docs = [
            {"title": "older",
             "published": (today - datetime.timedelta(days=1)).isoformat()},
            {"title": "newer", "published": today.isoformat()},
        ]
        picks = hype._recent_documents(docs, days=2)
        self.assertEqual([d["title"] for d in picks], ["newer", "older"])


class GenerateAndSaveTest(unittest.TestCase):
    def setUp(self):
        self._orig_key = os.environ.get("ANTHROPIC_API_KEY")
        self._orig_urlopen = urllib.request.urlopen
        os.environ["ANTHROPIC_API_KEY"] = "test-key-not-real"
        urllib.request.urlopen = lambda req, timeout=None: FakeResponse(
            "Exciting day in AI!"
        )

    def tearDown(self):
        if self._orig_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = self._orig_key
        urllib.request.urlopen = self._orig_urlopen

    def test_generate_includes_timestamp_and_document_count(self):
        now = datetime.datetime(2026, 5, 26, 19, 30, tzinfo=datetime.timezone.utc)
        docs = [
            {"title": "Headline 1",
             "published": datetime.date.today().isoformat(),
             "raw_text": "Big AI news."},
        ]
        artifact = hype.generate(docs, days=1, now=now)
        self.assertEqual(artifact["generated_at"], now.isoformat(timespec="seconds"))
        self.assertEqual(artifact["window_days"], 1)
        self.assertEqual(artifact["documents_used"], 1)
        self.assertEqual(artifact["hype"], "Exciting day in AI!")

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "daily_hype.json"
            artifact = {
                "generated_at": "2026-05-26T19:30:00+00:00",
                "window_days": 1,
                "documents_used": 4,
                "hype": "stuff",
            }
            hype.save(artifact, path)
            self.assertTrue(path.exists())
            loaded = hype.load(path)
            self.assertEqual(loaded, artifact)

    def test_load_returns_none_for_missing_or_corrupt_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = pathlib.Path(tmp) / "nope.json"
            self.assertIsNone(hype.load(missing))
            corrupt = pathlib.Path(tmp) / "bad.json"
            corrupt.write_text("{ not json", encoding="utf-8")
            self.assertIsNone(hype.load(corrupt))


class GenerateRaisesWithoutKeyTest(unittest.TestCase):
    def test_no_key_propagates_synthesis_error(self):
        orig = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            with self.assertRaises(synthesis.SynthesisError):
                hype.generate([{"title": "x", "published": "2026-05-26"}])
        finally:
            if orig is not None:
                os.environ["ANTHROPIC_API_KEY"] = orig


if __name__ == "__main__":
    unittest.main()
