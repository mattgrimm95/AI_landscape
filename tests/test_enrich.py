"""Unit tests for the entity-enrichment module.

Focus: the AI-relevance gate. The enrichment pipeline must not let an
off-topic plan land in the corpus, but it must also be permissive enough
that legitimately AI-adjacent enrichments (e.g. B-21 production stories
sitting under an AI-platform synthesis) pass.
"""

import json
import pathlib
import tempfile
import unittest

from ailandscape import corpus, enrich


# Two compact plan fixtures: one clearly AI-relevant, one clearly not.
# Each test composes its own plan from these pieces so the assertions stay
# self-contained and read top-to-bottom.

_AI_PLAN = {
    "entity": "Maven Smart System",
    "articles": [
        {
            "url": "https://example.test/maven-1",
            "title": "Pentagon expands Maven Smart System",
            "source": "Test",
            "html": "<p>The Pentagon expanded Palantir's Maven Smart System "
                    "with machine learning capabilities.</p>",
        },
    ],
    "synthesis": {
        "title": "Maven overview",
        "body": "Maven Smart System uses artificial intelligence and "
                "computer vision to fuse multi-source intelligence.",
    },
}

_NON_AI_PLAN = {
    "entity": "Cold War Chipmunk Trainer",
    "articles": [
        {
            "url": "https://example.test/chipmunk-1",
            "title": "The Tiny Chipmunk Trainer Was a Cold War Spyplane",
            "source": "Test",
            "html": "<p>The Chipmunk was a small training aircraft used "
                    "for reconnaissance during the Cold War.</p>",
        },
    ],
    "synthesis": {
        "title": "Chipmunk overview",
        "body": "A British piston-engine training aircraft used by air "
                "forces from the 1940s onward.",
    },
}

# An AI-relevant plan where ONLY the synthesis contains AI signal — the
# articles are production / contract stories with no AI keyword. This is
# the real-world B-21 shape: the platform IS AI-enabled, but each
# individual story focuses on the contract / production angle.
_SYNTHESIS_ONLY_PLAN = {
    "entity": "B-21 Raider",
    "articles": [
        {
            "url": "https://example.test/b21-1",
            "title": "Air Force ramps up B-21 production capacity",
            "source": "Test",
            "html": "<p>Northrop Grumman won a $4.5 billion deal to "
                    "accelerate B-21 Raider production by 25 percent.</p>",
        },
    ],
    "synthesis": {
        "title": "B-21 overview",
        "body": "The B-21 Raider's mission systems use AI-enabled onboard "
                "computing for multi-sensor fusion across radar, infrared, "
                "and electronic warfare inputs.",
    },
}


class AiRelevanceGateTest(unittest.TestCase):
    """The gate logic in isolation (no corpus I/O)."""

    def test_is_ai_relevant_positive_phrase(self):
        self.assertTrue(enrich._is_ai_relevant(
            "Uses machine learning to fuse sensor data."
        ))

    def test_is_ai_relevant_positive_acronym(self):
        self.assertTrue(enrich._is_ai_relevant(
            "AI-enabled targeting via the new SLAM stack."
        ))

    def test_is_ai_relevant_negative(self):
        self.assertFalse(enrich._is_ai_relevant(
            "UN peacekeeping troop numbers fall to a 25-year low."
        ))

    def test_is_ai_relevant_empty(self):
        self.assertFalse(enrich._is_ai_relevant(""))
        self.assertFalse(enrich._is_ai_relevant(None))

    def test_lowercase_ai_in_word_does_not_match(self):
        # "available" / "maintain" contain "ai" but should not fire the
        # AI gate — the SBIR regex is case-sensitive for bare AI/ML acronyms.
        self.assertFalse(enrich._is_ai_relevant(
            "The aircraft is available and easy to maintain."
        ))

    def test_plan_signal_synthesis_match(self):
        signal = enrich.plan_ai_signal(_SYNTHESIS_ONLY_PLAN)
        self.assertTrue(signal["ok"])
        self.assertEqual(signal["matched_in"], "synthesis")
        self.assertIn("ai-enabled", " ".join(signal["matched_terms"]).lower())

    def test_plan_signal_article_match(self):
        # Build a plan whose synthesis is non-AI but an article carries
        # an AI term — the gate should still admit it.
        plan = {
            "entity": "X",
            "articles": [{
                "url": "u", "title": "Acquires AI startup",
                "html": "<p>uses computer vision in the field</p>",
            }],
            "synthesis": {"title": "X", "body": "An organization."},
        }
        signal = enrich.plan_ai_signal(plan)
        self.assertTrue(signal["ok"])
        self.assertTrue(signal["matched_in"].startswith("article:"))

    def test_plan_signal_no_match(self):
        signal = enrich.plan_ai_signal(_NON_AI_PLAN)
        self.assertFalse(signal["ok"])
        self.assertEqual(signal["matched_in"], "")
        self.assertEqual(signal["matched_terms"], [])


