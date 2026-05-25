"""Tests for the corpus archive (audit-corpus-ai --prune / --reinstate).

The archive is the project's "preservation layer" -- pruned docs go
there instead of being discarded so a later filter tweak (or a smaller
SBIR / J-Book regex change) can re-evaluate and pull docs back without
a re-scrape. Tests pin the behaviour:

  * --prune MOVES drops to archive (active corpus shrinks, archive grows).
  * Protected docs (Claude syntheses, SBIR, J-Book records) are never
    dropped even if their body has no AI signal.
  * --reinstate brings back archived docs that now pass the gate, leaves
    those still off-topic in the archive.
  * --reinstate is idempotent and dedupes against the active corpus
    (no double-store if someone re-ingested via scrape).
"""

import argparse
import datetime
import json
import pathlib
import tempfile
import unittest

from ailandscape import cli, config, corpus


class CorpusArchiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        # Redirect every config path the audit command touches.
        self._orig = {
            "CORPUS_FILE": config.CORPUS_FILE,
            "CORPUS_ARCHIVE_FILE": config.CORPUS_ARCHIVE_FILE,
            "DATA_DIR": config.DATA_DIR,
            "SNAPSHOT_DIR": config.SNAPSHOT_DIR,
            "CORPUS_DIR": config.CORPUS_DIR,
        }
        config.CORPUS_FILE = self.tmp / "corpus" / "documents.jsonl"
        config.CORPUS_ARCHIVE_FILE = self.tmp / "corpus" / "archived.jsonl"
        config.DATA_DIR = self.tmp / "data"
        config.SNAPSHOT_DIR = self.tmp / "snapshots"
        config.CORPUS_DIR = self.tmp / "corpus"

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(config, k, v)

    def _seed(self, docs):
        config.CORPUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        for d in docs:
            corpus.append(config.CORPUS_FILE, d)

    def _args(self, **kw):
        # cmd_audit_corpus_ai reads .prune / .reinstate off the Namespace.
        defaults = {"prune": False, "reinstate": False}
        defaults.update(kw)
        return argparse.Namespace(**defaults)

    def _make_doc(self, chash, title, body="", source="Defense One",
                  metadata=None):
        return {
            "content_hash": chash, "url": "https://t/" + chash,
            "title": title, "source": source, "published": "",
            "raw_text": body, "metadata": metadata or {},
        }

    def test_prune_moves_to_archive(self):
        ai_doc = self._make_doc(
            "h1", "Machine learning beats human",
            "Pentagon adopted machine learning for the kill chain.",
        )
        non_ai_doc = self._make_doc(
            "h2", "Cold War Chipmunk Spyplane",
            "The Chipmunk was a small training aircraft.",
        )
        self._seed([ai_doc, non_ai_doc])

        rc = cli.cmd_audit_corpus_ai(self._args(prune=True))
        self.assertEqual(rc, 0)

        active = corpus.load(config.CORPUS_FILE)
        self.assertEqual([d["content_hash"] for d in active], ["h1"])
        # Archive should have the dropped one with extra fields.
        archived = cli._load_archive()
        self.assertEqual([d["content_hash"] for d in archived], ["h2"])
        self.assertIn("archived_at", archived[0])
        self.assertEqual(archived[0]["archived_reason"], "ai_filter")

    def test_protected_docs_never_dropped(self):
        # Claude synthesis bypasses the gate even with non-AI body.
        syn = self._make_doc(
            "s1", "Some entity overview",
            "Plain English sentence with no AI keyword.",
            source="Claude synthesis",
        )
        # SBIR record also bypasses (gated at ingest).
        sbir = self._make_doc(
            "s2", "Award abstract",
            "No AI term in body.",
            source="SBIR.gov DOD 2025",
            metadata={"data_source": "SBIR", "award_amount": 1000000},
        )
        self._seed([syn, sbir])

        cli.cmd_audit_corpus_ai(self._args(prune=True))

        active_hashes = {d["content_hash"] for d in corpus.load(config.CORPUS_FILE)}
        self.assertEqual(active_hashes, {"s1", "s2"})
        # Nothing went to archive.
        self.assertEqual(cli._load_archive(), [])

    def test_reinstate_recovers_now_passing_docs(self):
        # Seed: archive holds two docs. One is now AI-relevant under
        # the current filter (the filter could have been tightened
        # since archival, but the test fakes the post-tweak state by
        # putting an AI-keyword doc IN the archive).
        now_ai = self._make_doc(
            "a1", "Maven Smart System deal",
            "Marine Corps signs Maven Smart System enterprise license.",
        )
        still_off_topic = self._make_doc(
            "a2", "Cold War Chipmunk Spyplane",
            "The Chipmunk was a small training aircraft.",
        )
        # Manually populate archive (skip prune).
        cli._append_archive([now_ai, still_off_topic], reason="ai_filter_test")
        # Active corpus is empty.

        rc = cli.cmd_audit_corpus_ai(self._args(reinstate=True))
        self.assertEqual(rc, 0)

        active = corpus.load(config.CORPUS_FILE)
        self.assertEqual([d["content_hash"] for d in active], ["a1"])
        # The reinstated doc must have lost its archive-only fields.
        self.assertNotIn("archived_at", active[0])
        self.assertNotIn("archived_reason", active[0])
        # The off-topic doc stays in the archive.
        archived = cli._load_archive()
        self.assertEqual([d["content_hash"] for d in archived], ["a2"])

    def test_reinstate_dedupes_against_active_corpus(self):
        # If someone re-ingested the same doc by re-scraping, --reinstate
        # must NOT double-store it. It should silently drop the archive
        # copy (the active version is the authoritative one).
        live = self._make_doc(
            "x1", "Maven Smart System deal",
            "Marine Corps signs Maven Smart System enterprise license.",
        )
        self._seed([live])
        cli._append_archive([live], reason="ai_filter")  # same hash in archive

        cli.cmd_audit_corpus_ai(self._args(reinstate=True))

        active = corpus.load(config.CORPUS_FILE)
        self.assertEqual(len(active), 1, "must not duplicate the live doc")
        # The archive copy should be gone (dropped, not reinstated).
        self.assertEqual(cli._load_archive(), [])

    def test_audit_only_does_not_touch_files(self):
        ai_doc = self._make_doc("h1", "Generative AI breakthrough")
        non_ai_doc = self._make_doc("h2", "Quad ministers' meeting")
        self._seed([ai_doc, non_ai_doc])
        # No --prune, no --reinstate.
        cli.cmd_audit_corpus_ai(self._args())
        active = {d["content_hash"] for d in corpus.load(config.CORPUS_FILE)}
        self.assertEqual(active, {"h1", "h2"})
        self.assertFalse(config.CORPUS_ARCHIVE_FILE.exists())


