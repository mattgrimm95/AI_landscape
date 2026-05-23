"""Bulk-apply suggestions from review.json into corrections.json.

The `correct-from-review` CLI command walks the accumulating review store
and folds approved merge / ignore suggestions into the version-controlled
corrections.json. These tests cover the file-shuffling logic with
``--no-rebuild`` set, so they don't pull in SQLite or the rebuild path —
the goal is to verify the curator workflow, not the rebuild.
"""

import argparse
import json
import os
import pathlib
import tempfile
import unittest

from ailandscape import cli, config


class CorrectFromReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.review_path = pathlib.Path(self.tmp) / "review.json"
        self.corrections_path = pathlib.Path(self.tmp) / "corrections.json"
        # Redirect both files to the temp directory.
        self._orig_review = config.REVIEW_FILE
        self._orig_corrections = cli.CORRECTIONS_FILE
        config.REVIEW_FILE = self.review_path
        cli.CORRECTIONS_FILE = self.corrections_path

    def tearDown(self):
        config.REVIEW_FILE = self._orig_review
        cli.CORRECTIONS_FILE = self._orig_corrections

    def _seed_review(self, merges=(), ignores=()):
        store = {
            "suggested_merges": list(merges),
            "suggested_ignores": list(ignores),
            "history": [],
        }
        self.review_path.write_text(json.dumps(store), encoding="utf-8")

    def _args(self, **overrides):
        defaults = dict(
            merges=False, ignores=False, acronyms=False, yes=True,
            no_rebuild=True, ner=None,
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_missing_review_file_returns_error(self):
        rc = cli.cmd_correct_from_review(self._args(merges=True, ignores=True))
        self.assertEqual(rc, 1)

    def test_nothing_to_apply_when_no_flag_chosen(self):
        self._seed_review(merges=[{"from": "A", "into": "Acme"}])
        rc = cli.cmd_correct_from_review(self._args())
        self.assertEqual(rc, 0)
        # corrections.json was never written.
        self.assertFalse(self.corrections_path.exists())

    def test_applies_merges_with_yes_flag(self):
        self._seed_review(
            merges=[
                {"from": "Bare", "into": "Bare Full Name", "type": "person"},
                {"from": "Other", "into": "Other Long", "type": "organization"},
            ],
        )
        rc = cli.cmd_correct_from_review(
            self._args(merges=True, yes=True, no_rebuild=True)
        )
        self.assertEqual(rc, 0)
        data = json.loads(self.corrections_path.read_text(encoding="utf-8"))
        self.assertEqual(data["merge"]["Bare"], "Bare Full Name")
        self.assertEqual(data["merge"]["Other"], "Other Long")

    def test_applies_ignores_with_yes_flag(self):
        self._seed_review(
            ignores=[
                {"name": "Block 2", "type": "product", "reason": "version tag"},
                {"name": "Subscribe Newsletter", "type": "misc",
                 "reason": "boilerplate"},
            ],
        )
        cli.cmd_correct_from_review(
            self._args(ignores=True, yes=True, no_rebuild=True)
        )
        data = json.loads(self.corrections_path.read_text(encoding="utf-8"))
        self.assertIn("Block 2", data["ignore"])
        self.assertIn("Subscribe Newsletter", data["ignore"])

    def test_existing_corrections_are_preserved(self):
        # Pre-existing merge / ignore entries must survive a bulk apply.
        self.corrections_path.write_text(
            json.dumps({"merge": {"X": "X Full"}, "ignore": ["already-ignored"]}),
            encoding="utf-8",
        )
        self._seed_review(
            merges=[{"from": "New", "into": "New Full"}],
            ignores=[{"name": "new-ignore"}],
        )
        cli.cmd_correct_from_review(
            self._args(merges=True, ignores=True, yes=True, no_rebuild=True)
        )
        data = json.loads(self.corrections_path.read_text(encoding="utf-8"))
        # Old entries are still there.
        self.assertEqual(data["merge"]["X"], "X Full")
        self.assertIn("already-ignored", data["ignore"])
        # New entries are applied.
        self.assertEqual(data["merge"]["New"], "New Full")
        self.assertIn("new-ignore", data["ignore"])

    def test_applies_corroborated_acronyms(self):
        store = {
            "suggested_merges": [],
            "suggested_ignores": [],
            "acronym_suggestions": [
                {
                    "acronym": "DARPA",
                    "expansion": "Defense Advanced Research Projects Agency",
                    "documents": 5,
                },
                {
                    "acronym": "ISR",
                    "expansion": "intelligence, surveillance and reconnaissance",
                    "documents": 3,
                },
            ],
            "history": [],
        }
        self.review_path.write_text(json.dumps(store), encoding="utf-8")
        rc = cli.cmd_correct_from_review(
            self._args(acronyms=True, yes=True, no_rebuild=True)
        )
        self.assertEqual(rc, 0)
        data = json.loads(self.corrections_path.read_text(encoding="utf-8"))
        self.assertEqual(
            data["merge"]["DARPA"], "Defense Advanced Research Projects Agency"
        )
        self.assertEqual(
            data["merge"]["ISR"], "intelligence, surveillance and reconnaissance"
        )

    def test_skips_existing_merge_keys(self):
        # If a merge surface is already in corrections.json, the review
        # suggestion should not overwrite it (the curator's manual decision
        # wins over the auto-surfaced one).
        self.corrections_path.write_text(
            json.dumps({"merge": {"Same": "First Choice"}, "ignore": []}),
            encoding="utf-8",
        )
        self._seed_review(
            merges=[{"from": "Same", "into": "Second Choice"}],
        )
        cli.cmd_correct_from_review(
            self._args(merges=True, yes=True, no_rebuild=True)
        )
        data = json.loads(self.corrections_path.read_text(encoding="utf-8"))
        self.assertEqual(data["merge"]["Same"], "First Choice")


if __name__ == "__main__":
    unittest.main()
