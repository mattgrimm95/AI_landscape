import datetime
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


class SpikeDetectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.kg = KnowledgeGraphStore(os.path.join(self.tmp, "kg.db"))

    def tearDown(self):
        self.kg.close()

    def _seed_doc(self, content_hash, days_ago):
        # Synthesize a document whose `published` lands `days_ago` days
        # before today, so the spike calc has predictable input.
        date = (datetime.date.today() -
                datetime.timedelta(days=days_ago)).isoformat()
        return {"content_hash": content_hash, "published": date}

    def test_long_lived_entity_with_recent_surge_spikes(self):
        # An entity with ~30 historical mentions spread over 2 years that
        # suddenly gets 15 in the last 30 days. Long-term baseline is
        # ~1.2/month, recent is 15, ratio ~12 -> spike fires.
        node_id = self.kg.insert_node("LongLived", "organization",
                                      first_seen="2024-01-01")
        docs = []
        # 25 historical mentions over 700+ days (well past min_active_days)
        for i in range(25):
            h = "old%d" % i
            docs.append(self._seed_doc(h, days_ago=400 + i * 10))
            self.kg.insert_node_documents(node_id, [h])
        # 15 recent mentions inside the 30-day window
        for i in range(15):
            h = "new%d" % i
            docs.append(self._seed_doc(h, days_ago=i))
            self.kg.insert_node_documents(node_id, [h])
        self.kg.commit()
        spikes = trends.build_spikes(docs, self.kg)
        names = [s["name"] for s in spikes]
        self.assertIn("LongLived", names)

    def test_brand_new_entity_does_not_spike(self):
        # An entity that only existed for 20 days isn't "spiking" — it's
        # brand new. The "newly appeared" channel handles those; build_spikes
        # must skip them so the spike list stays meaningful.
        node_id = self.kg.insert_node("Newcomer", "organization",
                                      first_seen=str(datetime.date.today() -
                                                     datetime.timedelta(days=15)))
        docs = []
        for i in range(8):
            h = "n%d" % i
            docs.append(self._seed_doc(h, days_ago=i))
            self.kg.insert_node_documents(node_id, [h])
        self.kg.commit()
        spikes = trends.build_spikes(docs, self.kg)
        self.assertEqual(
            [s for s in spikes if s["name"] == "Newcomer"], []
        )

    def test_misc_entities_are_excluded(self):
        # Common capitalized words ("First", "However") that NER captures
        # as `misc` must never appear in the spike list — they would
        # dominate it otherwise.
        node_id = self.kg.insert_node("However", "misc",
                                      first_seen="2024-01-01")
        docs = []
        for i in range(50):
            h = "h%d" % i
            docs.append(self._seed_doc(h, days_ago=i if i < 15 else 200 + i))
            self.kg.insert_node_documents(node_id, [h])
        self.kg.commit()
        spikes = trends.build_spikes(docs, self.kg)
        self.assertEqual(
            [s for s in spikes if s["name"] == "However"], []
        )

    def test_below_min_recent_does_not_spike(self):
        # Recent count must clear the absolute floor — a node going from
        # 0 to 1 isn't a spike even if the ratio is technically infinite.
        node_id = self.kg.insert_node("Quiet", "organization",
                                      first_seen="2024-01-01")
        docs = []
        for i in range(20):
            h = "q%d" % i
            docs.append(self._seed_doc(h, days_ago=200 + i))
            self.kg.insert_node_documents(node_id, [h])
        # Only 2 recent mentions — below min_recent=5.
        for i in range(2):
            h = "qr%d" % i
            docs.append(self._seed_doc(h, days_ago=i))
            self.kg.insert_node_documents(node_id, [h])
        self.kg.commit()
        spikes = trends.build_spikes(docs, self.kg)
        self.assertEqual(
            [s for s in spikes if s["name"] == "Quiet"], []
        )


class TrajectoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.kg = KnowledgeGraphStore(os.path.join(self.tmp, "kg.db"))

    def tearDown(self):
        self.kg.close()

    def test_months_returned_in_chronological_order(self):
        data = trends.build_trajectory([], self.kg, months=6)
        months = [m["month"] for m in data["months"]]
        self.assertEqual(months, sorted(months))
        self.assertEqual(len(months), 6)

    def test_documents_bucketed_by_published_month(self):
        today = datetime.date.today()
        # Pick two months that we know are inside the 6-month window.
        recent_month = today.isoformat()[:7]
        prev = today.replace(day=1) - datetime.timedelta(days=1)
        prev_month = prev.isoformat()[:7]
        docs = [
            {"published": today.isoformat(), "title": "today"},
            {"published": today.isoformat(), "title": "today2"},
            {"published": prev.isoformat(), "title": "prev"},
        ]
        data = trends.build_trajectory(docs, self.kg, months=6)
        bucket = {m["month"]: m for m in data["months"]}
        self.assertEqual(bucket[recent_month]["documents"], 2)
        self.assertEqual(bucket[prev_month]["documents"], 1)

    def test_new_entities_bucketed_by_first_seen(self):
        today = datetime.date.today()
        first = today.isoformat()
        self.kg.insert_node(
            "JustAdded", "organization",
            mention_count=2, first_seen=first, last_seen=first,
        )
        self.kg.commit()
        data = trends.build_trajectory([], self.kg, months=3)
        bucket = {m["month"]: m for m in data["months"]}
        self.assertEqual(bucket[today.isoformat()[:7]]["new_entities"], 1)
        self.assertIn(
            "JustAdded", bucket[today.isoformat()[:7]]["new_entity_names"]
        )


if __name__ == "__main__":
    unittest.main()
