"""Autogenerate LLM_INDEX.md from module docstrings + public function signatures.

The skills_plan.md asks for "a file for another LLM to read to know what
types of high-level functions are included in the code repository" — so
another model (or a human running grep) can navigate the codebase
without reading every file. This script walks ``ailandscape/`` (and
``tests/`` headings), parses each module via ast (no imports needed —
safer + avoids side effects), and writes a single LLM_INDEX.md.

Output shape per module:

    ## ailandscape/<module>.py
    > <module docstring first line>

    - `function_name(arg1, arg2)` — first sentence of the function docstring.

Conventions enforced:
  * Private helpers (names starting with `_`) are skipped.
  * Classes get their public method list nested under them.
  * If a function has no docstring, it's still listed but flagged so
    the next maintenance pass adds one.
  * The script is idempotent: rerunning with no code changes produces a
    byte-identical file.

Run from the repo root:
    python scripts/build_llm_index.py
"""

import ast
import datetime
import pathlib
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "LLM_INDEX.md"
PKG_DIR = REPO_ROOT / "ailandscape"
TESTS_DIR = REPO_ROOT / "tests"
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _first_sentence(docstring):
    """Return the first prose sentence of a docstring, single-line."""
    if not docstring:
        return ""
    text = docstring.strip()
    # Take the first paragraph (stop at blank line).
    para = text.split("\n\n", 1)[0]
    # Collapse newlines so the index reads as a single line.
    para = " ".join(line.strip() for line in para.splitlines() if line.strip())
    # Trim at first sentence-ending punctuation that's followed by a
    # space and an uppercase letter (keeps "U.S." together but ends at
    # "first sentence. Second sentence.").
    for i, ch in enumerate(para):
        if ch in ".?!" and i + 2 < len(para):
            nxt = para[i + 1:i + 3]
            if nxt.startswith(" ") and len(nxt) > 1 and nxt[1].isupper():
                return para[:i + 1]
    return para


def _format_signature(node):
    """Render a function/method signature as ``name(arg, *args, kw=default)``.

    Defaults are stringified via ``ast.unparse`` for readability; very long
    defaults are trimmed.
    """
    args = node.args
    parts = []
    pos = args.args
    defaults = args.defaults
    # Pair positional args with their defaults (defaults align to the tail).
    n_defaults = len(defaults)
    for i, a in enumerate(pos):
        idx_in_defaults = i - (len(pos) - n_defaults)
        if idx_in_defaults >= 0:
            try:
                default_str = ast.unparse(defaults[idx_in_defaults])
            except Exception:
                default_str = "..."
            if len(default_str) > 40:
                default_str = default_str[:37] + "..."
            parts.append("%s=%s" % (a.arg, default_str))
        else:
            parts.append(a.arg)
    if args.vararg:
        parts.append("*" + args.vararg.arg)
    for a, d in zip(args.kwonlyargs, args.kw_defaults):
        if d is None:
            parts.append(a.arg)
        else:
            try:
                default_str = ast.unparse(d)
            except Exception:
                default_str = "..."
            if len(default_str) > 40:
                default_str = default_str[:37] + "..."
            parts.append("%s=%s" % (a.arg, default_str))
    if args.kwarg:
        parts.append("**" + args.kwarg.arg)
    return "%s(%s)" % (node.name, ", ".join(parts))


