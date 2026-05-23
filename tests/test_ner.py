import importlib.util
import unittest

from ailandscape import ner

_HAS_SPACY = (
    importlib.util.find_spec("spacy") is not None
    and importlib.util.find_spec("en_core_web_sm") is not None
)


def texts(entities):
    return {e["text"] for e in entities}


class NerTest(unittest.TestCase):
    def test_gazetteer_entities(self):
        found = texts(
            ner.extract(
                "The Pentagon and Lockheed Martin discussed the F-35.",
                backend="rule",
            )
        )
        self.assertIn("Pentagon", found)
        self.assertIn("Lockheed Martin", found)
        self.assertIn("F-35", found)

    def test_gazetteer_canonicalization(self):
        found = texts(
            ner.extract("The U.S. and the DoD issued a statement.", backend="rule")
        )
        self.assertIn("United States", found)
        self.assertIn("Department of Defense", found)

    def test_ai_capability_concepts(self):
        # Multi-word AI capabilities resolve to canonical concept nodes.
        found = ner.extract(
            "The lab applies computer vision and reinforcement learning.",
            backend="rule",
        )
        concepts = {e["text"]: e["label"] for e in found}
        self.assertEqual(concepts.get("Computer Vision"), "concept")
        self.assertEqual(concepts.get("Reinforcement Learning"), "concept")

    def test_lowercase_single_word_not_an_entity(self):
        # A lowercase common word must not match a single-word gazetteer key.
        found = texts(ner.extract("They built an army of drones.", backend="rule"))
        self.assertNotIn("U.S. Army", found)

    def test_proper_noun_detection(self):
        found = texts(
            ner.extract("Jensen Huang visited Brussels today.", backend="rule")
        )
        self.assertIn("Jensen Huang", found)
        self.assertIn("Brussels", found)

    def test_person_title_is_stripped(self):
        entities = ner.extract("Secretary Lloyd Austin spoke today.", backend="rule")
        people = [e for e in entities if e["text"] == "Lloyd Austin"]
        self.assertEqual(len(people), 1)
        self.assertEqual(people[0]["label"], "person")

    def test_chained_person_titles_are_stripped(self):
        # "Lt. Col. Jason Kruck" was capturing as a single misc phrase because
        # only ONE leading title was being stripped. With the chained-title
        # strip we recover "Jason Kruck" as a person.
        entities = ner.extract(
            "Lt. Col. Jason Kruck commanded the squadron.", backend="rule"
        )
        people = [e for e in entities if e["text"] == "Jason Kruck"]
        self.assertEqual(len(people), 1)
        self.assertEqual(people[0]["label"], "person")

    def test_multi_connector_phrases_are_kept_together(self):
        # Phrases that bridge two lowercase connectors ("of the") used to be
        # split — "Department of the Treasury" came out as just "Department"
        # and "Treasury". The connector look-ahead joins them back.
        found = texts(
            ner.extract(
                "Officials at the Department of the Treasury met today.",
                backend="rule",
            )
        )
        self.assertIn("Department of the Treasury", found)

    def test_offsets_point_at_surface_text(self):
        text = "China expanded its program."
        china = [
            e for e in ner.extract(text, backend="rule") if e["text"] == "China"
        ][0]
        self.assertEqual(text[china["start"]:china["end"]], "China")

    def test_empty_text(self):
        self.assertEqual(ner.extract("", backend="rule"), [])

    def test_default_backend_is_known(self):
        self.assertIn(ner.default_backend(), {"rule", "spacy", "hybrid"})

    @unittest.skipUnless(_HAS_SPACY, "spaCy / en_core_web_sm not installed")
    def test_hybrid_backend_combines_gazetteer_and_spacy(self):
        found = texts(
            ner.extract(
                "The Pentagon briefed Jensen Huang on the F-35 program.",
                backend="hybrid",
            )
        )
        # Gazetteer entities (precise, canonical).
        self.assertIn("Pentagon", found)
        self.assertIn("F-35", found)
        # spaCy contributes entities the gazetteer does not cover.
        self.assertTrue(any(t not in {"Pentagon", "F-35"} for t in found))


if __name__ == "__main__":
    unittest.main()
