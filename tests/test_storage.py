import os
import tempfile
import unittest

from ailandscape.storage_kg import KnowledgeGraphStore
from ailandscape.storage_ner import NEROutputLog


class NEROutputLogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = NEROutputLog(os.path.join(self.tmp, "ner.db"))

    def tearDown(self):
        self.store.close()

    def test_entities_are_appended(self):
        self.store.add_entities(
            "h1",
            [
                {"text": "China", "label": "place", "start": 0, "end": 5},
                {"text": "Pentagon", "label": "organization", "start": 6, "end": 14},
            ],
        )
        self.assertEqual(self.store.count_entities(), 2)
        entities = self.store.entities_for("h1")
        self.assertEqual(entities[0]["text"], "China")
        self.assertEqual(entities[1]["label"], "organization")

    def test_entities_for_filters_by_content_hash(self):
        self.store.add_entities("h1", [{"text": "China", "label": "place"}])
        self.store.add_entities("h2", [{"text": "Taiwan", "label": "place"}])
        self.assertEqual(len(self.store.entities_for("h1")), 1)
        self.assertEqual(self.store.entities_for("h2")[0]["text"], "Taiwan")
        self.assertEqual(self.store.count_entities(), 2)

    def test_clear_resets_entities_and_ids(self):
        self.store.add_entities("h", [{"text": "X", "label": "misc"}])
        self.store.clear()
        self.assertEqual(self.store.count_entities(), 0)
        # Ids restart from 1 after a clear, so a rebuild is reproducible.
        self.store.add_entities("h", [{"text": "Y", "label": "misc"}])
        self.assertEqual(self.store.all_entities()[0]["id"], 1)


class KnowledgeGraphStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.kg = KnowledgeGraphStore(os.path.join(self.tmp, "kg.db"))

    def tearDown(self):
        self.kg.close()

    def test_insert_and_query(self):
        china = self.kg.insert_node("China", "place", mention_count=3)
        pentagon = self.kg.insert_node("Pentagon", "organization", mention_count=5)
        self.kg.insert_alias(china, "china")
        self.kg.insert_edge(china, pentagon, "co_occurs_with", 2)
        self.kg.commit()
        self.assertEqual(self.kg.count_nodes(), 2)
        self.assertEqual(self.kg.count_edges(), 1)
        self.assertEqual(
            self.kg.node_by_alias("china")["canonical_name"], "China"
        )
        self.assertEqual(
            self.kg.top_nodes(1)[0]["canonical_name"], "Pentagon"
        )

    def test_clear_removes_all_data(self):
        self.kg.insert_node("X", "misc")
        self.kg.commit()
        self.kg.clear()
        self.assertEqual(self.kg.count_nodes(), 0)
        self.assertEqual(self.kg.count_edges(), 0)


if __name__ == "__main__":
    unittest.main()
