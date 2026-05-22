import importlib.util
import unittest

from ailandscape import config, scraper

SAMPLE = config.ROOT / "samples" / "sample_feed.xml"
_HAS_TRAFILATURA = importlib.util.find_spec("trafilatura") is not None


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

    def test_content_hash_uses_url_and_title_only(self):
        article = {"url": "u", "title": "t", "raw_text": "x"}
        # Body text does not affect the hash, so an article can be
        # de-duplicated before its page is fetched.
        self.assertEqual(
            scraper.content_hash(article),
            scraper.content_hash(dict(article, raw_text="completely different")),
        )
        # URL or title changes do produce a different hash.
        self.assertNotEqual(
            scraper.content_hash(article),
            scraper.content_hash(dict(article, title="different")),
        )
        self.assertNotEqual(
            scraper.content_hash(article),
            scraper.content_hash(dict(article, url="other")),
        )

    def test_extract_text_from_html_empty_returns_fallback(self):
        self.assertEqual(scraper.extract_text_from_html("", "FALLBACK"), "FALLBACK")

    @unittest.skipUnless(_HAS_TRAFILATURA, "trafilatura not installed")
    def test_extract_text_from_html_drops_boilerplate(self):
        html = (
            "<html><body>"
            "<nav>Home About Subscribe Login</nav>"
            "<article><h1>Defense AI Update</h1>"
            "<p>The Pentagon awarded a major contract to develop autonomous "
            "drones for reconnaissance missions across multiple theaters.</p>"
            "<p>Officials said the program will run for several years and "
            "involve close cooperation with allied nations.</p></article>"
            "<footer>Copyright 2026 Example News. Contact webmaster@example.com.</footer>"
            "</body></html>"
        )
        text = scraper.extract_text_from_html(html, fallback="FALLBACK")
        self.assertIn("Pentagon awarded a major contract", text)
        self.assertNotIn("webmaster@example.com", text)

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
