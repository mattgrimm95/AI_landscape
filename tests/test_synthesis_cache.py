"""Unit tests for the synthesis snapshot sidecar."""

import datetime
import json
import pathlib
import tempfile
import unittest

from ailandscape import synthesis_cache


class SnapshotShapeTest(unittest.TestCase):
    """The public API surface of the cache module."""

    def test_make_snapshot_starts_blank(self):
        snap = synthesis_cache.make_snapshot(
            corpus_mtime="2026-05-01T00:00:00+00:00", corpus_documents=42,
        )
        self.assertEqual(snap["corpus_documents"], 42)
        for name in synthesis_cache.SECTION_NAMES:
            self.assertIn(name, snap)
            self.assertFalse(snap[name]["available"])
            self.assertEqual(snap[name]["text"], "")
            self.assertEqual(snap[name]["error"], "")

    def test_set_section_records_text_and_metadata(self):
        snap = synthesis_cache.make_snapshot()
        synthesis_cache.set_section(
            snap, "hype",
            text="Big day in AI!", window_days=1, documents_used=12,
        )
        self.assertTrue(snap["hype"]["available"])
        self.assertEqual(snap["hype"]["text"], "Big day in AI!")
        self.assertEqual(snap["hype"]["documents_used"], 12)

    def test_set_section_records_failure(self):
        snap = synthesis_cache.make_snapshot()
        synthesis_cache.set_section(
            snap, "briefing_narrative",
            available=False, error="rate-limited",
        )
        self.assertFalse(snap["briefing_narrative"]["available"])
        self.assertEqual(snap["briefing_narrative"]["error"], "rate-limited")

    def test_set_section_rejects_unknown_name(self):
        snap = synthesis_cache.make_snapshot()
        with self.assertRaises(ValueError):
            synthesis_cache.set_section(snap, "made_up_section", text="hi")


class StalenessTest(unittest.TestCase):
    def _snapshot_with_age(self, hours):
        when = (datetime.datetime.now(datetime.timezone.utc)
                - datetime.timedelta(hours=hours))
        return {"generated_at": when.isoformat(timespec="seconds")}

    def test_fresh_snapshot_is_not_stale(self):
        snap = self._snapshot_with_age(hours=3)
        self.assertFalse(synthesis_cache.is_stale(snap))

    def test_old_snapshot_is_stale(self):
        snap = self._snapshot_with_age(hours=72)
        self.assertTrue(synthesis_cache.is_stale(snap))

    def test_missing_timestamp_is_treated_as_stale(self):
        # Safer to nudge the operator to regenerate than to silently hide
        # a missing timestamp; the UI surfaces this as "consider refreshing".
        self.assertTrue(synthesis_cache.is_stale({}))
        self.assertTrue(synthesis_cache.is_stale({"generated_at": ""}))
        self.assertTrue(
            synthesis_cache.is_stale({"generated_at": "not-a-date"})
        )

    def test_threshold_is_overridable(self):
        snap = self._snapshot_with_age(hours=10)
        self.assertFalse(synthesis_cache.is_stale(snap, max_age_hours=24))
        self.assertTrue(synthesis_cache.is_stale(snap, max_age_hours=6))


class SaveLoadRoundTripTest(unittest.TestCase):
    """Snapshots round-trip through the JSON file format unchanged."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self._orig_subdir = synthesis_cache.SNAPSHOT_SUBDIR
        synthesis_cache.SNAPSHOT_SUBDIR = self.tmp / "syntheses"

    def tearDown(self):
        synthesis_cache.SNAPSHOT_SUBDIR = self._orig_subdir

    def test_save_then_load_returns_equal_object(self):
        snap = synthesis_cache.make_snapshot(corpus_documents=10)
        synthesis_cache.set_section(
            snap, "hype", text="hello", window_days=1, documents_used=5
        )
        path = synthesis_cache.save_snapshot(snap)
        self.assertTrue(path.exists())
        loaded = synthesis_cache.load_snapshot(path)
        self.assertEqual(loaded, snap)

    def test_load_returns_none_for_missing_file(self):
        bogus = self.tmp / "nope.json"
        self.assertIsNone(synthesis_cache.load_snapshot(bogus))

    def test_load_returns_none_for_malformed_file(self):
        # A truncated or hand-edited snapshot should never crash the
        # request path; load_snapshot returns None and the caller falls
        # back to "no snapshot available".
        path = synthesis_cache.SNAPSHOT_SUBDIR
        path.mkdir(parents=True, exist_ok=True)
        bad = path / "garbage.json"
        bad.write_text("{not real json", encoding="utf-8")
        self.assertIsNone(synthesis_cache.load_snapshot(bad))

    def test_latest_snapshot_picks_most_recent_by_date(self):
        # ISO date filenames sort lexically == chronologically, so
        # latest_snapshot_path() should pick the most recent date even
        # if older files were touched more recently on disk.
        for date in ("2026-04-01", "2026-05-21", "2026-05-23", "2026-05-22"):
            snap = synthesis_cache.make_snapshot()
            snap["date"] = date
            path = synthesis_cache.snapshot_path(
                datetime.date.fromisoformat(date)
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(snap), encoding="utf-8")
        latest_path = synthesis_cache.latest_snapshot_path()
        self.assertEqual(latest_path.name, "2026-05-23.json")
        latest, _ = synthesis_cache.latest_snapshot()
        self.assertEqual(latest["date"], "2026-05-23")

    def test_atomic_write_replaces_existing_file(self):
        # Saving twice for the same date overwrites the prior snapshot
        # in place — used by ?refresh=1.
        first = synthesis_cache.make_snapshot()
        synthesis_cache.set_section(first, "hype", text="version 1")
        synthesis_cache.save_snapshot(first)
        second = synthesis_cache.make_snapshot()
        synthesis_cache.set_section(second, "hype", text="version 2")
        synthesis_cache.save_snapshot(second)
        loaded, _ = synthesis_cache.latest_snapshot()
        self.assertEqual(loaded["hype"]["text"], "version 2")


if __name__ == "__main__":
    unittest.main()
