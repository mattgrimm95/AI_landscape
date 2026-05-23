"""Golden-snapshot regression guard for the rebuild pipeline.

`test_pipeline.test_rebuild_is_deterministic` proves the pipeline is
deterministic — given the same corpus, two runs produce byte-identical
outputs. This test goes a step further: it checks that the pipeline still
produces a *specific known-good* output. If a heuristic change (NER
stopword tweak, reconcile prune adjustment, gazetteer growth) silently
drops a real entity or invents new merges, the counts diverge and the test
fails loudly.

The bundled `samples/sample_feed.xml` (4 articles, 17 expected nodes) is
the fixed input. When you intentionally adjust a heuristic, expect this
test to fail — re-run with the printed actuals to update the constants
below, and commit the new golden alongside the heuristic change so the
diff is visible in code review.

NOTE: the golden runs the ``rule`` backend so it doesn't depend on spaCy
or any optional model download.
"""

import os
import tempfile
import unittest

from ailandscape import config, corpus, pipeline, scraper
from ailandscape.storage_kg import KnowledgeGraphStore
from ailandscape.storage_ner import NEROutputLog


SAMPLE = config.ROOT / "samples" / "sample_feed.xml"

# --- the golden ---
# Counts (4-article sample, rule backend).
GOLDEN_DOCUMENTS = 4
GOLDEN_NER_ENTITIES = 37
GOLDEN_NODES = 17
GOLDEN_EDGES = 44
GOLDEN_TYPED_RELATIONS = 0  # sample feed has no in-sentence cue triples

# Specific canonical names that must always appear. These are
# gazetteer-trusted entities the sample feed mentions explicitly; if any
# vanishes, the change broke something fundamental.
REQUIRED_NODES = {
    "Pentagon", "Anduril", "Lockheed Martin", "F-35",
    "China", "Ukraine", "Russia", "Taiwan", "United States",
    "DARPA", "NATO", "U.S. Air Force",
    "Artificial Intelligence", "Hypersonic Weapons", "HIMARS",
    "People's Liberation Army", "Boeing",
}


class GoldenSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.corpus_path = os.path.join(self.tmp, "documents.jsonl")
        for article in scraper.scrape_fixture(SAMPLE, "Sample Feed"):
            corpus.append(self.corpus_path, pipeline.make_record(article))

    def test_rebuild_matches_golden_counts(self):
        ner_log = NEROutputLog(os.path.join(self.tmp, "ner.db"))
        kg = KnowledgeGraphStore(os.path.join(self.tmp, "kg.db"))
        try:
            result = pipeline.rebuild(
                self.corpus_path, ner_log, kg, ner_backend="rule"
            )
            self.assertEqual(
                result["documents"], GOLDEN_DOCUMENTS,
                "document count drifted",
            )
            self.assertEqual(
                result["entities"], GOLDEN_NER_ENTITIES,
                "NER entity count drifted — heuristic change suspected",
            )
            self.assertEqual(
                result["graph"]["nodes"], GOLDEN_NODES,
                "graph node count drifted",
            )
            self.assertEqual(
                result["graph"]["edges"], GOLDEN_EDGES,
                "graph edge count drifted",
            )
            self.assertEqual(
                result["graph"]["typed_relations"], GOLDEN_TYPED_RELATIONS,
                "typed-relation count drifted",
            )
            names = {n["canonical_name"] for n in kg.nodes()}
            missing = REQUIRED_NODES - names
            self.assertEqual(
                missing, set(),
                "required entities missing from the graph: %r" % sorted(missing),
            )
        finally:
            ner_log.close()
            kg.close()


if __name__ == "__main__":
    unittest.main()
