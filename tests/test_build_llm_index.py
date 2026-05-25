"""Smoke test for scripts/build_llm_index.py.

The script walks the codebase + parses every .py via ast. It must:
  * Not crash on the real codebase.
  * Produce non-trivial markdown (a header per module, with sensible
    reading order).
  * Be deterministic — running it twice in a row produces byte-identical
    output (except for the timestamp, which we strip before comparing).
"""

import importlib.util
import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "build_llm_index.py"


def _load():
    """Import scripts/build_llm_index.py as a module (it's outside the pkg)."""
    spec = importlib.util.spec_from_file_location("build_llm_index", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_llm_index"] = mod
    spec.loader.exec_module(mod)
    return mod


class BuildLlmIndexTest(unittest.TestCase):
    def setUp(self):
        self.mod = _load()

    def test_build_returns_markdown(self):
        text = self.mod.build()
        # Has a top-level title.
        self.assertIn("# AI Landscape — code index for LLMs", text)
        # Mentions the major package files.
        for must in ("pipeline.py", "corpus.py", "ner.py",
                     "reconcile.py", "ai_terms.py", "synthesis.py"):
            self.assertIn(must, text, "should mention %s" % must)
        # Renders some function signatures.
        self.assertIn("`is_ai_relevant(text)`", text)

    def test_first_sentence_trims_at_sentence_boundary(self):
        # First sentence is everything up to the first ". <Capital>" pair.
        out = self.mod._first_sentence("One. Two.")
        self.assertEqual(out, "One.")

    def test_first_sentence_keeps_us_inside_a_sentence(self):
        # "U.S." should NOT be treated as a sentence end — the regex
        # requires the next char after the . to be a space + uppercase.
        # Period followed by uppercase but with NO space is fine.
        out = self.mod._first_sentence("The U.S. funded it. The second sentence.")
        self.assertEqual(out, "The U.S. funded it.")

    def test_run_is_idempotent(self):
        # Strip the timestamp line before comparing -- everything else
        # should be byte-identical run-to-run.
        a = self.mod.build()
        b = self.mod.build()
        def _strip_ts(t):
            return "\n".join(
                line for line in t.splitlines()
                if not line.startswith("_Last generated:")
            )
        self.assertEqual(_strip_ts(a), _strip_ts(b))


if __name__ == "__main__":
    unittest.main()
