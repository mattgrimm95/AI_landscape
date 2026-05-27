"""Integration tests for ailandscape.explain.

These tests use the REAL `ailandscape` package as the fixture (rather
than mocking the AST) — the whole point of `explain` is to give an
accurate picture of THIS project, so the test of correctness IS
"does it correctly describe ailandscape?"

Assertions stay on invariants that are stable across reasonable
refactors (e.g. "synthesis has at least one CLI verb that uses it",
not "synthesis has exactly N CLI verbs") so the suite doesn't churn
on every cmd_X rename.
"""

import unittest

from ailandscape import explain


class TestExplainSystem(unittest.TestCase):
    """The system-overview path."""

    def test_explain_system_shape(self):
        data = explain.explain_system()
        self.assertEqual(data["target_type"], "system")
        self.assertEqual(data["target"], "ailandscape")
        self.assertIn("modules", data)
        self.assertIn("cli_verbs", data)
        self.assertIn("api_endpoints", data)
        self.assertIn("test_summary", data)

    def test_system_has_known_components(self):
        data = explain.explain_system()
        module_names = {m["module"] for m in data["modules"]}
        # The five most foundational modules — if any of these disappear
        # the project no longer works.
        for required in ("ailandscape.cli", "ailandscape.config",
                         "ailandscape.corpus", "ailandscape.pipeline",
                         "ailandscape.server"):
            self.assertIn(required, module_names,
                          "missing foundational module: " + required)

    def test_system_includes_new_explain_module(self):
        # The module we're testing should describe itself in the overview.
        data = explain.explain_system()
        module_names = {m["module"] for m in data["modules"]}
        self.assertIn("ailandscape.explain", module_names)

    def test_system_has_explain_verb(self):
        data = explain.explain_system()
        verbs = {v["verb"] for v in data["cli_verbs"]}
        self.assertIn("explain", verbs)
        # And a few of the foundational ones.
        for required in ("run", "rebuild", "stats", "serve"):
            self.assertIn(required, verbs)

    def test_system_has_api_endpoints(self):
        data = explain.explain_system()
        paths = {e["path"] for e in data["api_endpoints"]}
        self.assertGreater(len(paths), 0, "no API endpoints discovered")
        self.assertIn("/api/overview", paths)

    def test_system_test_summary_is_populated(self):
        data = explain.explain_system()
        s = data["test_summary"]
        # The suite itself must include itself in the count.
        self.assertGreater(s["test_files"], 0)
        self.assertGreater(s["test_count"], 0)


