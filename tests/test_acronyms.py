"""Acronym ↔ expansion extraction tests.

The extractor mines apposition patterns in article text. These tests cover
the recognized patterns, the initials-verification guard against false
positives, and the cross-document aggregation gate.
"""

import unittest

from ailandscape import acronyms


class ExtractPairsTest(unittest.TestCase):
    def test_expansion_paren_pattern(self):
        text = (
            "The Defense Advanced Research Projects Agency (DARPA) funded "
            "the program."
        )
        pairs = acronyms.extract_pairs(text)
        self.assertIn(
            ("DARPA", "Defense Advanced Research Projects Agency"), pairs
        )

    def test_acronym_paren_pattern(self):
        text = "DARPA (Defense Advanced Research Projects Agency) funded it."
        pairs = acronyms.extract_pairs(text)
        self.assertIn(
            ("DARPA", "Defense Advanced Research Projects Agency"), pairs
        )

    def test_lowercase_expansion_with_skipwords(self):
        # ISR = intelligence, surveillance and reconnaissance — lowercase
        # words and a connector ("and") skipped from initials.
        text = "intelligence, surveillance and reconnaissance (ISR) data."
        pairs = acronyms.extract_pairs(text)
        acronyms_only = [a for a, _ in pairs]
        self.assertIn("ISR", acronyms_only)

    def test_skipwords_excluded_from_initials(self):
        # "Department of Defense" → DOD, NOT DOOD (skip "of").
        text = "Department of Defense (DOD) announced the contract."
        pairs = acronyms.extract_pairs(text)
        self.assertIn(("DOD", "Department of Defense"), pairs)

    def test_false_positive_initials_mismatch_rejected(self):
        # "Lockheed Martin (XYZ)" — XYZ is not Lockheed Martin's initials.
        # The extractor must reject this as an accidental adjacency.
        text = "Lockheed Martin (XYZ) developed the system."
        pairs = acronyms.extract_pairs(text)
        self.assertEqual(pairs, [])

    def test_unrelated_parenthetical_rejected(self):
        # A parenthetical aside is not always an acronym definition.
        text = (
            "The Pentagon (a Defense Department building) hosted the meeting."
        )
        pairs = acronyms.extract_pairs(text)
        # "Pentagon" matches "P", not "ADDB" — rejected.
        self.assertEqual(pairs, [])

    def test_empty_text(self):
        self.assertEqual(acronyms.extract_pairs(""), [])
        self.assertEqual(acronyms.extract_pairs(None), [])


class AggregateTest(unittest.TestCase):
    def test_gated_by_min_doc_freq(self):
        # An acronym mapping from a single document is suggestive but not
        # corroborated — should not surface as a suggestion.
        per_doc = [
            [("DARPA", "Defense Advanced Research Projects Agency")],
        ]
        out = acronyms.aggregate(per_doc)
        self.assertEqual(out, [])

    def test_corroborated_across_two_docs_surfaces(self):
        per_doc = [
            [("DARPA", "Defense Advanced Research Projects Agency")],
            [("DARPA", "Defense Advanced Research Projects Agency")],
        ]
        out = acronyms.aggregate(per_doc)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["acronym"], "DARPA")
        self.assertEqual(out[0]["documents"], 2)

    def test_conflicting_expansions_kept_separate(self):
        # The same acronym with different expansions should NOT merge —
        # acronyms can collide, and the curator must disambiguate.
        per_doc = [
            [("DOD", "Department of Defense")],
            [("DOD", "Department of Defense")],
            [("DOD", "Day of Discharge")],
            [("DOD", "Day of Discharge")],
        ]
        out = acronyms.aggregate(per_doc)
        acronyms_kept = {(e["acronym"], e["expansion"]) for e in out}
        self.assertIn(("DOD", "Department of Defense"), acronyms_kept)
        self.assertIn(("DOD", "Day of Discharge"), acronyms_kept)

    def test_duplicate_pairs_in_one_doc_count_once(self):
        # If an article uses the same definitional apposition twice, it
        # still counts as one doc — the gate is documents, not occurrences.
        per_doc = [
            [
                ("DARPA", "Defense Advanced Research Projects Agency"),
                ("DARPA", "Defense Advanced Research Projects Agency"),
            ],
        ]
        out = acronyms.aggregate(per_doc)
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
