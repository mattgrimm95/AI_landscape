import os
import tempfile
import unittest

from ailandscape import config, corpus, pipeline, scraper
from ailandscape.storage_kg import KnowledgeGraphStore
from ailandscape.storage_ner import NEROutputLog

SAMPLE = config.ROOT / "samples" / "sample_feed.xml"


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.corpus_path = os.path.join(self.tmp, "documents.jsonl")
        for article in scraper.scrape_fixture(SAMPLE, "Sample Feed"):
            corpus.append(self.corpus_path, pipeline.make_record(article))

    def _stores(self, tag):
        return (
            NEROutputLog(os.path.join(self.tmp, tag + "_ner.db")),
            KnowledgeGraphStore(os.path.join(self.tmp, tag + "_kg.db")),
        )

    def test_rebuild_from_corpus(self):
        ner_log, kg = self._stores("a")
        try:
            result = pipeline.rebuild(
                self.corpus_path, ner_log, kg, ner_backend="rule"
            )
            self.assertEqual(result["documents"], 4)
            self.assertGreater(result["entities"], 0)
            self.assertGreater(result["graph"]["nodes"], 5)
            self.assertGreater(result["graph"]["edges"], 0)
            for alias in ("pentagon", "lockheed martin", "f-35", "china", "ukraine"):
                self.assertIsNotNone(
                    kg.node_by_alias(alias), "missing node: %s" % alias
                )
        finally:
            ner_log.close()
            kg.close()

    def test_rebuild_is_deterministic(self):
        # The same corpus must produce byte-identical outputs every time.
        ner1, kg1 = self._stores("d1")
        ner2, kg2 = self._stores("d2")
        try:
            pipeline.rebuild(self.corpus_path, ner1, kg1, ner_backend="rule")
            pipeline.rebuild(self.corpus_path, ner2, kg2, ner_backend="rule")
            self.assertEqual(ner1.all_entities(), ner2.all_entities())
            self.assertEqual(kg1.nodes(), kg2.nodes())
            self.assertEqual(kg1.edges(), kg2.edges())
            self.assertEqual(kg1.aliases(), kg2.aliases())
        finally:
            ner1.close()
            kg1.close()
            ner2.close()
            kg2.close()

    def test_rebuild_into_same_store_does_not_accumulate(self):
        ner_log, kg = self._stores("s")
        try:
            first = pipeline.rebuild(self.corpus_path, ner_log, kg, ner_backend="rule")
            second = pipeline.rebuild(self.corpus_path, ner_log, kg, ner_backend="rule")
            self.assertEqual(first["graph"], second["graph"])
            self.assertEqual(first["entities"], second["entities"])
            self.assertEqual(ner_log.count_entities(), first["entities"])
        finally:
            ner_log.close()
            kg.close()


if __name__ == "__main__":
    unittest.main()
