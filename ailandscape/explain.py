"""Structural explanation of the AI Landscape codebase.

The `explain` CLI verb renders a deterministic structural report about
some part of the system — a module, the system overview, the public
surface, and (importantly) the trust signals an operator wants to see
before believing the code is wired the way they think it is:

  - what does this module do (description from docstring)
  - what does it depend on (imports — internal vs. external)
  - what depends on it (reverse deps — the importers)
  - which CLI verbs reach it
  - which API endpoints reach it
  - how is it verified (test files + test count)
  - trust signals (docstring presence, TODO markers, last commit)

The output is a plain dict (easy to test, easy to feed to an LLM); a
separate `render` function formats it to readable text. With
`--narrative`, the same dict is also passed to Claude via
`claude_cli.summarize` for a prose explanation. The structural output
is always emitted first so the operator never depends on the LLM being
available.

Modeled on `briefing.py`'s "structural dict → render → optional LLM
narrative" pattern so the wiring is the same as the rest of the project.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess


PACKAGE = "ailandscape"
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


# --- AST helpers ------------------------------------------------------------

def _first_line(text):
    """First non-blank line of a docstring, stripped."""
    if not text:
        return ""
    for line in text.strip().splitlines():
        s = line.strip()
        if s:
            return s
    return ""


def _signature(node):
    """Render an ast.FunctionDef as `name(args) -> ret` using ast.unparse."""
    try:
        args = ast.unparse(node.args)
    except Exception:
        args = "..."
    ret = ""
    if node.returns is not None:
        try:
            ret = " -> " + ast.unparse(node.returns)
        except Exception:
            ret = ""
    return "{}({}){}".format(node.name, args, ret)


def _public_definitions(tree):
    """List of {kind, name, signature, doc} for public top-level fns + classes."""
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_"):
                continue
            out.append({
                "kind": "function",
                "name": node.name,
                "signature": _signature(node),
                "doc": _first_line(ast.get_docstring(node)),
            })
        elif isinstance(node, ast.ClassDef):
            if node.name.startswith("_"):
                continue
            out.append({
                "kind": "class",
                "name": node.name,
                "signature": node.name,
                "doc": _first_line(ast.get_docstring(node)),
            })
    return out


def _classify_imports(tree):
    """Return {'internal': sorted list, 'external': sorted list}.

    Internal = anything under the `ailandscape` package (including relative
    imports). External = everything else, dedup'd to top-level package name.
    """
    internal, external = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                top = n.name.split(".")[0]
                if top == PACKAGE:
                    internal.add(n.name)
                else:
                    external.add(top)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            # Relative: `from . import synthesis` → ailandscape.synthesis
            if node.level > 0:
                if mod:
                    internal.add("{}.{}".format(PACKAGE, mod))
                else:
                    for n in node.names:
                        internal.add("{}.{}".format(PACKAGE, n.name))
            elif mod.startswith(PACKAGE):
                internal.add(mod)
            elif mod:
                external.add(mod.split(".")[0])
    return {"internal": sorted(internal), "external": sorted(external)}


# --- module discovery -------------------------------------------------------

def _module_file(short_or_full):
    """Resolve a name to a .py file under ailandscape/, or None."""
    name = short_or_full
    if not name.startswith(PACKAGE + ".") and name != PACKAGE:
        name = "{}.{}".format(PACKAGE, name)
    parts = name.split(".")
    path = REPO_ROOT.joinpath(*parts).with_suffix(".py")
    if path.exists():
        return path
    init = REPO_ROOT.joinpath(*parts) / "__init__.py"
    if init.exists():
        return init
    return None


def _all_modules():
    """[(full_module_name, path), ...] for every .py in ailandscape/."""
    out = []
    pkg_dir = REPO_ROOT / PACKAGE
    for p in sorted(pkg_dir.rglob("*.py")):
        rel = p.relative_to(REPO_ROOT).with_suffix("")
        mod = ".".join(rel.parts)
        out.append((mod, p))
    return out


# --- cross-module analyses --------------------------------------------------

def _reverse_dependencies(target_full):
    """Find ailandscape.* modules that import `target_full`."""
    short = target_full.split(".")[-1]
    out = set()
    for mod, path in _all_modules():
        if mod == target_full:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                # `from ailandscape.synthesis import X`
                if node.module == target_full:
                    out.add(mod)
                    break
                # `from . import synthesis`
                if node.level > 0 and not node.module:
                    if any(n.name == short for n in node.names):
                        out.add(mod)
                        break
                # `from .synthesis import X` (relative form)
                if node.level > 0 and node.module == short:
                    out.add(mod)
                    break
            elif isinstance(node, ast.Import):
                for n in node.names:
                    if n.name == target_full:
                        out.add(mod)
                        break
    return sorted(out)


def _tests_for_module(target_full):
    """Find test_*.py files that reference the target + count their tests.

    Uses AST so multi-name imports (`from ailandscape import a, b, c`) are
    recognized — a substring match on `"from ailandscape import b"` would
    miss that, and did, in an earlier draft.
    """
    short = target_full.split(".")[-1]
    out = []
    tests_dir = REPO_ROOT / "tests"
    if not tests_dir.exists():
        return {"files": [], "total_files": 0, "total_tests": 0}
    for path in sorted(tests_dir.glob("test_*.py")):
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (OSError, SyntaxError):
            continue
        if not _references_module(tree, target_full, short):
            continue
        count = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                count += 1
        out.append({
            "file": str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
            "tests": count,
        })
    return {
        "files": out,
        "total_files": len(out),
        "total_tests": sum(f["tests"] for f in out),
    }


def _references_module(tree, target_full, short):
    """True if `tree` imports or attribute-references the target module."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # `from ailandscape import synthesis [, other_name, ...]`
            if node.module == PACKAGE and not node.level:
                if any(n.name == short for n in node.names):
                    return True
            # `from ailandscape.synthesis import X`
            if node.module == target_full:
                return True
            # `from .synthesis import X` (relative form within package)
            if node.level > 0 and node.module == short:
                return True
            # `from . import synthesis` (relative form within package)
            if node.level > 0 and not node.module:
                if any(n.name == short for n in node.names):
                    return True
        elif isinstance(node, ast.Import):
            # `import ailandscape.synthesis [as alias]`
            for n in node.names:
                if n.name == target_full:
                    return True
        elif isinstance(node, ast.Attribute):
            # `ailandscape.synthesis.X` attribute access on the package.
            if (isinstance(node.value, ast.Attribute)
                    and isinstance(node.value.value, ast.Name)
                    and node.value.value.id == PACKAGE
                    and node.value.attr == short):
                return True
    return False


