import json
import os
import tempfile
import unittest

from ailandscape import review
from ailandscape.storage_kg import KnowledgeGraphStore


class ReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.kg = KnowledgeGraphStore(os.path.join(self.tmp, "kg.db"))
        # Place "Africa" is the last word of exactly one multi-word place node:
        # a single-candidate match, so it should be a suggested merge.
        self.kg.insert_node("Africa", "place")
        self.kg.insert_node("Horn of Africa", "place")
        # "Acme" has no multi-word counterpart, so it stays untouched.
        self.kg.insert_node("Acme", "organization")
        self.kg.commit()

    def tearDown(self):
        self.kg.close()

    def test_build_review_finds_partial_name_dups(self):
        data = review.build_review([], self.kg)
        names = {(s["from"], s["into"]) for s in data["merge_suggestions"]}
        self.assertIn(("Africa", "Horn of Africa"), names)
        self.assertFalse(any(s["from"] == "Acme" for s in data["merge_suggestions"]))

    def test_save_review_accumulates_without_overwriting(self):
        path = os.path.join(self.tmp, "review.json")
        first = review.save_review(review.build_review([], self.kg), path)
        # A second run finds the same suggestion; it is not added again.
        second = review.save_review(review.build_review([], self.kg), path)
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        store = json.loads(open(path, encoding="utf-8").read())
        self.assertEqual(len(store["suggested_merges"]), 1)
        # The history accumulates one entry per run.
        self.assertEqual(len(store["history"]), 2)

    def test_save_review_preserves_manual_entries(self):
        path = os.path.join(self.tmp, "review.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "suggested_merges": [
                    {"from": "Manual", "into": "Manual Entry",
                     "type": "organization"}
                ],
                "history": [],
            }))
        review.save_review(review.build_review([], self.kg), path)
        store = json.loads(open(path, encoding="utf-8").read())
        froms = {s["from"] for s in store["suggested_merges"]}
        # The manually-added entry is kept; the new auto-finding is added.
        self.assertIn("Manual", froms)
        self.assertIn("Africa", froms)

    def test_render_review_produces_text(self):
        text = review.render_review(review.build_review([], self.kg))
        self.assertIn("AI LANDSCAPE - QUALITY REVIEW", text)
        self.assertIn("Africa", text)


if __name__ == "__main__":
    unittest.main()
