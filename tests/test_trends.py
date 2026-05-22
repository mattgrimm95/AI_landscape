import os
import tempfile
import unittest

from ailandscape import trends
from ailandscape.storage_kg import KnowledgeGraphStore


class TrendsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.kg = KnowledgeGraphStore(os.path.join(self.tmp, "kg.db"))
        self.kg.insert_node(
            "Old Entity", "organization", mention_count=3,
            first_seen="2024-01-05", last_seen="2024-02-01",
        )
        self.kg.insert_node(
            "New Entity", "organization", mention_count=9,
            first_seen="2026-05-01", last_seen="2026-05-20",
        )
        self.kg.commit()
        self.docs = [
            {"published": "2026-05-10", "title": "a"},
            {"published": "2026-05-12", "title": "b"},
            {"published": "2024-01-05", "title": "c"},
        ]

    def tearDown(self):
        self.kg.close()

    def test_document_volume_by_month(self):
        data = trends.build_trends(self.docs, self.kg)
        volume = {v["month"]: v["count"] for v in data["document_volume"]}
        self.assertEqual(volume["2026-05"], 2)
        self.assertEqual(volume["2024-01"], 1)

    def test_new_and_recent_entities_ordering(self):
        data = trends.build_trends(self.docs, self.kg)
        # The most recent first_seen leads the "new entities" list.
        self.assertEqual(data["new_entities"][0]["name"], "New Entity")
        # The most recent last_seen leads the "recent entities" list.
        self.assertEqual(data["recent_entities"][0]["name"], "New Entity")

    def test_render_trends_produces_text(self):
        text = trends.render_trends(trends.build_trends(self.docs, self.kg))
        self.assertIn("AI LANDSCAPE - TRENDS", text)
        self.assertIn("New Entity", text)


if __name__ == "__main__":
    unittest.main()