def _cli_verbs_for_module(target_full):
    """CLI verbs whose cmd_X function body references the target module."""
    short = target_full.split(".")[-1]
    cli_path = REPO_ROOT / PACKAGE / "cli.py"
    if not cli_path.exists():
        return []
    try:
        tree = ast.parse(cli_path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return []
    # Step 1: for each cmd_X function, collect attribute calls like `short.foo`.
    cmd_uses = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("cmd_"):
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Attribute)
                        and isinstance(inner.value, ast.Name)
                        and inner.value.id == short):
                    cmd_uses.setdefault(node.name, set()).add(inner.attr)
    if not cmd_uses:
        return []
    # Step 2: map cmd_X → verb name via `<parser>.set_defaults(func=cmd_X)`
    # combined with `<parser> = sub.add_parser("<verb>", ...)`.
    parser_var_to_verb = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr == "add_parser"
                and node.value.args
                and isinstance(node.value.args[0], ast.Constant)
                and node.targets
                and isinstance(node.targets[0], ast.Name)):
            parser_var_to_verb[node.targets[0].id] = node.value.args[0].value
    cmd_to_verb = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "set_defaults"
                and isinstance(node.func.value, ast.Name)):
            for kw in node.keywords:
                if kw.arg == "func" and isinstance(kw.value, ast.Name):
                    cmd_to_verb[kw.value.id] = parser_var_to_verb.get(
                        node.func.value.id, ""
                    )
    out = []
    for cmd, calls in sorted(cmd_uses.items()):
        verb = cmd_to_verb.get(cmd) or cmd.replace("cmd_", "").replace("_", "-")
        out.append({
            "verb": verb,
            "cmd_function": cmd,
            "calls": sorted(calls),
        })
    return out