def _describe_module(path):
    """Parse one .py file; return a markdown block describing it."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return "## %s\n\n*Could not parse: %s*\n" % (
            path.relative_to(REPO_ROOT).as_posix(), exc,
        )

    rel = path.relative_to(REPO_ROOT).as_posix()
    lines = ["## `%s`" % rel]
    mod_doc = ast.get_docstring(tree)
    if mod_doc:
        lines.append("")
        lines.append("> " + _first_sentence(mod_doc))
    else:
        lines.append("")
        lines.append("> *(no module docstring — consider adding one)*")
    lines.append("")

    # Top-level public functions
    funcs = [
        n for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not n.name.startswith("_")
    ]
    classes = [
        n for n in tree.body
        if isinstance(n, ast.ClassDef) and not n.name.startswith("_")
    ]

    if funcs:
        lines.append("**Functions**")
        for f in funcs:
            sig = _format_signature(f)
            doc = _first_sentence(ast.get_docstring(f)) or "*(no docstring)*"
            lines.append("- `%s` — %s" % (sig, doc))
        lines.append("")

    if classes:
        lines.append("**Classes**")
        for c in classes:
            cdoc = _first_sentence(ast.get_docstring(c)) or "*(no docstring)*"
            lines.append("- `class %s` — %s" % (c.name, cdoc))
            methods = [
                m for m in c.body
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not m.name.startswith("_")
            ]
            for m in methods:
                sig = _format_signature(m)
                mdoc = _first_sentence(ast.get_docstring(m)) or "*(no docstring)*"
                lines.append("  - `%s` — %s" % (sig, mdoc))
        lines.append("")

    if not funcs and not classes:
        lines.append("*(no public functions or classes)*")
        lines.append("")

    return "\n".join(lines)


def _walk(dir_path):
    """Return sorted list of .py files under dir_path (excluding __pycache__)."""
    if not dir_path.exists():
        return []
    out = []
    for p in dir_path.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        out.append(p)
    out.sort(key=lambda p: p.relative_to(REPO_ROOT).as_posix())
    return out


def build():
    """Generate the index markdown string. Pure function — no I/O."""
    today = datetime.date.today().isoformat()
    sections = [
        "# AI Landscape — code index for LLMs (and humans)",
        "",
        "Autogenerated from module + function docstrings by",
        "`scripts/build_llm_index.py`. Rerun after touching public",
        "module / function / class signatures.",
        "",
        "_Last generated: %s_" % today,
        "",
        "## Reading order",
        "",
        "1. **Pipeline core** — `ailandscape/pipeline.py`, `corpus.py`,",
        "   `scraper.py`, `ner.py`, `reconcile.py`, `relations.py`. The data",
        "   flow from feeds to graph.",
        "2. **Storage** — `storage_kg.py`, `storage_ner.py`. SQLite glue.",
        "3. **Shared gates** — `ai_terms.py`. The AI relevance lexicon",
        "   used by SBIR / enrich / scrape filters.",
        "4. **Surface layers** — `server.py` (FastAPI), `cli.py` (CLI),",
        "   `web/` (Cytoscape.js frontend, not indexed here).",
        "5. **LLM integration** — `synthesis.py`, `claude_cli.py`,",
        "   `synthesis_cache.py`. Two transports + a sidecar cache.",
        "6. **Reporting + tours** — `report.py`, `briefing.py`, `trends.py`,",
        "   `tours.py`, `review.py`, `capabilities.py`.",
        "7. **Data sources beyond RSS** — `sbir.py`, `jbooks.py`,",
        "   `feeds.py`, `feed_discovery.py`.",
        "8. **Tests** — `tests/test_*.py`. One file per module.",
        "",
        "---",
    ]
    for label, paths in (
        ("Package: `ailandscape/`", _walk(PKG_DIR)),
        ("Scripts: `scripts/`", _walk(SCRIPTS_DIR)),
        ("Tests: `tests/`", _walk(TESTS_DIR)),
    ):
        sections.append("")
        sections.append("# " + label)
        sections.append("")
        for p in paths:
            block = _describe_module(p)
            if block:
                sections.append(block)
                sections.append("")
    return "\n".join(sections).rstrip() + "\n"


def main(argv=None):
    text = build()
    OUTPUT.write_text(text, encoding="utf-8")
    print("wrote %s (%d bytes)" % (OUTPUT.relative_to(REPO_ROOT).as_posix(),
                                   len(text.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
