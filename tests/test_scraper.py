import unittest

from ailandscape import config, scraper

SAMPLE = config.ROOT / "samples" / "sample_feed.xml"


class ScraperTest(unittest.TestCase):
    def test_parse_sample_feed(self):
        articles = scraper.scrape_fixture(SAMPLE, "Sample Feed")
        self.assertEqual(len(articles), 4)
        first = articles[0]
        self.assertIn("Pentagon", first["title"])
        self.assertEqual(first["source"], "Sample Feed")
        self.assertTrue(first["url"].startswith("https://"))
        # content:encoded is preferred over description, with HTML stripped.
        self.assertIn("Artificial Intelligence", first["raw_text"])
        self.assertNotIn("<", first["raw_text"])

    def test_html_to_text(self):
        self.assertEqual(
            scraper.html_to_text("<p>Hello <b>world</b></p>"), "Hello world"
        )
        self.assertEqual(scraper.html_to_text(""), "")

    def test_content_hash_is_stable_and_distinct(self):
        article = {"url": "u", "title": "t", "raw_text": "x"}
        self.assertEqual(
            scraper.content_hash(article), scraper.content_hash(dict(article))
        )
        changed = dict(article, title="different")
        self.assertNotEqual(
            scraper.content_hash(article), scraper.content_hash(changed)
        )

    def test_parse_atom_feed(self):
        atom = (
            '<?xml version="1.0"?>'
            '<feed xmlns="http://www.w3.org/2005/Atom">'
            "<entry><title>Atom Title</title>"
            '<link href="https://atom.example/1"/>'
            "<summary>Reporting on China and NATO.</summary>"
            "<updated>2026-05-21</updated></entry></feed>"
        )
        articles = scraper.parse_feed(atom, "Atom Source")
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["title"], "Atom Title")
        self.assertEqual(articles[0]["url"], "https://atom.example/1")

    def test_invalid_xml_raises_feed_error(self):
        with self.assertRaises(scraper.FeedError):
            scraper.parse_feed("not xml at all", "Bad")


if __name__ == "__main__":
    unittest.main()
