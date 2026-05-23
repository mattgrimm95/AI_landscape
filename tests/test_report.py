import json
import os
import tempfile
import unittest

from ailandscape import report
from ailandscape.storage_kg import KnowledgeGraphStore
from ailandscape.storage_ner import NEROutputLog


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ner = NEROutputLog(os.path.join(self.tmp, "ner.db"))
        self.kg = KnowledgeGraphStore(os.path.join(self.tmp, "kg.db"))

    def tearDown(self):
        self.ner.close()
        self.kg.close()

    def _seed(self):
        self.ner.add_entities(
            "h1",
            [
                {"text": "China", "label": "place"},
                {"text": "Pentagon", "label": "organization"},
            ],
        )
        china = self.kg.insert_node(
            "China", "place", mention_count=5, document_count=3
        )
        pentagon = self.kg.insert_node(
            "Pentagon", "organization", mention_count=2, document_count=2
        )
        self.kg.insert_node(
            "Hegseth", "person", mention_count=1, document_count=1
        )
        self.kg.insert_node(
            "Pete Hegseth", "person", mention_count=4, document_count=2
        )
        self.kg.insert_edge(china, pentagon, "co_occurs_with", 3)
        self.kg.commit()

    def test_build_overview_funnel_and_breakdowns(self):
        self._seed()
        docs = [{"fetched_at": "2026-05-22T00:00:00+00:00"}]
        overview = report.build_overview(docs, self.ner, self.kg)
        self.assertEqual(overview["funnel"]["documents"], 1)
        self.assertEqual(overview["funnel"]["raw_mentions"], 2)
        self.assertEqual(overview["funnel"]["nodes"], 4)
        self.assertEqual(overview["funnel"]["edges"], 1)
        entity_types = {name: count for name, count, _ in overview["entity_types"]}
        self.assertEqual(entity_types["person"], 2)

    def test_overview_flags_quality_issues(self):
        self._seed()
        overview = report.build_overview([], self.ner, self.kg)
        # "Hegseth" is single-mention and a partial name of "Pete Hegseth".
        self.assertEqual(overview["quality"]["singletons"], 1)
        self.assertEqual(overview["quality"]["partial_name_dups"], 1)
        self.assertEqual(
            overview["quality"]["examples"][0], ("Hegseth", "Pete Hegseth")
        )
        # Hegseth and Pete Hegseth have no edges -> isolated.
        self.assertEqual(overview["quality"]["isolated"], 2)
        mentions = dict(overview["distributions"]["mentions"])
        self.assertEqual(sum(mentions.values()), 4)

    def test_render_overview_produces_readable_text(self):
        self._seed()
        text = report.render_overview(
            report.build_overview(
                [{"fetched_at": "2026-05-22T00:00:00+00:00"}], self.ner, self.kg
            )
        )
        for heading in (
            "AI LANDSCAPE",
            "PIPELINE FUNNEL",
            "SCRAPE STATUS",
            "ENTITY TYPES",
            "DISTRIBUTIONS",
            "DATA QUALITY",
        ):
            self.assertIn(heading, text)

    def test_overview_handles_empty_data(self):
        overview = report.build_overview([], self.ner, self.kg)
        self.assertEqual(overview["funnel"]["nodes"], 0)
        self.assertFalse(overview["scrape"]["within_24h"])
        self.assertIn("AI LANDSCAPE", report.render_overview(overview))


class DateQualityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ner = NEROutputLog(os.path.join(self.tmp, "ner.db"))
        self.kg = KnowledgeGraphStore(os.path.join(self.tmp, "kg.db"))

    def tearDown(self):
        self.ner.close()
        self.kg.close()

    def test_unparseable_dates_surface_per_source(self):
        docs = [
            {"source": "GoodFeed", "published": "2026-05-21T10:00:00+00:00"},
            {"source": "GoodFeed", "published": "Wed, 21 May 2026 16:00:12 +0000"},
            {"source": "BrokenFeed", "published": "21 maggio 2026"},
            {"source": "BrokenFeed", "published": "il y a deux jours"},
            {"source": "NoDateFeed", "published": ""},
        ]
        overview = report.build_overview(docs, self.ner, self.kg)
        dq = overview["dates"]
        self.assertEqual(dq["totals"]["parsed"], 2)
        self.assertEqual(dq["totals"]["unparseable"], 2)
        self.assertEqual(dq["totals"]["missing"], 1)
        # BrokenFeed should top the concerning list.
        concerning = dq["concerning"]
        self.assertTrue(concerning)
        self.assertEqual(concerning[0]["source"], "BrokenFeed")
        self.assertEqual(concerning[0]["unparseable"], 2)

    def test_render_includes_date_coverage_section(self):
        docs = [
            {"source": "BrokenFeed", "published": "garbage"},
        ]
        text = report.render_overview(
            report.build_overview(docs, self.ner, self.kg)
        )
        self.assertIn("PUBLISHED-DATE COVERAGE", text)
        self.assertIn("BrokenFeed", text)


class FeedHealthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ner = NEROutputLog(os.path.join(self.tmp, "ner.db"))
        self.kg = KnowledgeGraphStore(os.path.join(self.tmp, "kg.db"))

    def tearDown(self):
        self.ner.close()
        self.kg.close()

    def test_stale_feeds_are_surfaced(self):
        # Fresh: fetched yesterday + recent adds.
        # Stale: fetched two years ago, no recent adds.
        docs = [
            {"source": "Fresh", "fetched_at": "2026-05-22T00:00:00+00:00"},
            {"source": "Stale", "fetched_at": "2024-01-01T00:00:00+00:00"},
        ]
        # Construct a tiny run-history file with one run that added a Fresh
        # doc and not a Stale one.
        history = os.path.join(self.tmp, "run_history.jsonl")
        with open(history, "w", encoding="utf-8") as h:
            h.write(json.dumps({
                "finished_at": "2026-05-22T00:00:00+00:00",
                "feeds": {"Fresh": {"added": 1}, "Stale": {"added": 0}},
            }) + "\n")
        overview = report.build_overview(docs, self.ner, self.kg, history)
        stale_sources = {r["source"] for r in overview["feeds"]["stale"]}
        self.assertIn("Stale", stale_sources)
        self.assertNotIn("Fresh", stale_sources)


class SignalCoverageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ner = NEROutputLog(os.path.join(self.tmp, "ner.db"))
        self.kg = KnowledgeGraphStore(os.path.join(self.tmp, "kg.db"))

    def tearDown(self):
        self.ner.close()
        self.kg.close()

    def test_documents_without_entities_are_counted(self):
        # Two documents in the corpus; only one has any NER output.
        self.ner.add_entities(
            "h1", [{"text": "China", "label": "place"}]
        )
        docs = [
            {"source": "A", "content_hash": "h1", "title": "Has entity",
             "url": "u1", "raw_text": "x" * 800},
            {"source": "B", "content_hash": "h2", "title": "Empty",
             "url": "u2", "raw_text": "x" * 800},
            {"source": "C", "content_hash": "h3", "title": "Short",
             "url": "u3", "raw_text": "tiny"},
        ]
        overview = report.build_overview(docs, self.ner, self.kg)
        signals = overview["signals"]
        self.assertEqual(signals["no_entities"], 2)  # h2 and h3
        self.assertEqual(signals["short_body"], 1)   # h3


class DiffRunsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.history = os.path.join(self.tmp, "run_history.jsonl")

    def test_diff_none_with_single_run(self):
        with open(self.history, "w", encoding="utf-8") as h:
            h.write(json.dumps({"finished_at": "t", "documents": 10}) + "\n")
        self.assertIsNone(report.diff_runs(self.history))

    def test_diff_computes_deltas_and_percentages(self):
        with open(self.history, "w", encoding="utf-8") as h:
            h.write(json.dumps({
                "finished_at": "t1", "documents": 100, "nodes": 1000,
                "typed_relations": 200,
            }) + "\n")
            h.write(json.dumps({
                "finished_at": "t2", "documents": 110, "nodes": 800,
                "typed_relations": 210,
            }) + "\n")
        diff = report.diff_runs(self.history)
        self.assertIsNotNone(diff)
        deltas = diff["deltas"]
        self.assertEqual(deltas["documents"]["delta"], 10)
        self.assertAlmostEqual(deltas["documents"]["delta_pct"], 10.0)
        self.assertEqual(deltas["nodes"]["delta"], -200)
        self.assertEqual(deltas["nodes"]["delta_pct"], -20.0)
        # KPIs only in one record are skipped (graceful for legacy lines).
        self.assertNotIn("singletons", deltas)

    def test_render_diff_highlights_large_changes(self):
        with open(self.history, "w", encoding="utf-8") as h:
            h.write(json.dumps({
                "finished_at": "t1", "documents": 100, "nodes": 1000,
            }) + "\n")
            h.write(json.dumps({
                "finished_at": "t2", "documents": 200, "nodes": 1001,
            }) + "\n")
        text = report.render_diff(report.diff_runs(self.history))
        self.assertIn("RUN-OVER-RUN DIFF", text)
        # 100% change on documents is flagged; <1% on nodes is not.
        doc_line = [
            l for l in text.splitlines() if l.startswith("documents")
        ][0]
        self.assertIn("**", doc_line)
        nodes_line = [
            l for l in text.splitlines() if l.startswith("nodes")
        ][0]
        self.assertNotIn("**", nodes_line)


if __name__ == "__main__":
    unittest.main()
