import os
import tempfile
import unittest
import urllib.error
import urllib.request

from ailandscape import config, corpus, pipeline, sbir

SAMPLE_SBIR = config.ROOT / "samples" / "sample_sbir.json"


def _award(**overrides):
    base = {
        "firm": "Test Firm",
        "award_title": "A Generic Project",
        "agency": "Department of Defense",
        "branch": "Army",
        "phase": "Phase I",
        "program": "SBIR",
        "agency_tracking_number": "X1-0001",
        "abstract": "A project about widgets and gears.",
        "research_area_keywords": "widgets",
    }
    base.update(overrides)
    return base


class IsAIRelatedTest(unittest.TestCase):
    def test_abstract_machine_learning_is_ai(self):
        award = _award(abstract="We use machine learning to sort cargo.")
        self.assertTrue(sbir.is_ai_related(award))

    def test_keywords_only_is_ai(self):
        # AI appears only in research_area_keywords, not the abstract.
        award = _award(
            abstract="A platform that fuses open-source data.",
            research_area_keywords="artificial intelligence, forecasting",
        )
        self.assertTrue(sbir.is_ai_related(award))

    def test_uppercase_ai_acronym_is_ai(self):
        award = _award(abstract="The system adds an AI module for triage.")
        self.assertTrue(sbir.is_ai_related(award))

    def test_ml_acronym_is_ai(self):
        award = _award(abstract="An ML pipeline classifies incoming signals.")
        self.assertTrue(sbir.is_ai_related(award))

    def test_data_science_keyword_is_ai(self):
        award = _award(
            abstract="The team applies data science to readiness data.",
            research_area_keywords="logistics",
        )
        self.assertTrue(sbir.is_ai_related(award))

    def test_embodied_ai_robotics_is_ai(self):
        award = _award(
            award_title="Dexterous Manipulation for Depot Repair Robotics",
            abstract="A quadruped robot uses imitation learning for terrain.",
            research_area_keywords="robotics",
        )
        self.assertTrue(sbir.is_ai_related(award))

    def test_non_ai_award_is_rejected(self):
        award = _award(
            award_title="Composite Structures for Hypersonic Vehicles",
            abstract="A ceramic-matrix composite for high-temperature loads.",
            research_area_keywords="materials, composites",
        )
        self.assertFalse(sbir.is_ai_related(award))

    def test_lowercase_ai_inside_words_is_not_matched(self):
        # "maintain", "available", "training" contain "ai" but are not AI.
        award = _award(
            award_title="Available Maintenance Training Aid",
            abstract="A trainer that keeps spare parts available.",
            research_area_keywords="sustainment",
        )
        self.assertFalse(sbir.is_ai_related(award))


class AwardToArticleTest(unittest.TestCase):
    def test_basic_fields(self):
        award = _award(
            firm="Skyward Autonomy Inc.",
            award_title="ML for Drone Swarms",
            abstract="Develops machine learning for swarms.",
            award_link="https://www.sbir.gov/awards/example-1",
        )
        article = sbir.award_to_article(award)
        self.assertEqual(article["title"], "ML for Drone Swarms")
        self.assertEqual(article["url"], "https://www.sbir.gov/awards/example-1")
        self.assertIn("SBIR", article["source"])
        self.assertIn("Develops machine learning for swarms.",
                      article["raw_text"])

    def test_lead_sentence_is_active_voice(self):
        # Active voice so the relation extractor reads agency -> firm.
        award = _award(firm="Acme Robotics", agency="Department of Defense",
                        branch="Navy")
        article = sbir.award_to_article(award)
        lead = article["raw_text"].split("\n")[0]
        self.assertIn("awarded Acme Robotics", lead)
        self.assertLess(lead.index("Department of Defense"),
                        lead.index("Acme Robotics"))

    def test_url_is_synthesized_when_link_missing(self):
        award = _award(award_link="", agency_tracking_number="A2-11223")
        article = sbir.award_to_article(award)
        self.assertTrue(article["url"].startswith("https://www.sbir.gov/award/"))
        self.assertIn("A2-11223", article["url"])

    def test_research_institution_yields_partnership_sentence(self):
        award = _award(firm="Acme Robotics", ri_name="MIT")
        article = sbir.award_to_article(award)
        self.assertIn("Acme Robotics partnered with MIT", article["raw_text"])

    def test_award_amount_in_text_and_metadata(self):
        award = _award(award_amount="1499000", firm="Acme Robotics")
        article = sbir.award_to_article(award)
        # The dollar figure appears in the prose (so edge evidence carries it)
        # and as a structured number in the document metadata.
        self.assertIn("$1,499,000", article["raw_text"])
        self.assertEqual(article["metadata"]["award_amount"], 1499000.0)
        self.assertEqual(article["metadata"]["data_source"], "SBIR")

    def test_missing_award_amount_yields_none(self):
        article = sbir.award_to_article(_award())
        self.assertIsNone(article["metadata"]["award_amount"])
        self.assertNotIn("worth $", article["raw_text"])


