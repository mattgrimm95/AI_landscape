import json
import os
import pathlib
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


class FeedHealthScorecardTest(unittest.TestCase):
    """`scrape_into_corpus` should now emit a per-feed scorecard."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.corpus_path = os.path.join(self.tmp, "documents.jsonl")
        self._orig_fetch = scraper.fetch_feed
        self._orig_extract = scraper.extract_article
        self._orig_sleep = pipeline.time.sleep
        pipeline.time.sleep = lambda _s: None

    def tearDown(self):
        scraper.fetch_feed = self._orig_fetch
        scraper.extract_article = self._orig_extract
        pipeline.time.sleep = self._orig_sleep

    def test_per_feed_stats_track_each_source(self):
        # Two feeds: one returns articles, one errors out. The result
        # should carry a `feeds` dict with a row for each.
        def fake_fetch(feed):
            if feed["url"] == "ok":
                return [
                    {"url": "https://example.test/a", "title": "A",
                     "raw_text": "body A"},
                    {"url": "https://example.test/b", "title": "B",
                     "raw_text": "body B"},
                ]
            raise scraper.FeedError("simulated outage")
        scraper.fetch_feed = fake_fetch
        scraper.extract_article = lambda url, fallback="": fallback
        feeds = [
            # ai_only=False: this test is about per-feed scorecard
            # tracking, not the AI-relevance filter (covered separately
            # in AiRelevanceFilterTest below). Disable the filter so
            # the synthetic "body A" / "body B" payloads still land.
            {"name": "Good", "url": "ok", "ai_only": False},
            {"name": "Broken", "url": "down", "ai_only": False},
        ]
        result = pipeline.scrape_into_corpus(feeds, self.corpus_path)
        self.assertIn("feeds", result)
        self.assertEqual(result["feeds"]["Good"]["fetched"], 2)
        self.assertEqual(result["feeds"]["Good"]["added"], 2)
        self.assertEqual(result["feeds"]["Good"]["error"], "")
        self.assertIn("simulated outage", result["feeds"]["Broken"]["error"])
        self.assertEqual(result["feeds"]["Broken"]["added"], 0)
        # Totals still match the legacy fields.
        self.assertEqual(result["added"], 2)


class AiRelevanceFilterTest(unittest.TestCase):
    """Per-feed AI-relevance filter applied during scrape_into_corpus."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.corpus_path = os.path.join(self.tmp, "documents.jsonl")
        self._orig_fetch = scraper.fetch_feed
        self._orig_extract = scraper.extract_article
        self._orig_sleep = pipeline.time.sleep
        pipeline.time.sleep = lambda _s: None

    def tearDown(self):
        scraper.fetch_feed = self._orig_fetch
        scraper.extract_article = self._orig_extract
        pipeline.time.sleep = self._orig_sleep

    def _articles(self):
        # Two AI articles + two non-AI articles, deterministic order.
        return [
            {"url": "https://t/ai1", "title": "Machine learning beats human"},
            {"url": "https://t/non1", "title": "Cold War Chipmunk Spyplane"},
            {"url": "https://t/ai2", "title": "MUM-T demo at Edwards AFB"},
            {"url": "https://t/non2", "title": "Quad foreign ministers meet"},
        ]

    def test_default_filter_drops_non_ai_articles(self):
        scraper.fetch_feed = lambda f: list(self._articles())
        # extract_article returns the title as body — so the AI filter
        # sees the title text as full content.
        scraper.extract_article = lambda url, fallback="": fallback or url

        feeds = [{"name": "General defense feed", "url": "x"}]  # ai_only defaults to True
        result = pipeline.scrape_into_corpus(feeds, self.corpus_path)
        self.assertEqual(result["fetched"], 4)
        # The two AI articles land; the two non-AI ones are filtered.
        self.assertEqual(result["added"], 2)
        self.assertEqual(result["filtered_non_ai"], 2)
        self.assertEqual(result["feeds"]["General defense feed"]["filtered_non_ai"], 2)
        # The dropped URLs are not in the corpus.
        landed = corpus.load(self.corpus_path)
        landed_urls = {d["url"] for d in landed}
        self.assertIn("https://t/ai1", landed_urls)
        self.assertIn("https://t/ai2", landed_urls)
        self.assertNotIn("https://t/non1", landed_urls)
        self.assertNotIn("https://t/non2", landed_urls)

    def test_ai_only_false_bypasses_filter(self):
        # Pure AI feeds (publisher-curated) should bypass the filter so
        # legit AI articles that don't repeat the keyword still land.
        scraper.fetch_feed = lambda f: list(self._articles())
        scraper.extract_article = lambda url, fallback="": fallback or url

        feeds = [{"name": "Pure AI feed", "url": "x", "ai_only": False}]
        result = pipeline.scrape_into_corpus(feeds, self.corpus_path)
        self.assertEqual(result["added"], 4)
        self.assertEqual(result["filtered_non_ai"], 0)

    def test_feed_default_is_filter_on(self):
        # A feed dict without an explicit ai_only key must default to
        # filter-on (conservative: keep the corpus AI-focused).
        scraper.fetch_feed = lambda f: [
            {"url": "https://t/non", "title": "Cold War Chipmunk Spyplane"},
        ]
        scraper.extract_article = lambda url, fallback="": fallback or url

        feeds = [{"name": "Unspecified", "url": "x"}]  # no ai_only key
        result = pipeline.scrape_into_corpus(feeds, self.corpus_path)
        self.assertEqual(result["added"], 0)
        self.assertEqual(result["filtered_non_ai"], 1)


