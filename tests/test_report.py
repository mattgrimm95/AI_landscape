import os
import tempfile
import unittest

from ailandscape import report
from ailandscape.storage_kg import KnowledgeGraphStore
from ailandscape.storage_ner import NEROutputLog


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ner = NEROutputLog(os.path.join(self.tmp, "ner.db"))
        self.kg = KnowledgeGraphStore(os.path.join(self.tmp, "kg.db"))

    def tearDown(self):
        self.ner.close()
        self.kg.close()

    def _seed(self):
        self.ner.add_entities(
            "h1",
            [
                {"text": "China", "label": "place"},
                {"text": "Pentagon", "label": "organization"},
            ],
        )
        china = self.kg.insert_node(
            "China", "place", mention_count=5, document_count=3
        )
        pentagon = self.kg.insert_node(
            "Pentagon", "organization", mention_count=2, document_count=2
        )
        self.kg.insert_node(
            "Hegseth", "person", mention_count=1, document_count=1
        )
        self.kg.insert_node(
            "Pete Hegseth", "person", mention_count=4, document_count=2
        )
        self.kg.insert_edge(china, pentagon, "co_occurs_with", 3)
        self.kg.commit()

    def test_build_overview_funnel_and_breakdowns(self):
        self._seed()
        docs = [{"fetched_at": "2026-05-22T00:00:00+00:00"}]
        overview = report.build_overview(docs, self.ner, self.kg)
        self.assertEqual(overview["funnel"]["documents"], 1)
        self.assertEqual(overview["funnel"]["raw_mentions"], 2)
        self.assertEqual(overview["funnel"]["nodes"], 4)
        self.assertEqual(overview["funnel"]["edges"], 1)
        entity_types = {name: count for name, count, _ in overview["entity_types"]}
        self.assertEqual(entity_types["person"], 2)

    def test_overview_flags_quality_issues(self):
        self._seed()
        overview = report.build_overview([], self.ner, self.kg)
        # "Hegseth" is single-mention and a partial name of "Pete Hegseth".
        self.assertEqual(overview["quality"]["singletons"], 1)
        self.assertEqual(overview["quality"]["partial_name_dups"], 1)
        self.assertEqual(
            overview["quality"]["examples"][0], ("Hegseth", "Pete Hegseth")
        )

    def test_render_overview_produces_readable_text(self):
        self._seed()
        text = report.render_overview(
            report.build_overview(
                [{"fetched_at": "2026-05-22T00:00:00+00:00"}], self.ner, self.kg
            )
        )
        for heading in (
            "AI LANDSCAPE",
            "PIPELINE FUNNEL",
            "SCRAPE STATUS",
            "ENTITY TYPES",
            "DATA QUALITY",
        ):
            self.assertIn(heading, text)

    def test_overview_handles_empty_data(self):
        overview = report.build_overview([], self.ner, self.kg)
        self.assertEqual(overview["funnel"]["nodes"], 0)
        self.assertFalse(overview["scrape"]["within_24h"])
        self.assertIn("AI LANDSCAPE", report.render_overview(overview))


if __name__ == "__main__":
    unittest.main()
