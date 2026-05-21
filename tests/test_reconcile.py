import os
import tempfile
import unittest

from ailandscape import reconcile
from ailandscape.storage_kg import KnowledgeGraphStore
from ailandscape.storage_raw import RawLogStore


class ReconcileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.raw = RawLogStore(os.path.join(self.tmp, "raw.db"))
        self.kg = KnowledgeGraphStore(os.path.join(self.tmp, "kg.db"))

    def tearDown(self):
        self.raw.close()
        self.kg.close()

    def _seed(self):
        doc1, _ = self.raw.add_document({"source": "S", "title": "A"}, "h1")
        self.raw.add_entities(
            doc1,
            [
                {"text": "China", "label": "place"},
                {"text": "Pentagon", "label": "organization"},
            ],
        )
        doc2, _ = self.raw.add_document({"source": "S", "title": "B"}, "h2")
        self.raw.add_entities(
            doc2,
            [
                {"text": "china", "label": "place"},  # dedupes with "China"
                {"text": "Taiwan", "label": "place"},
            ],
        )

    def test_normalize(self):
        self.assertEqual(reconcile.normalize("  The Pentagon!  "), "pentagon")
        self.assertEqual(reconcile.normalize("U.S. Navy"), "u s navy")

    def test_dedup_and_edges(self):
        self._seed()
        summary = reconcile.reconcile(self.raw, self.kg)
        self.assertEqual(summary["documents"], 2)
        self.assertEqual(summary["nodes"], 3)  # China, Pentagon, Taiwan
        self.assertEqual(summary["edges"], 2)
        china = self.kg.node_by_alias("china")
        self.assertEqual(china["mention_count"], 2)
        self.assertEqual(china["document_count"], 2)

    def test_rebuild_is_idempotent(self):
        self._seed()
        reconcile.reconcile(self.raw, self.kg)
        reconcile.reconcile(self.raw, self.kg)
        self.assertEqual(self.kg.count_nodes(), 3)
        self.assertEqual(self.kg.node_by_alias("china")["mention_count"], 2)

    def test_noise_is_ignored(self):
        doc, _ = self.raw.add_document({"source": "S"}, "h")
        self.raw.add_entities(
            doc,
            [
                {"text": "Officials", "label": "misc"},
                {"text": "China", "label": "place"},
            ],
        )
        reconcile.reconcile(self.raw, self.kg)
        self.assertIsNone(self.kg.node_by_alias("officials"))
        self.assertIsNotNone(self.kg.node_by_alias("china"))

    def test_corrections_merge(self):
        doc, _ = self.raw.add_document({"source": "S"}, "h")
        self.raw.add_entities(
            doc,
            [
                {"text": "Lockheed", "label": "organization"},
                {"text": "Lockheed Martin", "label": "organization"},
            ],
        )
        corrections = ({reconcile.normalize("Lockheed"): "Lockheed Martin"}, set())
        reconcile.reconcile(self.raw, self.kg, corrections=corrections)
        node = self.kg.node_by_alias("lockheed")
        self.assertIsNotNone(node)
        self.assertEqual(node["canonical_name"], "Lockheed Martin")
        # Both surface forms collapse onto a single node.
        self.assertEqual(self.kg.count_nodes(), 1)


if __name__ == "__main__":
    unittest.main()
