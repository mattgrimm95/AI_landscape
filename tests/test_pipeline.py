import os
import tempfile
import unittest

from ailandscape import config, pipeline, reconcile, scraper
from ailandscape.storage_kg import KnowledgeGraphStore
from ailandscape.storage_raw import RawLogStore

SAMPLE = config.ROOT / "samples" / "sample_feed.xml"


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.raw = RawLogStore(os.path.join(self.tmp, "raw.db"))
        self.kg = KnowledgeGraphStore(os.path.join(self.tmp, "kg.db"))

    def tearDown(self):
        self.raw.close()
        self.kg.close()

    def test_end_to_end_on_sample_feed(self):
        articles = scraper.scrape_fixture(SAMPLE, "Sample Feed")
        ingest = pipeline.ingest_articles(articles, self.raw, ner_backend="rule")
        self.assertEqual(ingest["new_documents"], 4)
        self.assertEqual(ingest["skipped"], 0)
        self.assertGreater(ingest["entities"], 0)

        graph = reconcile.reconcile(self.raw, self.kg)
        self.assertEqual(graph["documents"], 4)
        self.assertGreater(graph["nodes"], 5)
        self.assertGreater(graph["edges"], 0)

        # Known gazetteer entities must be present in the graph.
        for alias in ("pentagon", "lockheed martin", "f-35", "china", "ukraine"):
            self.assertIsNotNone(
                self.kg.node_by_alias(alias), "missing node: %s" % alias
            )

        # Pentagon appears in three sample articles -> should be a hub.
        pentagon = self.kg.node_by_alias("pentagon")
        self.assertGreaterEqual(pentagon["document_count"], 3)

    def test_duplicate_ingest_is_skipped(self):
        articles = scraper.scrape_fixture(SAMPLE, "Sample Feed")
        pipeline.ingest_articles(articles, self.raw, ner_backend="rule")
        second = pipeline.ingest_articles(articles, self.raw, ner_backend="rule")
        self.assertEqual(second["new_documents"], 0)
        self.assertEqual(second["skipped"], 4)
        self.assertEqual(self.raw.count_documents(), 4)


if __name__ == "__main__":
    unittest.main()
