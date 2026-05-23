import json
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

    def test_node_documents_are_persisted(self):
        self._seed()
        reconcile.reconcile(self.documents, self.ner, self.kg)
        china = self.kg.node_by_alias("china")
        # China is mentioned in both h1 and h2; Taiwan only in h2.
        self.assertEqual(
            set(self.kg.documents_for_node(china["id"])), {"h1", "h2"}
        )
        taiwan = self.kg.node_by_alias("taiwan")
        self.assertEqual(self.kg.documents_for_node(taiwan["id"]), ["h2"])

    def test_co_occurrence_edge_carries_strength(self):
        self._seed()
        reconcile.reconcile(self.documents, self.ner, self.kg)
        cooc = [
            e for e in self.kg.edges() if e["relation"] == "co_occurs_with"
        ]
        self.assertTrue(cooc)
        strength = json.loads(cooc[0]["metadata"])["strength"]
        self.assertGreater(strength, 0)
        self.assertLessEqual(strength, 1.0)

    def test_node_dates_use_published_not_fetched(self):
        # The document was fetched in May 2026 but published in Jan 2024;
        # the node's first/last seen must reflect when the news happened.
        self.documents.append({
            "content_hash": "h1",
            "fetched_at": "2026-05-21T00:00:00+00:00",
            "published": "Wed, 10 Jan 2024 12:00:00 +0000",
        })
        self.ner.add_entities("h1", [{"text": "China", "label": "place"}])
        reconcile.reconcile(self.documents, self.ner, self.kg)
        china = self.kg.node_by_alias("china")
        self.assertEqual(china["first_seen"], "2024-01-10")
        self.assertEqual(china["last_seen"], "2024-01-10")

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

    def test_typed_edge_stores_confidence_from_weight(self):
        text = "Lockheed Martin builds the F-35 fighter."
        offset = 2
        self.documents.append({
            "content_hash": "h1",
            "fetched_at": "2026-05-21T00:00:00+00:00",
            "title": "",
            "raw_text": text,
        })
        self.ner.add_entities("h1", [
            {"text": "Lockheed Martin", "label": "organization",
             "start": offset + 0, "end": offset + 15},
            {"text": "F-35", "label": "product",
             "start": offset + 27, "end": offset + 31},
        ])
        reconcile.reconcile(self.documents, self.ner, self.kg)
        typed = [e for e in self.kg.edges() if e["relation"] == "develops"][0]
        meta = json.loads(typed["metadata"])
        # Confidence rises with weight: 1 occurrence -> 0.5.
        self.assertEqual(meta["confidence"], 0.5)

    def test_typed_edge_stores_evidence(self):
        text = "Lockheed Martin builds the F-35 fighter."
        # corpus.document_text prepends ". " to raw_text, so entity offsets
        # are shifted by two characters.
        offset = 2
        self.documents.append({
            "content_hash": "h1",
            "fetched_at": "2026-05-21T00:00:00+00:00",
            "title": "",
            "raw_text": text,
        })
        self.ner.add_entities("h1", [
            {"text": "Lockheed Martin", "label": "organization",
             "start": offset + 0, "end": offset + 15},
            {"text": "F-35", "label": "product",
             "start": offset + 27, "end": offset + 31},
        ])
        reconcile.reconcile(self.documents, self.ner, self.kg)
        typed = [e for e in self.kg.edges() if e["relation"] == "develops"]
        self.assertEqual(len(typed), 1)
        meta = json.loads(typed[0]["metadata"])
        self.assertIn("Lockheed Martin builds the F-35", meta["evidence"])
        self.assertEqual(meta["source"], "h1")

    def test_coreference_leaves_ambiguous_surnames_alone(self):
        self._add_doc(
            "h1",
            [
                {"text": "Pete Hegseth", "label": "person"},
                {"text": "Jane Hegseth", "label": "person"},
            ],
        )
        self._add_doc("h2", [{"text": "Hegseth", "label": "person"}])
        # A third document with the bare surname keeps it above the
        # weak-single-word prune so coreference is what is under test here.
        self._add_doc("h3", [{"text": "Hegseth", "label": "person"}])
        summary = reconcile.reconcile(self.documents, self.ner, self.kg)
        # Two people share the surname, so bare "Hegseth" stays its own node.
        self.assertEqual(summary["nodes"], 3)

    def test_organization_coreference_merges_partial_name(self):
        self._add_doc(
            "h1", [{"text": "Lockheed Martin", "label": "organization"}]
        )
        self._add_doc("h2", [{"text": "Lockheed", "label": "organization"}])
        summary = reconcile.reconcile(self.documents, self.ner, self.kg)
        # "Lockheed" folds into "Lockheed Martin" on the shared first word.
        self.assertEqual(summary["nodes"], 1)
        node = self.kg.node_by_alias("lockheed")
        self.assertEqual(node["canonical_name"], "Lockheed Martin")
        self.assertEqual(node["mention_count"], 2)

    def test_org_coreference_leaves_ambiguous_first_word_alone(self):
        self._add_doc(
            "h1",
            [
                {"text": "General Dynamics", "label": "organization"},
                {"text": "General Atomics", "label": "organization"},
            ],
        )
        self._add_doc("h2", [{"text": "General", "label": "organization"}])
        # Bare "General" is mentioned in two docs so it survives the
        # weak-single-word prune; coreference is what is under test here.
        self._add_doc("h3", [{"text": "General", "label": "organization"}])
        summary = reconcile.reconcile(self.documents, self.ner, self.kg)
        # Two orgs share the first word, so bare "General" stays its own node.
        self.assertEqual(summary["nodes"], 3)

    def test_single_word_non_gazetteer_single_doc_is_pruned(self):
        # A capitalized common noun typed by NER as a real entity, but only
        # ever mentioned in one document — the prune drops it.
        self._add_doc("h1", [{"text": "Designs", "label": "group"}])
        summary = reconcile.reconcile(self.documents, self.ner, self.kg)
        self.assertEqual(summary["nodes"], 0)

    def test_lowercase_single_word_entity_is_pruned(self):
        # Two documents reference "kin" as an org; even with doc_freq=2 a
        # lowercase canonical fails the proper-noun shape check.
        self._add_doc("h1", [{"text": "kin", "label": "organization"}])
        self._add_doc("h2", [{"text": "kin", "label": "organization"}])
        summary = reconcile.reconcile(self.documents, self.ner, self.kg)
        self.assertEqual(summary["nodes"], 0)

    def test_attribute_extraction_strips_email_boilerplate(self):
        # An academic-page glitch concatenated a name with contact / email
        # boilerplate; the clean name + an email attribute should result.
        self._add_doc("h1", [{
            "text": ("Chelsea Finn Contact : tianheyu@cs.stanford.edu "
                     "Links: Paper"),
            "label": "person",
        }])
        self._add_doc("h2", [{"text": "Chelsea Finn", "label": "person"}])
        reconcile.reconcile(self.documents, self.ner, self.kg)
        node = self.kg.node_by_alias("chelsea finn")
        self.assertIsNotNone(node)
        self.assertEqual(node["canonical_name"], "Chelsea Finn")
        attrs = json.loads(node["metadata"]).get("attributes", {})
        self.assertEqual(attrs.get("email"), "tianheyu@cs.stanford.edu")

    def test_gazetteer_entity_kept_even_in_one_document(self):
        # Pentagon is a gazetteer canonical, so a single-doc mention survives
        # the new prune.
        self._add_doc("h1", [{"text": "Pentagon", "label": "organization"}])
        summary = reconcile.reconcile(self.documents, self.ner, self.kg)
        self.assertEqual(summary["nodes"], 1)
        self.assertIsNotNone(self.kg.node_by_alias("pentagon"))


if __name__ == "__main__":
    unittest.main()
