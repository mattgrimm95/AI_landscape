import json
import os
import unittest
import urllib.error
import urllib.request

from ailandscape import claude_cli, synthesis


class SynthesisTest(unittest.TestCase):
    def setUp(self):
        self._orig_key = os.environ.get("ANTHROPIC_API_KEY")
        self._orig_urlopen = urllib.request.urlopen
        # These tests exercise the Anthropic-API transport. Disable the
        # Claude Code CLI path so the transport router doesn't pick it
        # up on a developer machine that has the CLI installed.
        self._orig_cli_available = claude_cli.is_available
        claude_cli.is_available = lambda: False

    def tearDown(self):
        if self._orig_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = self._orig_key
        urllib.request.urlopen = self._orig_urlopen
        claude_cli.is_available = self._orig_cli_available

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
        # Exercise the API path; disable the CLI transport on machines
        # where it happens to be installed.
        self._orig_cli_available = claude_cli.is_available
        claude_cli.is_available = lambda: False

    def tearDown(self):
        if self._orig_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = self._orig_key
        urllib.request.urlopen = self._orig_urlopen
        claude_cli.is_available = self._orig_cli_available

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


class CliTransportTest(unittest.TestCase):
    """The CLI-first router: when claude_cli is available, prefer it."""

    def setUp(self):
        # Force the CLI to be "available" regardless of host.
        self._orig_available = claude_cli.is_available
        self._orig_summarize = claude_cli.summarize
        claude_cli.is_available = lambda: True

    def tearDown(self):
        claude_cli.is_available = self._orig_available
        claude_cli.summarize = self._orig_summarize

    def test_transport_picks_cli_when_available(self):
        self.assertEqual(synthesis.transport(), synthesis.TRANSPORT_CLI)

    def test_summarize_briefing_routes_through_cli(self):
        captured = {}

        def fake_summarize(prompt, **kwargs):
            captured["prompt"] = prompt
            return "Cli-written narrative."

        claude_cli.summarize = fake_summarize
        result = synthesis.summarize_briefing({
            "totals": {"documents": 5, "entities": 10, "typed_relations": 3},
            "trending_topics": [{"name": "Computer Vision"}],
            "top_entities": [{"name": "Pentagon"}],
            "contract_awards": [],
            "key_relationships": [],
        })
        self.assertEqual(result, "Cli-written narrative.")
        self.assertIn("Computer Vision", captured["prompt"])

    def test_summarize_hype_routes_through_cli(self):
        captured = {}

        def fake_summarize(prompt, **kwargs):
            captured["prompt"] = prompt
            return "Cli-written hype!"

        claude_cli.summarize = fake_summarize
        docs = [
            {"title": "Big day", "source": "X",
             "raw_text": "Pentagon awarded Lockheed a contract."},
        ]
        result = synthesis.summarize_hype(docs)
        self.assertEqual(result, "Cli-written hype!")
        self.assertIn("Big day", captured["prompt"])

    def test_cli_error_becomes_synthesis_error(self):
        def boom(*a, **kw):
            raise claude_cli.ClaudeCliError("CLI not logged in")

        claude_cli.summarize = boom
        with self.assertRaises(synthesis.SynthesisError) as ctx:
            synthesis.summarize_hype([])
        self.assertIn("logged in", str(ctx.exception))


class TransportPriorityTest(unittest.TestCase):
    """The router prefers CLI > API > None and reports the chosen one."""

    def setUp(self):
        self._orig_key = os.environ.get("ANTHROPIC_API_KEY")
        self._orig_available = claude_cli.is_available

    def tearDown(self):
        if self._orig_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = self._orig_key
        claude_cli.is_available = self._orig_available

    def test_none_when_neither_transport_present(self):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        claude_cli.is_available = lambda: False
        self.assertIsNone(synthesis.transport())
        self.assertFalse(synthesis.is_configured())

    def test_api_when_only_key_present(self):
        os.environ["ANTHROPIC_API_KEY"] = "fake"
        claude_cli.is_available = lambda: False
        self.assertEqual(synthesis.transport(), synthesis.TRANSPORT_API)

    def test_cli_preferred_even_when_key_also_present(self):
        os.environ["ANTHROPIC_API_KEY"] = "fake"
        claude_cli.is_available = lambda: True
        self.assertEqual(synthesis.transport(), synthesis.TRANSPORT_CLI)


if __name__ == "__main__":
    unittest.main()