class TestExplainModule(unittest.TestCase):
    """The per-module deep-dive path."""

    def test_explain_known_small_module(self):
        # synthesis_cache is small and stable — a good fixture.
        data = explain.explain_module("synthesis_cache")
        self.assertEqual(data["target_type"], "module")
        self.assertEqual(data["target"], "ailandscape.synthesis_cache")
        self.assertTrue(data["file"].endswith("synthesis_cache.py"))
        self.assertGreater(len(data["public"]), 0,
                           "synthesis_cache should expose at least one public fn")

    def test_unknown_module_raises(self):
        with self.assertRaises(FileNotFoundError):
            explain.explain_module("definitely_not_a_real_module_xyz123")

    def test_imports_are_classified(self):
        data = explain.explain_module("pipeline")
        imp = data["imports"]
        self.assertIn("internal", imp)
        self.assertIn("external", imp)
        # pipeline definitely imports other ailandscape modules.
        self.assertGreater(len(imp["internal"]), 0)
        # And they should all start with ailandscape.
        for name in imp["internal"]:
            self.assertTrue(name.startswith("ailandscape"),
                            "expected ailandscape.* in internal, got " + name)

    def test_reverse_deps_for_config(self):
        # config is widely imported — every reverse dep should be ailandscape.*
        data = explain.explain_module("config")
        self.assertGreater(len(data["reverse_deps"]), 5,
                           "config should be a heavily-imported module")
        for dep in data["reverse_deps"]:
            self.assertTrue(dep.startswith("ailandscape."), dep)

    def test_cli_verbs_for_synthesis_module(self):
        # synthesize-daily and briefing --narrative both call synthesis.
        data = explain.explain_module("synthesis")
        verbs = {v["verb"] for v in data["cli_verbs"]}
        self.assertIn("synthesize-daily", verbs,
                      "synthesize-daily CLI verb should be linked to synthesis")

    def test_api_endpoints_for_synthesis_module(self):
        # /api/hype and /api/briefing/narrative both call synthesis.
        data = explain.explain_module("synthesis")
        paths = {e["path"] for e in data["api_endpoints"]}
        self.assertGreater(
            len(paths), 0,
            "synthesis should be linked to at least one API endpoint"
        )

    def test_tests_section_is_populated(self):
        # The explain module's own tests (this file) reference it.
        data = explain.explain_module("explain")
        files = {f["file"] for f in data["tests"]["files"]}
        # At least one path that ends with test_explain.py.
        matches = [f for f in files if f.endswith("test_explain.py")]
        self.assertEqual(len(matches), 1,
                         "explain should be covered by tests/test_explain.py")

    def test_trust_signals(self):
        data = explain.explain_module("synthesis_cache")
        s = data["trust_signals"]
        self.assertIn("has_module_docstring", s)
        self.assertIn("public_definitions", s)
        self.assertIn("test_files", s)
        self.assertIn("test_count", s)
        self.assertIn("todo_markers", s)
        self.assertIn("last_commit", s)
        # synthesis_cache has a docstring (we wrote it ourselves).
        self.assertTrue(s["has_module_docstring"])


class TestExplainDispatcher(unittest.TestCase):
    def test_default_is_system(self):
        self.assertEqual(explain.explain("system")["target_type"], "system")
        self.assertEqual(explain.explain("")["target_type"], "system")
        self.assertEqual(explain.explain("all")["target_type"], "system")
        self.assertEqual(explain.explain("overview")["target_type"], "system")

    def test_module_target(self):
        self.assertEqual(
            explain.explain("synthesis_cache")["target_type"], "module"
        )


class TestRender(unittest.TestCase):
    def test_render_system_is_text(self):
        text = explain.render(explain.explain_system())
        self.assertIsInstance(text, str)
        self.assertIn("System overview", text)
        self.assertIn("ailandscape", text)

    def test_render_module_mentions_target(self):
        data = explain.explain_module("synthesis_cache")
        text = explain.render(data)
        self.assertIn("synthesis_cache", text)
        self.assertIn("Trust signals", text)
        self.assertIn("Tests (", text)

    def test_render_module_with_no_imports_does_not_crash(self):
        # Pass a hand-built minimal dict — protects the renderer from
        # surprise when a real module ever happens to have zero internal
        # AND zero external imports.
        data = {
            "target_type": "module",
            "target": "ailandscape.fake",
            "file": "ailandscape/fake.py",
            "description": "",
            "public": [],
            "imports": {"internal": [], "external": []},
            "reverse_deps": [],
            "cli_verbs": [],
            "api_endpoints": [],
            "tests": {"files": [], "total_files": 0, "total_tests": 0},
            "trust_signals": {
                "has_module_docstring": False,
                "public_definitions": 0,
                "test_files": 0,
                "test_count": 0,
                "todo_markers": 0,
                "last_commit": None,
            },
        }
        text = explain.render(data)
        self.assertIn("ailandscape.fake", text)
        # The "consider adding a test" hint should appear.
        self.assertIn("consider adding", text)


class TestNarrativePrompt(unittest.TestCase):
    def test_prompt_includes_rendered_report(self):
        data = explain.explain_module("synthesis_cache")
        prompt = explain._narrative_prompt(data)
        self.assertIn("synthesis_cache", prompt)
        self.assertIn("STRUCTURAL REPORT", prompt)
        # The render is embedded — pick a section header that should survive.
        self.assertIn("Trust signals", prompt)


if __name__ == "__main__":
    unittest.main()