class EnrichFromPlanGateTest(unittest.TestCase):
    """The gate's behaviour when enrich_from_plan actually runs."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.corpus_path = self.tmp / "documents.jsonl"

    def _docs(self):
        return corpus.load(self.corpus_path)

    def test_ai_plan_lands(self):
        result = enrich.enrich_from_plan(self.corpus_path, _AI_PLAN)
        self.assertTrue(result["ai_relevant"])
        self.assertEqual(result["articles_added"], 1)
        self.assertTrue(result["synthesis_added"])
        self.assertEqual(len(self._docs()), 2)  # 1 article + 1 synthesis

    def test_synthesis_only_signal_admits_plan(self):
        # Per-article filtering would reject this (no AI keyword in the
        # article body), but plan-level filtering admits it because the
        # synthesis vouches for the topic.
        result = enrich.enrich_from_plan(self.corpus_path, _SYNTHESIS_ONLY_PLAN)
        self.assertTrue(result["ai_relevant"])
        self.assertEqual(result["matched_in" if False else "ai_matched_in"],
                         "synthesis")
        self.assertEqual(result["articles_added"], 1)
        self.assertTrue(result["synthesis_added"])

    def test_non_ai_plan_is_rejected_by_default(self):
        result = enrich.enrich_from_plan(self.corpus_path, _NON_AI_PLAN)
        self.assertFalse(result["ai_relevant"])
        self.assertEqual(result["articles_added"], 0)
        self.assertEqual(result["articles_skipped"], 1)
        self.assertFalse(result["synthesis_added"])
        # Nothing landed in the corpus.
        self.assertEqual(self._docs(), [])

    def test_allow_non_ai_bypass(self):
        result = enrich.enrich_from_plan(
            self.corpus_path, _NON_AI_PLAN, allow_non_ai=True,
        )
        self.assertFalse(result["ai_relevant"])
        self.assertEqual(result["articles_added"], 1)
        self.assertTrue(result["synthesis_added"])
        self.assertEqual(len(self._docs()), 2)


class RealEnrichmentPlansTest(unittest.TestCase):
    """Regression: the two enrichment plans on disk must still pass.

    Asserts that the operator's existing Palantir and B-21 plans continue
    to be admitted after the AI gate was added -- so a re-run of either
    plan would not silently start rejecting historical enrichments.
    """

    def _read_plan(self, name):
        path = pathlib.Path(__file__).resolve().parents[1] / "data" / "enrichment" / name
        if not path.exists():
            self.skipTest("plan file not present: " + name)
        return json.loads(path.read_text(encoding="utf-8"))

    def test_palantir_plan_passes(self):
        plan = self._read_plan("palantir_2026-05-23.json")
        signal = enrich.plan_ai_signal(plan)
        self.assertTrue(signal["ok"], "Palantir plan must pass the AI gate")

    def test_b21_plan_passes(self):
        plan = self._read_plan("b21_2026-05-23.json")
        signal = enrich.plan_ai_signal(plan)
        self.assertTrue(signal["ok"], "B-21 plan must pass the AI gate")


if __name__ == "__main__":
    unittest.main()
