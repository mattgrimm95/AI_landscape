"""Tests for the shared AI-relevance lexicon."""

import unittest

from ailandscape import ai_terms


class AiTermsCorePatternsTest(unittest.TestCase):
    """The original SBIR terms must still match (no regression)."""

    def test_core_ai_terms(self):
        for phrase in [
            "artificial intelligence research",
            "machine learning model",
            "deep neural network",
            "reinforcement learning",
            "generative model",
            "computer vision pipeline",
            "natural language processing",
        ]:
            self.assertTrue(ai_terms.is_ai_relevant(phrase), repr(phrase))

    def test_acronyms_case_sensitive(self):
        # Bare uppercase acronyms hit; lowercase fragments inside words don't.
        self.assertTrue(ai_terms.is_ai_relevant("Run AI agents."))
        self.assertTrue(ai_terms.is_ai_relevant("with ML pipelines"))
        self.assertTrue(ai_terms.is_ai_relevant("LLM evaluation"))
        # lowercase "ai" inside "available" or "maintain" must not match.
        self.assertFalse(ai_terms.is_ai_relevant(
            "The aircraft is available and easy to maintain."
        ))
        self.assertFalse(ai_terms.is_ai_relevant("HTML rendering"))


class AiTermsDefenseOverlapTest(unittest.TestCase):
    """The expanded defense-AI overlap vocabulary."""

    def test_jadc2_acronym(self):
        self.assertTrue(ai_terms.is_ai_relevant(
            "JADC2 is the Joint All-Domain Command and Control architecture."
        ))
        self.assertTrue(ai_terms.is_ai_relevant("CJADC2 next steps"))

    def test_mum_t_and_c_uas(self):
        self.assertTrue(ai_terms.is_ai_relevant("MUM-T demo at Edwards"))
        self.assertTrue(ai_terms.is_ai_relevant("C-UAS interceptor swarm"))

    def test_named_ai_platforms(self):
        # Named platforms count: Maven, Lattice, AIP, Replicator.
        self.assertTrue(ai_terms.is_ai_relevant(
            "Marine Corps signs Maven Smart System deal"
        ))
        self.assertTrue(ai_terms.is_ai_relevant("Anduril Lattice OS demo"))
        self.assertTrue(ai_terms.is_ai_relevant("Project Replicator update"))
        self.assertTrue(ai_terms.is_ai_relevant(
            "Palantir's Artificial Intelligence Platform now in IL5"
        ))
        # MSS as a standalone acronym
        self.assertTrue(ai_terms.is_ai_relevant("MSS rollout to combatant commands"))

    def test_collaborative_combat_aircraft_and_loitering(self):
        self.assertTrue(ai_terms.is_ai_relevant(
            "GA-ASI's collaborative combat aircraft prototype"
        ))
        self.assertTrue(ai_terms.is_ai_relevant("Loitering munition strike"))

    def test_sensor_fusion_phrases(self):
        self.assertTrue(ai_terms.is_ai_relevant("multi-sensor fusion stack"))
        self.assertTrue(ai_terms.is_ai_relevant("sensor fusion onboard the B-21"))
        self.assertTrue(ai_terms.is_ai_relevant("intelligence fusion in MSS"))

    def test_frontier_and_agentic(self):
        self.assertTrue(ai_terms.is_ai_relevant("frontier model evaluation"))
        self.assertTrue(ai_terms.is_ai_relevant("agentic system for targeting"))
        self.assertTrue(ai_terms.is_ai_relevant("agentic ai in defense workflows"))

    def test_edge_inference(self):
        self.assertTrue(ai_terms.is_ai_relevant(
            "Edge inference at the tactical edge"
        ))
        self.assertTrue(ai_terms.is_ai_relevant("edge AI deployment"))


class AiTermsIntentionalGapsTest(unittest.TestCase):
    """Terms deliberately NOT in the lexicon to avoid over-matching."""

    def test_plain_electronic_warfare_does_not_match(self):
        # EW spans far beyond AI; many EW articles are about jamming
        # hardware not autonomy. Must not match alone.
        self.assertFalse(ai_terms.is_ai_relevant(
            "Ukrainian electronic warfare jammers near the border."
        ))

    def test_plain_drone_does_not_match(self):
        # Most drone coverage is about platform / payload. Only
        # autonomous / swarm / wall / AI-enabled drones should match.
        self.assertFalse(ai_terms.is_ai_relevant(
            "A drone carrying a payload was launched at Bagram."
        ))

    def test_real_non_ai_titles_rejected(self):
        # Real titles from the corpus catch-up that should NOT pass.
        for title in [
            "The Tiny Chipmunk Trainer Was The Cold War's Most Unlikely Spyplane",
            "Peacekeeping troop numbers fall to lowest in at least 25 years, SIPRI says",
            "Supercarrier USS Gerald R. Ford To Act As Floating Nuclear Power Plant",
            "Bunker Talk: Memorial Day Weekend Edition",
            "China Seeks Independence, Weakening Trump's Leverage",  # AI replaced
        ]:
            self.assertFalse(ai_terms.is_ai_relevant(title), title)


class AiTermsHelpersTest(unittest.TestCase):
    def test_is_ai_relevant_handles_none_and_empty(self):
        self.assertFalse(ai_terms.is_ai_relevant(None))
        self.assertFalse(ai_terms.is_ai_relevant(""))

    def test_ai_terms_in_returns_distinct_hits(self):
        hits = ai_terms.ai_terms_in(
            "machine learning and computer vision and AI everywhere"
        )
        self.assertIn("ai", " ".join(hits).lower())
        self.assertIn("machine learning", hits)
        self.assertIn("computer vision", hits)

    def test_sbir_reexports_same_objects(self):
        # Legacy call sites import sbir._AI_TERMS / sbir._AI_ACRONYMS;
        # they must be the same compiled regex object as the canonical one
        # so a single edit lifts every gate.
        from ailandscape import sbir
        self.assertIs(sbir._AI_TERMS, ai_terms.AI_TERMS)
        self.assertIs(sbir._AI_ACRONYMS, ai_terms.AI_ACRONYMS)


if __name__ == "__main__":
    unittest.main()
