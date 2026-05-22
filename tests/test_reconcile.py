import os
import tempfile
import unittest

from ailandscape import reconcile
from ailandscape.storage_kg import KnowledgeGraphStore
from ailandscape.storage_ner import NEROutputLog


class ReconcileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ner = NEROutputLog(os.path.join(self.tmp, "ner.db"))
        self.kg = KnowledgeGraphStore(os.path.join(self.tmp, "kg.db"))
        self.documents = []

    def tearDown(self):
        self.ner.close()
        self.kg.close()

    def _add_doc(self, content_hash, entities):
        self.documents.append(
            {"content_hash": content_hash, "fetched_at": "2026-05-21T00:00:00+00:00"}
        )
        self.ner.add_entities(content_hash, entities)

    def _seed(self):
        self._add_doc(
            "h1",
            [
                {"text": "China", "label": "place"},
                {"text": "Pentagon", "label": "organization"},
            ],
        )
        self._add_doc(
            "h2",
            [
                {"text": "china", "label": "place"},  # dedupes with "China"
                {"text": "Taiwan", "label": "place"},
            ],
        )

    def test_normalize_collapses_wording_variants(self):
        n = reconcile.normalize
        self.assertEqual(n("  The Pentagon!  "), "pentagon")
        self.assertEqual(n("Pentagon's"), n("Pentagon"))   # possessive
        self.assertEqual(n("U.S."), n("US"))               # acronym dots
        self.assertEqual(n("U.S. Navy"), "us navy")
        self.assertEqual(n("drone swarms"), n("drone swarm"))  # plural
        self.assertEqual(n("F-35s"), n("F-35"))

    def test_normalize_strips_leading_article(self):
        n = reconcile.normalize
        # A leading article ("the"/"a"/"an") is an NER span artifact, so the
        # article variant must share a dedup key with the bare form.
        self.assertEqual(
            n("a National Science Foundation"),
            n("National Science Foundation"),
        )
        self.assertEqual(n("an Institute for Data"), n("Institute for Data"))
        self.assertEqual(
            n("the Naval Surface Warfare Center"),
            n("Naval Surface Warfare Center"),
        )
        # A word that merely begins with those letters is left intact.
        self.assertEqual(n("Antarctica"), "antarctica")
        self.assertEqual(n("Apple"), "apple")
        self.assertEqual(n("Andorra"), "andorra")

    def test_leading_article_merges_node_and_cleans_display(self):
        # NER sometimes swallows a leading article into the entity span.
        # The article variant is seen first, so a stale display name would
        # surface here as "a National Science Foundation".
        self._add_doc(
            "h1",
            [{"text": "a National Science Foundation", "label": "organization"}],
        )
        self._add_doc(
            "h2",
            [{"text": "National Science Foundation", "label": "organization"}],
        )
        summary = reconcile.reconcile(self.documents, self.ner, self.kg)
        # The article variant collapses onto the bare-form node...
        self.assertEqual(summary["nodes"], 1)
        node = self.kg.node_by_alias("national science foundation")
        self.assertIsNotNone(node)
        self.assertEqual(node["mention_count"], 2)
        self.assertEqual(node["document_count"], 2)
        # ...and the display name carries no leading-article artifact.
        self.assertEqual(node["canonical_name"], "National Science Foundation")

    def test_dedup_and_edges(self):
        self._seed()
        summary = reconcile.reconcile(self.documents, self.ner, self.kg)
        self.assertEqual(summary["documents"], 2)
        self.assertEqual(summary["nodes"], 3)  # China, Pentagon, Taiwan
        self.assertEqual(summary["edges"], 2)
        china = self.kg.node_by_alias("china")
        self.assertEqual(china["mention_count"], 2)
        self.assertEqual(china["document_count"], 2)

    def test_rebuild_is_idempotent(self):
        self._seed()
        reconcile.reconcile(self.documents, self.ner, self.kg)
        reconcile.reconcile(self.documents, self.ner, self.kg)
        self.assertEqual(self.kg.count_nodes(), 3)
        self.assertEqual(self.kg.node_by_alias("china")["mention_count"], 2)

    def test_noise_is_ignored(self):
        self._add_doc(
            "h",
            [
                {"text": "Officials", "label": "misc"},
                {"text": "China", "label": "place"},
            ],
        )
        reconcile.reconcile(self.documents, self.ner, self.kg)
        self.assertIsNone(self.kg.node_by_alias("officials"))
        self.assertIsNotNone(self.kg.node_by_alias("china"))

    def test_corrections_merge(self):
        self._add_doc(
            "h",
            [
                {"text": "Lockheed", "label": "organization"},
                {"text": "Lockheed Martin", "label": "organization"},
            ],
        )
        corrections = ({reconcile.normalize("Lockheed"): "Lockheed Martin"}, set())
        reconcile.reconcile(
            self.documents, self.ner, self.kg, corrections=corrections
        )
        node = self.kg.node_by_alias("lockheed")
        self.assertIsNotNone(node)
        self.assertEqual(node["canonical_name"], "Lockheed Martin")
        # Both surface forms collapse onto a single node.
        self.assertEqual(self.kg.count_nodes(), 1)

    def test_wording_variants_merge_node_and_edge(self):
        # Same entities, slightly different wording across two documents.
        self._add_doc(
            "h1",
            [
                {"text": "Drone Swarms", "label": "concept"},
                {"text": "Pentagon", "label": "organization"},
            ],
        )
        self._add_doc(
            "h2",
            [
                {"text": "Drone Swarm", "label": "concept"},
                {"text": "Pentagon's", "label": "organization"},
            ],
        )
        summary = reconcile.reconcile(self.documents, self.ner, self.kg)
        # "Drone Swarms"/"Drone Swarm" and "Pentagon"/"Pentagon's" each
        # collapse to a single node...
        self.assertEqual(summary["nodes"], 2)
        # ...and the relationship between them is one edge of weight 2,
        # not two separate edges.
        self.assertEqual(summary["edges"], 1)
        self.assertEqual(self.kg.edges()[0]["weight"], 2)

    def test_person_coreference_merges_partial_name(self):
        self._add_doc("h1", [{"text": "Pete Hegseth", "label": "person"}])
        self._add_doc("h2", [{"text": "Hegseth", "label": "person"}])
        summary = reconcile.reconcile(self.documents, self.ner, self.kg)
        # "Hegseth" folds into "Pete Hegseth" — one node, mentions combined.
        self.assertEqual(summary["nodes"], 1)
        node = self.kg.node_by_alias("hegseth")
        self.assertIsNotNone(node)
        self.assertEqual(node["canonical_name"], "Pete Hegseth")
        self.assertEqual(node["mention_count"], 2)
        self.assertEqual(node["document_count"], 2)

    def test_coreference_leaves_ambiguous_surnames_alone(self):
        self._add_doc(
            "h1",
            [
                {"text": "Pete Hegseth", "label": "person"},
                {"text": "Jane Hegseth", "label": "person"},
            ],
        )
        self._add_doc("h2", [{"text": "Hegseth", "label": "person"}])
        summary = reconcile.reconcile(self.documents, self.ner, self.kg)
        # Two people share the surname, so bare "Hegseth" stays its own node.
        self.assertEqual(summary["nodes"], 3)


if __name__ == "__main__":
    unittest.main()
