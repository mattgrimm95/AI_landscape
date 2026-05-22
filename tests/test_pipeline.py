import os
import tempfile
import unittest

from ailandscape import config, corpus, pipeline, scraper
from ailandscape.storage_kg import KnowledgeGraphStore
from ailandscape.storage_raw import RawLogStore

SAMPLE = config.ROOT / "samples" / "sample_feed.xml"


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.corpus_path = os.path.join(self.tmp, "documents.jsonl")
        for article in scraper.scrape_fixture(SAMPLE, "Sample Feed"):
            corpus.append(self.corpus_path, pipeline.make_record(article))

    def _stores(self, tag):
        return (
            RawLogStore(os.path.join(self.tmp, tag + "_raw.db")),
            KnowledgeGraphStore(os.path.join(self.tmp, tag + "_kg.db")),
        )

    def test_rebuild_from_corpus(self):
        raw, kg = self._stores("a")
        try:
            result = pipeline.rebuild(
                self.corpus_path, raw, kg, ner_backend="rule"
            )
            self.assertEqual(result["documents"], 4)
            self.assertGreater(result["graph"]["nodes"], 5)
            self.assertGreater(result["graph"]["edges"], 0)
            for alias in ("pentagon", "lockheed martin", "f-35", "china", "ukraine"):
                self.assertIsNotNone(
                    kg.node_by_alias(alias), "missing node: %s" % alias
                )
        finally:
            raw.close()
            kg.close()

    def test_rebuild_is_deterministic(self):
        # The same corpus must produce byte-identical graph data every time.
        raw1, kg1 = self._stores("d1")
        raw2, kg2 = self._stores("d2")
        try:
            pipeline.rebuild(self.corpus_path, raw1, kg1, ner_backend="rule")
            pipeline.rebuild(self.corpus_path, raw2, kg2, ner_backend="rule")
            self.assertEqual(kg1.nodes(), kg2.nodes())
            self.assertEqual(kg1.edges(), kg2.edges())
            self.assertEqual(kg1.aliases(), kg2.aliases())
        finally:
            raw1.close()
            kg1.close()
            raw2.close()
            kg2.close()

    def test_rebuild_into_same_store_does_not_accumulate(self):
        raw, kg = self._stores("s")
        try:
            first = pipeline.rebuild(self.corpus_path, raw, kg, ner_backend="rule")
            second = pipeline.rebuild(self.corpus_path, raw, kg, ner_backend="rule")
            self.assertEqual(first["graph"], second["graph"])
            self.assertEqual(raw.count_documents(), 4)
        finally:
            raw.close()
            kg.close()


if __name__ == "__main__":
    unittest.main()