def _api_endpoints_for_module(target_full):
    """FastAPI endpoints in server.py whose handler references the target."""
    short = target_full.split(".")[-1]
    server_path = REPO_ROOT / PACKAGE / "server.py"
    if not server_path.exists():
        return []
    try:
        tree = ast.parse(server_path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return []
    out = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        method, path = None, None
        for dec in node.decorator_list:
            if (isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr in ("get", "post", "put", "delete", "patch")
                    and dec.args
                    and isinstance(dec.args[0], ast.Constant)):
                method = dec.func.attr.upper()
                path = dec.args[0].value
                break
        if not path:
            continue
        calls = set()
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Attribute)
                    and isinstance(inner.value, ast.Name)
                    and inner.value.id == short):
                calls.add(inner.attr)
        if calls:
            out.append({
                "method": method,
                "path": path,
                "handler": node.name,
                "calls": sorted(calls),
            })
    return sorted(out, key=lambda e: e["path"])


def _last_commit(path):
    """`git log -1` for a single file. Returns dict or None."""
    try:
        rel = path.relative_to(REPO_ROOT)
    except ValueError:
        rel = path
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%h%x09%ai%x09%s", "--", str(rel)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        sha, date_full, subject = result.stdout.strip().split("\t", 2)
    except ValueError:
        return None
    return {
        "sha": sha,
        "date": date_full.split()[0],
        "subject": subject,
    }


def _trust_signals(target_full, tree, path):
    text = path.read_text(encoding="utf-8")
    tests = _tests_for_module(target_full)
    public_count = 0
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not n.name.startswith("_"):
                public_count += 1
    todo_markers = sum(text.count(m) for m in ("TODO", "FIXME", "XXX", "HACK"))
    return {
        "has_module_docstring": ast.get_docstring(tree) is not None,
        "public_definitions": public_count,
        "test_files": tests["total_files"],
        "test_count": tests["total_tests"],
        "todo_markers": todo_markers,
        "last_commit": _last_commit(path),
    }


# --- public entry points ----------------------------------------------------

def explain_module(name):
    """Build the structural report dict for one ailandscape module."""
    path = _module_file(name)
    if path is None:
        raise FileNotFoundError(
            "no module named {!r} in {}".format(name, REPO_ROOT / PACKAGE)
        )
    target_full = name if name.startswith(PACKAGE + ".") else "{}.{}".format(PACKAGE, name)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        "target_type": "module",
        "target": target_full,
        "file": str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
        "description": _first_line(ast.get_docstring(tree)),
        "public": _public_definitions(tree),
        "imports": _classify_imports(tree),
        "reverse_deps": _reverse_dependencies(target_full),
        "cli_verbs": _cli_verbs_for_module(target_full),
        "api_endpoints": _api_endpoints_for_module(target_full),
        "tests": _tests_for_module(target_full),
        "trust_signals": _trust_signals(target_full, tree, path),
    }


