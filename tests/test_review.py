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
        # Person "Hegseth" is the last word of exactly one multi-word person
        # node — a single-candidate match the review should suggest as a
        # merge. The compound-with-bare guard does NOT trigger because
        # "Pete Hegseth" has no qualifier prefix and no "of" before Hegseth.
        self.kg.insert_node("Hegseth", "person")
        self.kg.insert_node("Pete Hegseth", "person")
        # "Acme" has no multi-word counterpart, so it stays untouched.
        self.kg.insert_node("Acme", "organization")
        self.kg.commit()

    def tearDown(self):
        self.kg.close()

    def test_build_review_finds_partial_name_dups(self):
        data = review.build_review([], self.kg)
        names = {(s["from"], s["into"]) for s in data["merge_suggestions"]}
        self.assertIn(("Hegseth", "Pete Hegseth"), names)
        self.assertFalse(any(s["from"] == "Acme" for s in data["merge_suggestions"]))

    def test_save_review_accumulates_without_overwriting(self):
        path = os.path.join(self.tmp, "review.json")
        first = review.save_review(review.build_review([], self.kg), path)
        # A second run finds the same suggestion; it is not added again.
        second = review.save_review(review.build_review([], self.kg), path)
        self.assertEqual(first["merges"], 1)
        self.assertEqual(second["merges"], 0)
        store = json.loads(open(path, encoding="utf-8").read())
        self.assertEqual(len(store["suggested_merges"]), 1)
        # The history accumulates one entry per run.
        self.assertEqual(len(store["history"]), 2)

    def test_review_flags_structural_noise(self):
        # URL fragments, @-handles, too-short tokens, all-digit strings, and
        # likely version tags ("Block 2") are flagged; digit-prefixed
        # military unit names and short military designations ("Mk 1") are
        # NOT (they are legitimate proper nouns).
        self.kg.insert_node("https://t.co/abc", "organization")
        self.kg.insert_node("@PGSA_IRAN", "organization")
        self.kg.insert_node("aa", "organization")
        self.kg.insert_node("1234", "organization")
        self.kg.insert_node("Block 2", "product")
        self.kg.insert_node("1st Cavalry Division", "organization")
        self.kg.insert_node("Mk 1", "product")
        self.kg.commit()
        data = review.build_review([], self.kg)
        names = {s["name"] for s in data["noise_suggestions"]}
        self.assertIn("https://t.co/abc", names)
        self.assertIn("@PGSA_IRAN", names)
        self.assertIn("aa", names)
        self.assertIn("1234", names)
        self.assertIn("Block 2", names)
        self.assertNotIn("1st Cavalry Division", names)
        self.assertNotIn("Mk 1", names)

    def test_noise_filter_respects_gazetteer(self):
        # Gazetteer-trusted canonicals that happen to match the version-tag
        # shape ("Gemma 4", "Genie 3", "Lyria 3", "Zone 5") must not be
        # flagged as noise — the gazetteer is the curator-of-record, and
        # this holds even when their corpus presence is thin (doc_freq=1).
        for name in ("Gemma 4", "Genie 3", "Lyria 3", "Zone 5"):
            self.kg.insert_node(name, "product", document_count=1)
        self.kg.insert_node("Block 2", "product", document_count=1)
        self.kg.commit()
        data = review.build_review([], self.kg)
        names = {s["name"] for s in data["noise_suggestions"]}
        self.assertNotIn("Gemma 4", names)
        self.assertNotIn("Genie 3", names)
        self.assertNotIn("Lyria 3", names)
        self.assertNotIn("Zone 5", names)
        self.assertIn("Block 2", names)

    def test_noise_filter_respects_doc_frequency(self):
        # A version-tag-shaped name that the gazetteer has not yet curated
        # can still escape the noise flag on the strength of corpus evidence
        # alone: two or more independent documents using the same string is
        # the principled signal that it's a real product, not boilerplate.
        # "Aster 30" (real MBDA missile) and an unknown "Foobar 7" both
        # cross the doc-frequency floor and must survive; the same shape
        # confined to one document ("Block 2") is still flagged.
        self.kg.insert_node("Aster 30", "product", document_count=2)
        self.kg.insert_node("Foobar 7", "product", document_count=3)
        self.kg.insert_node("Block 2", "product", document_count=1)
        self.kg.commit()
        data = review.build_review([], self.kg)
        names = {s["name"] for s in data["noise_suggestions"]}
        self.assertNotIn("Aster 30", names)
        self.assertNotIn("Foobar 7", names)
        self.assertIn("Block 2", names)

    def test_review_does_not_merge_compound_with_bare(self):
        # The bare name appears AFTER an "of" or behind a qualifier
        # ("Gulf", "South", "Broad ... of") in the compound — those are
        # distinct entities, not a longer form of the bare name.
        self.kg.insert_node("Oman", "place")
        self.kg.insert_node("Gulf of Oman", "place")
        self.kg.insert_node("Lebanon", "place")
        self.kg.insert_node("South Lebanon", "place")
        self.kg.insert_node("MIT", "organization")
        self.kg.insert_node("Broad Institute of MIT", "organization")
        self.kg.commit()
        data = review.build_review([], self.kg)
        pairs = {(s["from"], s["into"]) for s in data["merge_suggestions"]}
        self.assertNotIn(("Oman", "Gulf of Oman"), pairs)
        self.assertNotIn(("Lebanon", "South Lebanon"), pairs)
        self.assertNotIn(("MIT", "Broad Institute of MIT"), pairs)

    def test_save_review_accumulates_noise(self):
        path = os.path.join(self.tmp, "review.json")
        self.kg.insert_node("https://t.co/abc", "organization")
        self.kg.commit()
        result = review.save_review(review.build_review([], self.kg), path)
        self.assertGreaterEqual(result["ignores"], 1)
        store = json.loads(open(path, encoding="utf-8").read())
        self.assertTrue(any(
            s["name"] == "https://t.co/abc"
            for s in store["suggested_ignores"]
        ))

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
        self.assertIn("Hegseth", froms)

    def test_render_review_produces_text(self):
        text = review.render_review(review.build_review([], self.kg))
        self.assertIn("AI LANDSCAPE - QUALITY REVIEW", text)
        self.assertIn("Hegseth", text)


class GazetteerCandidatesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.kg = KnowledgeGraphStore(os.path.join(self.tmp, "kg.db"))

    def tearDown(self):
        self.kg.close()

    def test_high_freq_misc_nodes_become_candidates(self):
        # A multi-word misc node mentioned across many docs should surface
        # as a gazetteer-add candidate.
        self.kg.insert_node(
            "Defense Tech Alliance", "misc",
            mention_count=10, document_count=4,
        )
        # A multi-word *typed* node — already categorized; not a candidate.
        self.kg.insert_node(
            "Lockheed Martin", "organization",
            mention_count=20, document_count=8,
        )
        # A single-word misc node — too prone to common-noun noise.
        self.kg.insert_node(
            "Alliance", "misc",
            mention_count=15, document_count=6,
        )
        # Below mention threshold.
        self.kg.insert_node(
            "Sparse Mention Group", "misc",
            mention_count=2, document_count=3,
        )
        self.kg.commit()
        data = review.build_review([], self.kg)
        names = [c["name"] for c in data["gazetteer_candidates"]]
        self.assertIn("Defense Tech Alliance", names)
        self.assertNotIn("Lockheed Martin", names)
        self.assertNotIn("Alliance", names)
        self.assertNotIn("Sparse Mention Group", names)

    def test_existing_gazetteer_name_not_resurfaced(self):
        # "Pentagon" is in the gazetteer; even if it shows up as `misc`
        # in the graph (theoretical edge case), it should not be promoted
        # as a candidate.
        self.kg.insert_node(
            "Pentagon", "misc",
            mention_count=50, document_count=20,
        )
        self.kg.commit()
        data = review.build_review([], self.kg)
        names = [c["name"] for c in data["gazetteer_candidates"]]
        self.assertNotIn("Pentagon", names)

    def test_acronym_suggestions_surface_with_corroboration(self):
        # Two documents define the same acronym ↔ expansion — corroborated,
        # should surface. A third with a different mapping for the same
        # acronym should also surface (as a separate suggestion).
        docs = [
            {"raw_text": (
                "The Defense Advanced Research Projects Agency (DARPA) "
                "announced the program."
            )},
            {"raw_text": (
                "Officials at the Defense Advanced Research Projects Agency "
                "(DARPA) confirmed."
            )},
        ]
        data = review.build_review(docs, self.kg)
        acros = {(a["acronym"], a["expansion"]) for a in data["acronym_suggestions"]}
        self.assertIn(
            ("DARPA", "Defense Advanced Research Projects Agency"), acros,
        )

    def test_save_review_carries_acronym_suggestions(self):
        docs = [
            {"raw_text": "The Department of Defense (DOD) said so."},
            {"raw_text": "Officials at the Department of Defense (DOD)."},
        ]
        path = os.path.join(self.tmp, "review.json")
        review.save_review(review.build_review(docs, self.kg), path)
        store = json.loads(open(path, encoding="utf-8").read())
        acros = {(a["acronym"], a["expansion"]) for a in store["acronym_suggestions"]}
        self.assertIn(("DOD", "Department of Defense"), acros)

    def test_gazetteer_candidates_refreshed_not_accumulated(self):
        # First run produces a candidate. Then we drop it below threshold
        # and run again — the candidate should be gone, not lingering.
        self.kg.insert_node(
            "Trending Topic", "misc",
            mention_count=10, document_count=4,
        )
        self.kg.commit()
        path = os.path.join(self.tmp, "review.json")
        review.save_review(review.build_review([], self.kg), path)
        store_1 = json.loads(open(path, encoding="utf-8").read())
        self.assertEqual(
            [c["name"] for c in store_1["gazetteer_candidates"]],
            ["Trending Topic"],
        )
        # Now downgrade — same node, but only one mention left.
        self.kg.conn.execute("UPDATE nodes SET mention_count = 1")
        self.kg.commit()
        review.save_review(review.build_review([], self.kg), path)
        store_2 = json.loads(open(path, encoding="utf-8").read())
        # The stale candidate is gone.
        self.assertEqual(store_2["gazetteer_candidates"], [])


if __name__ == "__main__":
    unittest.main()
