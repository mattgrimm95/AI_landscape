import datetime
import os
import tempfile
import unittest

from ailandscape import briefing
from ailandscape.storage_kg import KnowledgeGraphStore


class BriefingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.kg = KnowledgeGraphStore(os.path.join(self.tmp, "kg.db"))
        pentagon = self.kg.insert_node(
            "Pentagon", "organization", mention_count=20
        )
        anduril = self.kg.insert_node(
            "Anduril", "organization", mention_count=8
        )
        ai = self.kg.insert_node(
            "Artificial Intelligence", "concept", mention_count=40
        )
        self.kg.insert_edge(
            pentagon, anduril, "awards_contract", 3,
            metadata={"evidence": "the Pentagon awarded Anduril a contract",
                      "source": "h1"},
        )
        self.kg.insert_edge(pentagon, ai, "co_occurs_with", 5)
        self.kg.commit()
        self.now = datetime.datetime(
            2026, 5, 22, tzinfo=datetime.timezone.utc
        )
        self.docs = [
            {"title": "Recent doc", "source": "Feed A", "url": "u1",
             "fetched_at": "2026-05-21T00:00:00+00:00"},
            {"title": "Old doc", "source": "Feed B", "url": "u2",
             "fetched_at": "2026-01-01T00:00:00+00:00"},
        ]

    def tearDown(self):
        self.kg.close()

    def test_briefing_structure_and_window(self):
        b = briefing.build_briefing(self.docs, self.kg, days=7, now=self.now)
        self.assertEqual(b["totals"]["documents"], 2)
        self.assertEqual(b["totals"]["entities"], 3)
        self.assertEqual(b["totals"]["typed_relations"], 1)
        # Only the recent document falls inside the 7-day window.
        self.assertEqual(b["recent_count"], 1)
        self.assertEqual(b["recent_documents"][0]["title"], "Recent doc")

    def test_contract_awards_and_trending_topics(self):
        b = briefing.build_briefing(self.docs, self.kg, days=7, now=self.now)
        self.assertEqual(len(b["contract_awards"]), 1)
        award = b["contract_awards"][0]
        self.assertEqual(award["subject"], "Pentagon")
        self.assertEqual(award["object"], "Anduril")
        self.assertIn("awarded Anduril", award["evidence"])
        # Trending topics are concept-type nodes.
        self.assertEqual(
            b["trending_topics"][0]["name"], "Artificial Intelligence"
        )

    def test_sbir_funding_totals(self):
        docs = self.docs + [
            {"title": "Award 1", "source": "SBIR.gov", "url": "s1",
             "fetched_at": "2026-05-20T00:00:00+00:00",
             "metadata": {"data_source": "SBIR", "award_amount": 1000000.0}},
            {"title": "Award 2", "source": "SBIR.gov", "url": "s2",
             "fetched_at": "2026-05-20T00:00:00+00:00",
             "metadata": {"data_source": "SBIR", "award_amount": 500000.0}},
        ]
        b = briefing.build_briefing(docs, self.kg, days=7, now=self.now)
        self.assertEqual(b["sbir_funding"]["awards"], 2)
        self.assertEqual(b["sbir_funding"]["total_amount"], 1500000.0)

    def test_render_briefing_produces_text(self):
        b = briefing.build_briefing(self.docs, self.kg, days=7, now=self.now)
        text = briefing.render_briefing(b)
        self.assertIn("AI LANDSCAPE - BRIEFING", text)
        self.assertIn("Anduril", text)


class SubfieldBriefingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.kg = KnowledgeGraphStore(os.path.join(self.tmp, "kg.db"))
        llm = self.kg.insert_node(
            "Large Language Models", "concept", mention_count=50,
        )
        ai = self.kg.insert_node(
            "Artificial Intelligence", "concept", mention_count=80,
        )
        anthropic = self.kg.insert_node(
            "Anthropic", "organization", mention_count=20,
        )
        anduril = self.kg.insert_node(
            "Anduril", "organization", mention_count=15,
        )
        # Anthropic co-occurs with the subfield concept; Anduril does not.
        self.kg.insert_edge(anthropic, llm, "co_occurs_with", 8)
        self.kg.insert_edge(anduril, ai, "co_occurs_with", 6)
        self.kg.commit()
        self.now = datetime.datetime(
            2026, 5, 22, tzinfo=datetime.timezone.utc
        )
        self.docs = [
            {"title": "About LLMs", "source": "TechCrunch",
             "raw_text": "Large language models reshape industries.",
             "fetched_at": "2026-05-20T00:00:00+00:00"},
            {"title": "Drone deal", "source": "Breaking Defense",
             "raw_text": "Anduril delivers more drones.",
             "fetched_at": "2026-05-21T00:00:00+00:00"},
        ]

    def tearDown(self):
        self.kg.close()

    def test_subfield_scopes_entities_and_documents(self):
        b = briefing.build_briefing(
            self.docs, self.kg, days=7, now=self.now,
            subfield_concepts=["Large Language Models"],
        )
        # Anduril doesn't touch the subfield → out of scope.
        names = {e["name"] for e in b["top_entities"]}
        self.assertIn("Large Language Models", names)
        self.assertIn("Anthropic", names)
        self.assertNotIn("Anduril", names)
        # Only the doc mentioning the subfield concept is in scope.
        titles = {d["title"] for d in b["recent_documents"]}
        self.assertIn("About LLMs", titles)
        self.assertNotIn("Drone deal", titles)


if __name__ == "__main__":
    unittest.main()
