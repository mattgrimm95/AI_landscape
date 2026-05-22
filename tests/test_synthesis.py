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


if __name__ == "__main__":
    unittest.main()
