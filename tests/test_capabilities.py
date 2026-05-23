"""Capability subfield index tests.

`capabilities.build_capabilities` groups gazetteer concept nodes into the
hand-curated subfields in `gazetteer.SUBFIELDS` and computes the leading
orgs co-occurring with each subfield's concepts. The shape that powers the
Capabilities modal in the web UI.
"""

import os
import tempfile
import unittest

from ailandscape import capabilities, gazetteer
from ailandscape.storage_kg import KnowledgeGraphStore


class BuildCapabilitiesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.kg = KnowledgeGraphStore(os.path.join(self.tmp, "kg.db"))

    def tearDown(self):
        self.kg.close()

    def test_returns_card_per_subfield(self):
        cards = capabilities.build_capabilities([], [])
        self.assertEqual(len(cards), len(gazetteer.SUBFIELDS))
        labels = [c["label"] for c in cards]
        self.assertEqual(
            labels, [s["label"] for s in gazetteer.SUBFIELDS]
        )

    def test_concepts_resolved_from_live_nodes(self):
        # Insert two concept nodes that belong to the foundation-models
        # subfield and one that doesn't. The card should pick up the two.
        llm_id = self.kg.insert_node(
            "Large Language Models", "concept",
            mention_count=50, document_count=20,
        )
        gen_id = self.kg.insert_node(
            "Generative AI", "concept",
            mention_count=30, document_count=12,
        )
        self.kg.insert_node(
            "Drone Swarm", "concept",
            mention_count=8, document_count=4,
        )
        self.kg.commit()
        nodes = self.kg.nodes()
        cards = capabilities.build_capabilities(nodes, [])
        foundation = next(c for c in cards if c["id"] == "foundation_models")
        names = {c["name"] for c in foundation["concepts"]}
        self.assertEqual(names, {"Large Language Models", "Generative AI"})
        # Sorted by mentions: LLM first.
        self.assertEqual(
            foundation["concepts"][0]["name"], "Large Language Models"
        )
        self.assertEqual(foundation["concept_count"], 2)
        self.assertEqual(foundation["mentions"], 80)

    def test_top_organizations_ranked_by_cooccurrence(self):
        # Two concepts, three orgs. Anthropic co-occurs heavily with LLM
        # and Foundation Models; OpenAI only with LLM; an unrelated org
        # only with Drone Swarm. The card should rank Anthropic > OpenAI
        # and not include the unrelated org.
        llm = self.kg.insert_node(
            "Large Language Models", "concept", mention_count=50,
        )
        fm = self.kg.insert_node(
            "Foundation Models", "concept", mention_count=30,
        )
        drone = self.kg.insert_node(
            "Drone Swarm", "concept", mention_count=10,
        )
        anthropic = self.kg.insert_node(
            "Anthropic", "organization", mention_count=40,
        )
        openai = self.kg.insert_node(
            "OpenAI", "organization", mention_count=25,
        )
        anduril = self.kg.insert_node(
            "Anduril", "organization", mention_count=10,
        )
        self.kg.insert_edge(anthropic, llm, "co_occurs_with", weight=10)
        self.kg.insert_edge(anthropic, fm, "co_occurs_with", weight=6)
        self.kg.insert_edge(openai, llm, "co_occurs_with", weight=4)
        self.kg.insert_edge(anduril, drone, "co_occurs_with", weight=8)
        self.kg.commit()
        nodes = self.kg.nodes()
        edges = self.kg.edges()
        cards = capabilities.build_capabilities(nodes, edges)
        foundation = next(c for c in cards if c["id"] == "foundation_models")
        org_names = [o["name"] for o in foundation["top_organizations"]]
        self.assertEqual(org_names[0], "Anthropic")
        self.assertIn("OpenAI", org_names)
        self.assertNotIn("Anduril", org_names)

    def test_subfield_concept_names_lookup(self):
        names = capabilities.subfield_concept_names("foundation_models")
        self.assertIn("Large Language Models", names)
        self.assertNotIn("Drone Swarm", names)
        self.assertEqual(capabilities.subfield_concept_names("nope"), [])


class GazetteerSubfieldTest(unittest.TestCase):
    def test_subfields_cover_no_overlap(self):
        # Each concept name should belong to at most one subfield (the
        # taxonomy is a partition, not overlapping tags).
        seen = {}
        for subfield in gazetteer.SUBFIELDS:
            for name in subfield["concepts"]:
                self.assertNotIn(
                    name, seen,
                    "%r appears in both %r and %r" % (
                        name, seen.get(name), subfield["id"]
                    ),
                )
                seen[name] = subfield["id"]

    def test_subfield_for_concept_lookup(self):
        self.assertEqual(
            gazetteer.subfield_for_concept("Large Language Models"),
            "foundation_models",
        )
        self.assertEqual(
            gazetteer.subfield_for_concept("Computer Vision"), "perception"
        )
        self.assertIsNone(
            gazetteer.subfield_for_concept("Some Random Thing")
        )


if __name__ == "__main__":
    unittest.main()