def explain_system():
    """Build the system-wide overview dict."""
    modules = []
    for mod, path in _all_modules():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, OSError):
            continue
        public_count = 0
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not n.name.startswith("_"):
                    public_count += 1
        modules.append({
            "module": mod,
            "file": str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
            "description": _first_line(ast.get_docstring(tree)),
            "public_count": public_count,
        })

    cli_path = REPO_ROOT / PACKAGE / "cli.py"
    verbs = []
    if cli_path.exists():
        try:
            cli_tree = ast.parse(cli_path.read_text(encoding="utf-8"))
            for node in ast.walk(cli_tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "add_parser"
                        and node.args
                        and isinstance(node.args[0], ast.Constant)):
                    help_text = ""
                    for kw in node.keywords:
                        if kw.arg == "help" and isinstance(kw.value, ast.Constant):
                            help_text = kw.value.value
                    verbs.append({"verb": node.args[0].value, "help": help_text})
        except (SyntaxError, OSError):
            pass

    server_path = REPO_ROOT / PACKAGE / "server.py"
    endpoints = []
    if server_path.exists():
        try:
            server_tree = ast.parse(server_path.read_text(encoding="utf-8"))
            for node in server_tree.body:
                if not isinstance(node, ast.FunctionDef):
                    continue
                for dec in node.decorator_list:
                    if (isinstance(dec, ast.Call)
                            and isinstance(dec.func, ast.Attribute)
                            and dec.func.attr in ("get", "post", "put", "delete", "patch")
                            and dec.args
                            and isinstance(dec.args[0], ast.Constant)):
                        endpoints.append({
                            "method": dec.func.attr.upper(),
                            "path": dec.args[0].value,
                            "handler": node.name,
                            "doc": _first_line(ast.get_docstring(node)),
                        })
                        break
        except (SyntaxError, OSError):
            pass

    test_files, total_tests = [], 0
    tests_dir = REPO_ROOT / "tests"
    if tests_dir.exists():
        for p in sorted(tests_dir.glob("test_*.py")):
            test_files.append(str(p.relative_to(REPO_ROOT)).replace("\\", "/"))
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except (SyntaxError, OSError):
                continue
            for n in ast.walk(tree):
                if isinstance(n, ast.FunctionDef) and n.name.startswith("test_"):
                    total_tests += 1

    return {
        "target_type": "system",
        "target": PACKAGE,
        "modules": modules,
        "cli_verbs": sorted(verbs, key=lambda v: v["verb"]),
        "api_endpoints": sorted(endpoints, key=lambda e: e["path"]),
        "test_summary": {
            "test_files": len(test_files),
            "test_count": total_tests,
            "files": test_files,
        },
    }


def explain(target):
    """Dispatcher. 'system' / '' / 'all' → overview; else treat as module."""
    if not target or target in ("system", "all", "overview"):
        return explain_system()
    return explain_module(target)


# --- rendering --------------------------------------------------------------

def render(data):
    """Format an explain dict as readable text."""
    if data.get("target_type") == "system":
        return _render_system(data)
    return _render_module(data)


def _render_module(d):
    lines = []
    lines.append("## {}".format(d["target"]))
    lines.append("")
    lines.append("File: {}".format(d["file"]))
    if d["description"]:
        lines.append("Description: {}".format(d["description"]))
    lines.append("")

    if d["public"]:
        lines.append("### Public definitions ({})".format(len(d["public"])))
        for p in d["public"]:
            kind = "class" if p["kind"] == "class" else "fn"
            doc = " — {}".format(p["doc"]) if p["doc"] else ""
            lines.append("  - [{}] {}{}".format(kind, p["signature"], doc))
        lines.append("")

    imp = d["imports"]
    if imp["internal"] or imp["external"]:
        lines.append("### Imports")
        if imp["internal"]:
            lines.append("  internal: {}".format(", ".join(imp["internal"])))
        if imp["external"]:
            lines.append("  external: {}".format(", ".join(imp["external"])))
        lines.append("")

    if d["reverse_deps"]:
        lines.append("### Reverse deps — modules that import this ({})".format(
            len(d["reverse_deps"])
        ))
        for r in d["reverse_deps"]:
            lines.append("  - {}".format(r))
        lines.append("")

    if d["cli_verbs"]:
        lines.append("### CLI verbs that use this module ({})".format(len(d["cli_verbs"])))
        for v in d["cli_verbs"]:
            lines.append("  - {} ({}; calls: {})".format(
                v["verb"], v["cmd_function"], ", ".join(v["calls"])
            ))
        lines.append("")

    if d["api_endpoints"]:
        lines.append("### API endpoints that use this module ({})".format(
            len(d["api_endpoints"])
        ))
        for e in d["api_endpoints"]:
            lines.append("  - {} {} → {} (calls: {})".format(
                e["method"], e["path"], e["handler"], ", ".join(e["calls"])
            ))
        lines.append("")

    t = d["tests"]
    lines.append("### Tests ({} files, {} tests)".format(
        t["total_files"], t["total_tests"]
    ))
    for f in t["files"]:
        lines.append("  - {} ({} tests)".format(f["file"], f["tests"]))
    if not t["files"]:
        lines.append("  (no test file references this module — consider adding one)")
    lines.append("")

    s = d["trust_signals"]
    lines.append("### Trust signals")
    lines.append("  module docstring:           {}".format(
        "yes" if s["has_module_docstring"] else "NO — module has no top-level docstring"
    ))
    lines.append("  public definitions:         {}".format(s["public_definitions"]))
    lines.append("  test coverage:              {} files, {} tests".format(
        s["test_files"], s["test_count"]
    ))
    lines.append("  TODO/FIXME/XXX/HACK markers:{:>4}".format(s["todo_markers"]))
    if s["last_commit"]:
        c = s["last_commit"]
        lines.append("  last commit:                {}  {}  {}".format(
            c["sha"], c["date"], c["subject"]
        ))
    else:
        lines.append("  last commit:                (not in git or git unavailable)")
    return "\n".join(lines)


