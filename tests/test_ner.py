import unittest

from ailandscape import ner


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

    def test_offsets_point_at_surface_text(self):
        text = "China expanded its program."
        china = [
            e for e in ner.extract(text, backend="rule") if e["text"] == "China"
        ][0]
        self.assertEqual(text[china["start"]:china["end"]], "China")

    def test_empty_text(self):
        self.assertEqual(ner.extract("", backend="rule"), [])

    def test_default_backend_is_known(self):
        self.assertIn(ner.default_backend(), {"rule", "spacy"})


if __name__ == "__main__":
    unittest.main()
