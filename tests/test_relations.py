import unittest

from ailandscape import relations


def _ent(text, label, start, end):
    return {"text": text, "label": label, "start": start, "end": end}


def _triples(rels):
    """Drop the evidence snippet, leaving (subject, relation, object)."""
    return [r[:3] for r in rels]


class RelationsTest(unittest.TestCase):
    def test_develops_relation(self):
        text = "Lockheed Martin builds the F-35 fighter."
        rels = relations.extract_relations(
            text,
            [
                _ent("Lockheed Martin", "organization", 0, 15),
                _ent("F-35", "product", 27, 31),
            ],
        )
        self.assertIn(("Lockheed Martin", "develops", "F-35"), _triples(rels))

    def test_awards_contract_relation(self):
        text = "The Pentagon awarded Anduril a major contract."
        rels = relations.extract_relations(
            text,
            [
                _ent("Pentagon", "organization", 4, 12),
                _ent("Anduril", "organization", 21, 28),
            ],
        )
        self.assertIn(("Pentagon", "awards_contract", "Anduril"), _triples(rels))

    def test_relation_carries_evidence_snippet(self):
        # Every typed relation carries the source text it was read from.
        text = "Lockheed Martin builds the F-35 fighter."
        rels = relations.extract_relations(
            text,
            [
                _ent("Lockheed Martin", "organization", 0, 15),
                _ent("F-35", "product", 27, 31),
            ],
        )
        self.assertEqual(len(rels), 1)
        subj, relation, obj, evidence = rels[0]
        self.assertEqual(
            (subj, relation, obj), ("Lockheed Martin", "develops", "F-35")
        )
        self.assertIn("Lockheed Martin builds the F-35", evidence)

    def test_type_constraint_rejects_bad_subject(self):
        # "develops" requires an organization subject — a place is rejected.
        text = "China builds the J-20 jet."
        rels = relations.extract_relations(
            text,
            [
                _ent("China", "place", 0, 5),
                _ent("J-20", "product", 17, 21),
            ],
        )
        self.assertEqual(rels, [])

    def test_sentence_break_blocks_relation(self):
        text = "The Pentagon spoke. Anduril builds drones."
        rels = relations.extract_relations(
            text,
            [
                _ent("Pentagon", "organization", 4, 12),
                _ent("Anduril", "organization", 20, 27),
            ],
        )
        self.assertEqual(rels, [])

    def test_distant_entities_are_not_related(self):
        text = "Pentagon " + ("x" * 100) + " awarded Anduril."
        anduril_at = text.index("Anduril")
        rels = relations.extract_relations(
            text,
            [
                _ent("Pentagon", "organization", 0, 8),
                _ent("Anduril", "organization", anduril_at, anduril_at + 7),
            ],
        )
        self.assertEqual(rels, [])

    def test_entities_without_offsets_yield_nothing(self):
        rels = relations.extract_relations(
            "Anything here.",
            [{"text": "Pentagon", "label": "organization"}],
        )
        self.assertEqual(rels, [])

    def test_start_char_offsets_are_accepted(self):
        # NER-log rows use start_char/end_char rather than start/end.
        text = "The Pentagon awarded Anduril a contract."
        rels = relations.extract_relations(
            text,
            [
                {"text": "Pentagon", "label": "organization",
                 "start_char": 4, "end_char": 12},
                {"text": "Anduril", "label": "organization",
                 "start_char": 21, "end_char": 28},
            ],
        )
        self.assertIn(("Pentagon", "awards_contract", "Anduril"), _triples(rels))

    def test_passive_voice_flips_direction(self):
        # "X was awarded ... by Y" means Y is the awarding subject.
        text = "Anduril was awarded a contract by the Pentagon."
        pentagon_at = text.index("Pentagon")
        rels = relations.extract_relations(
            text,
            [
                _ent("Anduril", "organization", 0, 7),
                _ent("Pentagon", "organization", pentagon_at, pentagon_at + 8),
            ],
        )
        self.assertIn(("Pentagon", "awards_contract", "Anduril"), _triples(rels))
        self.assertNotIn(
            ("Anduril", "awards_contract", "Pentagon"), _triples(rels)
        )

    def test_passive_develops_flips_direction(self):
        text = "The F-35 was built by Lockheed Martin."
        lockheed_at = text.index("Lockheed Martin")
        rels = relations.extract_relations(
            text,
            [
                _ent("F-35", "product", 4, 8),
                _ent("Lockheed Martin", "organization",
                     lockheed_at, lockheed_at + 15),
            ],
        )
        self.assertIn(("Lockheed Martin", "develops", "F-35"), _triples(rels))

    def test_located_in_relation(self):
        text = "Anduril is headquartered in California."
        california_at = text.index("California")
        rels = relations.extract_relations(
            text,
            [
                _ent("Anduril", "organization", 0, 7),
                _ent("California", "place", california_at, california_at + 10),
            ],
        )
        self.assertIn(("Anduril", "located_in", "California"), _triples(rels))


if __name__ == "__main__":
    unittest.main()