class FixtureFilterTest(unittest.TestCase):
    def test_ai_articles_keeps_only_ai_awards(self):
        awards = sbir.load_fixture(SAMPLE_SBIR)
        self.assertEqual(len(awards), 6)
        articles = sbir.ai_articles(awards)
        # 4 of the 6 fixture awards are AI-related.
        self.assertEqual(len(articles), 4)
        titles = {a["title"] for a in articles}
        self.assertIn("Machine Learning for Resilient Drone Swarm Coordination",
                      titles)
        self.assertNotIn("Lightweight Composite Structures for Hypersonic Vehicles",
                          titles)


class FetchAwardsTest(unittest.TestCase):
    def _patch_get_json(self, fake):
        self._orig_get = sbir._get_json
        sbir._get_json = fake
        self.addCleanup(lambda: setattr(sbir, "_get_json", self._orig_get))

    def _patch_page_size(self, size):
        self._orig_page = sbir._PAGE_SIZE
        sbir._PAGE_SIZE = size
        self.addCleanup(lambda: setattr(sbir, "_PAGE_SIZE", self._orig_page))

    def test_pagination_collects_all_pages(self):
        self._patch_page_size(2)
        pages = [[{"firm": "A"}, {"firm": "B"}],
                 [{"firm": "C"}, {"firm": "D"}],
                 [{"firm": "E"}]]
        calls = []
        self._patch_get_json(
            lambda url: (calls.append(url), pages[len(calls) - 1])[1]
        )
        awards = sbir.fetch_awards(agency="DOD", max_records=100)
        self.assertEqual([a["firm"] for a in awards],
                         ["A", "B", "C", "D", "E"])
        self.assertEqual(len(calls), 3)  # stops after the short final page

    def test_max_records_caps_results(self):
        self._patch_page_size(2)
        self._patch_get_json(lambda url: [{"firm": "x"}, {"firm": "y"}])
        awards = sbir.fetch_awards(agency="DOD", max_records=3)
        self.assertEqual(len(awards), 3)

    def test_api_error_propagates(self):
        def boom(url):
            raise sbir.SBIRError("API down")

        self._patch_get_json(boom)
        with self.assertRaises(sbir.SBIRError):
            sbir.fetch_awards(agency="DOD")

    def test_get_json_retries_then_raises_on_429(self):
        attempts = []

        def fake_urlopen(req, timeout=None):
            attempts.append(1)
            raise urllib.error.HTTPError(
                req.full_url, 429, "Too Many Requests", {}, None
            )

        orig_open = urllib.request.urlopen
        orig_sleep = sbir.time.sleep
        urllib.request.urlopen = fake_urlopen
        sbir.time.sleep = lambda _s: None
        try:
            with self.assertRaises(sbir.SBIRError):
                sbir._get_json("https://example.test/awards", max_retries=2)
        finally:
            urllib.request.urlopen = orig_open
            sbir.time.sleep = orig_sleep
        self.assertEqual(len(attempts), 3)  # initial try + 2 retries


class ScrapeSBIRIntoCorpusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ailandscape-sbir-test-")
        self.corpus_path = os.path.join(self.tmp, "documents.jsonl")
        self._orig_fetch = sbir.fetch_awards
        self.addCleanup(lambda: setattr(sbir, "fetch_awards", self._orig_fetch))

    def test_adds_only_ai_awards(self):
        awards = sbir.load_fixture(SAMPLE_SBIR)
        sbir.fetch_awards = lambda **_kw: awards
        result = pipeline.scrape_sbir_into_corpus(
            [{"agency": "DOD"}], self.corpus_path
        )
        self.assertEqual(result["sbir_added"], 4)
        docs = corpus.load(self.corpus_path)
        self.assertEqual(len(docs), 4)
        self.assertTrue(all("SBIR" in d["source"] for d in docs))

    def test_second_run_de_duplicates(self):
        awards = sbir.load_fixture(SAMPLE_SBIR)
        sbir.fetch_awards = lambda **_kw: awards
        pipeline.scrape_sbir_into_corpus([{"agency": "DOD"}], self.corpus_path)
        again = pipeline.scrape_sbir_into_corpus(
            [{"agency": "DOD"}], self.corpus_path
        )
        self.assertEqual(again["sbir_added"], 0)
        self.assertEqual(len(corpus.load(self.corpus_path)), 4)

    def test_skips_gracefully_when_api_unavailable(self):
        def boom(**_kw):
            raise sbir.SBIRError("API in maintenance")

        sbir.fetch_awards = boom
        result = pipeline.scrape_sbir_into_corpus(
            [{"agency": "DOD", "year": 2025}], self.corpus_path
        )
        self.assertEqual(result["sbir_added"], 0)
        self.assertFalse(os.path.exists(self.corpus_path))


if __name__ == "__main__":
    unittest.main()