def _render_system(d):
    lines = []
    lines.append("## System overview: {}".format(d["target"]))
    lines.append("")
    lines.append("Counts: {} modules, {} CLI verbs, {} API endpoints, {} test files / {} tests".format(
        len(d["modules"]),
        len(d["cli_verbs"]),
        len(d["api_endpoints"]),
        d["test_summary"]["test_files"],
        d["test_summary"]["test_count"],
    ))
    lines.append("")

    lines.append("### Modules ({})".format(len(d["modules"])))
    for m in d["modules"]:
        desc = m["description"] or "(no docstring)"
        lines.append("  - {} [{} public]".format(m["module"], m["public_count"]))
        lines.append("      {}".format(desc))
    lines.append("")

    lines.append("### CLI verbs ({})".format(len(d["cli_verbs"])))
    for v in d["cli_verbs"]:
        lines.append("  - {:<24} {}".format(v["verb"], v["help"]))
    lines.append("")

    lines.append("### API endpoints ({})".format(len(d["api_endpoints"])))
    for e in d["api_endpoints"]:
        doc = e["doc"] or ""
        lines.append("  - {:<5} {:<36} → {}  {}".format(
            e["method"], e["path"], e["handler"], doc
        ))
    return "\n".join(lines)


# --- optional Claude narrative ---------------------------------------------

def narrate(data):
    """Ask Claude to weave a narrative explanation from the explain dict.

    Returns the narrative text. Raises if no Claude transport is available
    — the caller should print the deterministic render first and then add
    the narrative below (so failure here doesn't lose the structural data).
    """
    from . import claude_cli  # local import: only needed when --narrative

    prompt = _narrative_prompt(data)
    return claude_cli.summarize(prompt)


def _narrative_prompt(data):
    """Build the prompt sent to Claude for --narrative mode."""
    rendered = render(data)
    target = data.get("target", "?")
    target_type = data.get("target_type", "?")
    return (
        "You are an experienced software engineer helping the maintainer of "
        "the AI Landscape project understand a part of their own codebase. "
        "Below is a deterministic structural report on `{}` ({}). "
        "In 4-8 short paragraphs, explain:\n"
        "  1. What this part of the system does and why it exists.\n"
        "  2. How it fits into the larger system (what it depends on, what depends on it).\n"
        "  3. The critical paths through it (the most important code flows).\n"
        "  4. What the trust signals tell us about correctness and maintenance.\n"
        "  5. One concrete suggestion for what to verify or improve, if anything stands out.\n"
        "\nDo not invent details that aren't in the report. If the report shows "
        "zero tests or a missing docstring, name it explicitly. Be concrete.\n"
        "\n--- STRUCTURAL REPORT ---\n{}\n".format(target, target_type, rendered)
    )