class HistoryCommandTest(unittest.TestCase):
    """The `ailandscape history` CLI surfaces the per-run JSONL nicely."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self._orig_path = config.RUN_HISTORY_FILE
        self._orig_legacy = config._LEGACY_RUN_HISTORY
        config.RUN_HISTORY_FILE = self.tmp / "run-history.jsonl"
        config._LEGACY_RUN_HISTORY = self.tmp / "legacy.jsonl"

    def tearDown(self):
        config.RUN_HISTORY_FILE = self._orig_path
        config._LEGACY_RUN_HISTORY = self._orig_legacy

    def _seed(self, *records):
        config.RUN_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with config.RUN_HISTORY_FILE.open("a", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

    def test_history_empty(self):
        rc = cli.cmd_history(argparse.Namespace(limit=20, full=False))
        self.assertEqual(rc, 0)  # exits cleanly, prints "no history" message

    def test_history_reads_seeded_records(self):
        self._seed(
            {
                "finished_at": "2026-05-22T19:02:35+00:00",
                "fetched": 100, "added": 5, "filtered_non_ai": 0,
                "scrape_seconds": 60.0, "rebuild_seconds": 30.0,
                "documents": 100, "nodes": 1000, "typed_relations": 50,
            },
            {
                "finished_at": "2026-05-23T19:02:35+00:00",
                "fetched": 80, "added": 10, "filtered_non_ai": 20,
                "scrape_seconds": 50.0, "rebuild_seconds": 28.0,
                "documents": 110, "nodes": 1100, "typed_relations": 55,
                "feeds": {"BrokenFeed": {"error": "404", "fetched": 0,
                                         "added": 0, "filtered_non_ai": 0}},
            },
        )
        # Just exercise the code path -- assertion is no-throw + rc=0.
        rc = cli.cmd_history(argparse.Namespace(limit=20, full=False))
        self.assertEqual(rc, 0)
        rc = cli.cmd_history(argparse.Namespace(limit=1, full=True))
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
