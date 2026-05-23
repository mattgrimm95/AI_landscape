import json
import os
import unittest
import urllib.error
import urllib.request

from ailandscape import synthesis


class SynthesisTest(unittest.TestCase):
    def setUp(self):
        self._orig_key = os.environ.get("ANTHROPIC_API_KEY")
        self._orig_urlopen = urllib.request.urlopen

    def tearDown(self):
        if self._orig_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = self._orig_key
        urllib.request.urlopen = self._orig_urlopen

    def _briefing(self):
        return {
            "totals": {"documents": 5, "entities": 10, "typed_relations": 3},
            "trending_topics": [{"name": "Computer Vision"}],
            "top_entities": [{"name": "Pentagon"}],
            "contract_awards": [
                {"subject": "Pentagon", "relation": "awards_contract",
                 "object": "Anduril"},
            ],
            "key_relationships": [],
        }

    def test_not_configured_without_key(self):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        self.assertFalse(synthesis.is_configured())

    def test_summarize_raises_without_key(self):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        with self.assertRaises(synthesis.SynthesisError):
            synthesis.summarize_briefing(self._briefing())

    def test_summarize_returns_text_with_mocked_api(self):
        os.environ["ANTHROPIC_API_KEY"] = "test-key-not-real"
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

            def read(self):
                return json.dumps(
                    {"content": [{"type": "text",
                                  "text": "An analyst summary."}]}
                ).encode("utf-8")

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        urllib.request.urlopen = fake_urlopen
        result = synthesis.summarize_briefing(self._briefing())
        self.assertEqual(result, "An analyst summary.")
        self.assertEqual(captured["url"], synthesis.API_URL)
        # The briefing data is carried into the prompt.
        prompt = captured["body"]["messages"][0]["content"]
        self.assertIn("Computer Vision", prompt)
        self.assertIn("Anduril", prompt)

    def test_api_error_becomes_synthesis_error(self):
        os.environ["ANTHROPIC_API_KEY"] = "test-key-not-real"

        def boom(request, timeout=None):
            raise urllib.error.HTTPError(
                synthesis.API_URL, 401, "Unauthorized", {}, None
            )

        urllib.request.urlopen = boom
        with self.assertRaises(synthesis.SynthesisError):
            synthesis.summarize_briefing(self._briefing())


class HypeSynthesisTest(unittest.TestCase):
    def setUp(self):
        self._orig_key = os.environ.get("ANTHROPIC_API_KEY")
        self._orig_urlopen = urllib.request.urlopen

    def tearDown(self):
        if self._orig_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = self._orig_key
        urllib.request.urlopen = self._orig_urlopen

    def _docs(self):
        return [
            {"title": "Lockheed wins $2B AI contract",
             "source": "DefenseScoop",
             "raw_text": "The Pentagon awarded Lockheed Martin a major AI deal."},
            {"title": "Anthropic ships Claude 5",
             "source": "TechCrunch",
             "raw_text": "Claude 5 sets a new bar on agentic tasks."},
        ]

    def test_hype_raises_without_key(self):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        with self.assertRaises(synthesis.SynthesisError):
            synthesis.summarize_hype(self._docs())

    def test_hype_uses_recent_doc_titles_and_snippets(self):
        os.environ["ANTHROPIC_API_KEY"] = "test-key-not-real"
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

            def read(self):
                return json.dumps(
                    {"content": [{"type": "text", "text": "Big day in AI!"}]}
                ).encode("utf-8")

        def fake_urlopen(request, timeout=None):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        urllib.request.urlopen = fake_urlopen
        result = synthesis.summarize_hype(
            self._docs(), sbir_funding={"awards": 4, "total_amount": 12345678}
        )
        self.assertEqual(result, "Big day in AI!")
        prompt = captured["body"]["messages"][0]["content"]
        # The hype prompt must include the headline-style document titles
        # and the SBIR funding total so Claude has something to be excited
        # about. The "hype" framing language is also part of the prompt.
        self.assertIn("Lockheed wins $2B AI contract", prompt)
        self.assertIn("Anthropic ships Claude 5", prompt)
        self.assertIn("$12,345,678", prompt)
        self.assertIn("hype", prompt.lower())

    def test_hype_quiet_day_still_produces_a_prompt(self):
        # No documents should still produce a non-empty prompt (the prompt
        # itself acknowledges a quiet day and asks for a sober pick).
        os.environ["ANTHROPIC_API_KEY"] = "test-key-not-real"
        captured = {}

        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *_a): return False
            def read(self):
                return json.dumps(
                    {"content": [{"type": "text", "text": "Quiet day."}]}
                ).encode("utf-8")

        def fake_urlopen(request, timeout=None):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        urllib.request.urlopen = fake_urlopen
        result = synthesis.summarize_hype([])
        self.assertEqual(result, "Quiet day.")
        prompt = captured["body"]["messages"][0]["content"]
        self.assertIn("no documents", prompt)


if __name__ == "__main__":
    unittest.main()