class QualityKpiTest(unittest.TestCase):
    """Pipeline `run` must capture quality KPIs in the run-history record."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.corpus_path = os.path.join(self.tmp, "documents.jsonl")
        for article in scraper.scrape_fixture(SAMPLE, "Sample Feed"):
            corpus.append(self.corpus_path, pipeline.make_record(article))
        self._orig_history = config.RUN_HISTORY_FILE
        config.RUN_HISTORY_FILE = pathlib.Path(self.tmp) / "run_history.jsonl"
        # No actual scrape, just rebuild + record.
        self._orig_sleep = pipeline.time.sleep
        pipeline.time.sleep = lambda _s: None

    def tearDown(self):
        config.RUN_HISTORY_FILE = self._orig_history
        pipeline.time.sleep = self._orig_sleep

    def test_run_records_quality_kpis(self):
        ner_log = NEROutputLog(os.path.join(self.tmp, "ner.db"))
        kg = KnowledgeGraphStore(os.path.join(self.tmp, "kg.db"))
        try:
            # An empty feeds list means scrape is a no-op; the rebuild
            # path still runs and records the quality KPIs.
            pipeline.run(
                feeds=[], corpus_path=self.corpus_path,
                ner_log=ner_log, kg_store=kg, ner_backend="rule",
            )
        finally:
            ner_log.close()
            kg.close()
        lines = config.RUN_HISTORY_FILE.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[-1])
        # Quality KPIs land on the record.
        for key in (
            "singletons", "singleton_pct", "isolated", "isolated_pct",
            "partial_name_dups", "mentions_per_node", "typed_relations",
        ):
            self.assertIn(key, record)


class BackfillTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.corpus_path = os.path.join(self.tmp, "documents.jsonl")
        self._orig_extract = scraper.extract_article
        self._orig_sleep = pipeline.time.sleep
        pipeline.time.sleep = lambda _s: None
        self.addCleanup(
            lambda: setattr(scraper, "extract_article", self._orig_extract)
        )
        self.addCleanup(
            lambda: setattr(pipeline.time, "sleep", self._orig_sleep)
        )

    def test_short_doc_repaired_long_doc_untouched(self):
        long_text = "Full article body sentence. " * 60
        corpus.append(self.corpus_path, {
            "url": "https://example.test/short", "title": "Short",
            "content_hash": "h1", "raw_text": "teaser only",
        })
        corpus.append(self.corpus_path, {
            "url": "https://example.test/long", "title": "Long",
            "content_hash": "h2", "raw_text": long_text,
        })
        scraper.extract_article = lambda url, fallback="": (
            long_text if url.endswith("/short") else fallback
        )
        result = pipeline.backfill_corpus_text(self.corpus_path)
        self.assertEqual(result["scanned"], 2)
        self.assertEqual(result["repaired"], 1)
        docs = {d["content_hash"]: d for d in corpus.load(self.corpus_path)}
        self.assertIn("Full article body", docs["h1"]["raw_text"])
        self.assertEqual(docs["h2"]["raw_text"], long_text)

    def test_failed_refetch_leaves_corpus_unchanged(self):
        corpus.append(self.corpus_path, {
            "url": "https://example.test/short", "title": "Short",
            "content_hash": "h1", "raw_text": "teaser only",
        })
        # A failed re-fetch returns the short fallback unchanged.
        scraper.extract_article = lambda url, fallback="": fallback
        result = pipeline.backfill_corpus_text(self.corpus_path)
        self.assertEqual(result["repaired"], 0)
        self.assertEqual(
            corpus.load(self.corpus_path)[0]["raw_text"], "teaser only"
        )


if __name__ == "__main__":
    unittest.main()
