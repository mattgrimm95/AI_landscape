import unittest
import urllib.error

from ailandscape import jbooks


SAMPLE_TEXT = """
Department of Defense FY 2027 Budget Estimates
Defense Advanced Research Projects Agency
RDT&E, Defense-Wide

Exhibit R-2, RDT&E Budget Item Justification: PB 2027 DARPA
Program Element Number: 0602250D8Z   Program Element Name: Artificial Intelligence Initiative
This program element develops machine learning capabilities for sensor exploitation, autonomy
in contested environments, and human-machine teaming for command and control.

Exhibit R-2, RDT&E Budget Item Justification: PB 2027 DARPA
Program Element Number: 0603250D8Z   Program Element Name: Composite Materials
This program element develops ceramic-matrix composites for hypersonic airframes; the work is
limited to materials science and structural testing.
"""


class JBooksTest(unittest.TestCase):
    def test_is_ai_related_matches_machine_learning(self):
        self.assertTrue(
            jbooks.is_ai_related("uses machine learning for sensor fusion")
        )
        self.assertFalse(
            jbooks.is_ai_related(
                "uses ceramic composites and structural tests"
            )
        )

    def test_is_ai_acronym_case_sensitive(self):
        # The acronym filter must be case-sensitive: "ai" inside ordinary
        # words ("available", "maintain") should not fire.
        self.assertFalse(
            jbooks.is_ai_related("the system became available to maintain")
        )
        self.assertTrue(jbooks.is_ai_related("a new AI capability"))

    def test_is_rdte_requires_marker(self):
        self.assertTrue(jbooks.is_rdte("This is an RDT&E justification."))
        self.assertTrue(
            jbooks.is_rdte("Research, Development, Test and Evaluation")
        )
        self.assertFalse(
            jbooks.is_rdte("Procurement-only summary, no science here.")
        )

    def test_extract_program_elements_splits_on_headers(self):
        chunks = jbooks.extract_program_elements(SAMPLE_TEXT)
        titles = " ".join(c["title"] for c in chunks)
        self.assertIn("0602250D8Z", titles)
        self.assertIn("0603250D8Z", titles)

    def test_ai_articles_keeps_only_ai_program_elements(self):
        out = jbooks.ai_articles(
            SAMPLE_TEXT, "https://x.test/p.pdf", "FY2027", "Defense-Wide"
        )
        # Only the AI-themed PE produces an article.
        self.assertEqual(len(out), 1)
        self.assertIn("Artificial Intelligence", out[0]["title"])
        self.assertEqual(out[0]["metadata"]["data_source"], "J-Book")
        self.assertEqual(out[0]["metadata"]["fiscal_year"], "FY2027")
        self.assertEqual(out[0]["metadata"]["agency"], "Defense-Wide")
        self.assertEqual(out[0]["url"], "https://x.test/p.pdf")

    def test_non_rdte_document_yields_no_articles(self):
        text = "This procurement summary mentions machine learning briefly."
        # No RDT&E marker -> no articles regardless of AI mentions.
        self.assertEqual(
            jbooks.ai_articles(text, "u", "FY2027", "Air Force"), []
        )

    def test_find_pdf_links_resolves_relative_urls(self):
        html = b'<a href="r1_book.pdf">R1</a> <a href="/2027/r1.pdf">R1abs</a>'
        links = jbooks.find_pdf_links(html, "https://x.test/budget/")
        self.assertEqual(links, [
            "https://x.test/budget/r1_book.pdf",
            "https://x.test/2027/r1.pdf",
        ])

    def test_fetch_jbook_articles_raises_on_index_failure(self):
        def boom(url, timeout=None):
            raise urllib.error.URLError("connection refused")

        orig = jbooks._fetch_url
        jbooks._fetch_url = boom
        try:
            with self.assertRaises(jbooks.JBookError):
                jbooks.fetch_jbook_articles(
                    "https://x.test/", "FY2027", "Defense-Wide"
                )
        finally:
            jbooks._fetch_url = orig


if __name__ == "__main__":
    unittest.main()
