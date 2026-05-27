"""Claude Code CLI transport for headless synthesis.

The Anthropic Messages API (in `synthesis.py`) needs a separately-billed
`ANTHROPIC_API_KEY`. An operator on a Claude Code Max subscription
already has authenticated, paid access through the `claude` CLI binary,
so this module shells out to it instead — same model, no extra billing,
no key to manage.

The CLI is invoked with `--print` (one-shot, no REPL) and the prompt as
a positional argument. The response text is captured from stdout. The
input/output shape mirrors `synthesis._call_anthropic` so callers can
swap transports without code change.

## Discovery

`find_cli()` looks for `claude` in this order:
  1. `$PATH` (the normal place for a CLI install)
  2. `%APPDATA%\\Claude\\claude-code\\<version>\\claude.exe` — the Windows
     installer's default location. The highest version directory wins,
     so a side-by-side install upgrade is invisible to callers.

Returns the absolute path to the executable, or `None` if not found. A
caller asks `is_available()` to gate any attempt to use the transport.

## Why a separate module (not inside synthesis.py)?

So the test suite can mock the CLI cleanly without touching the API
path, and so a future second transport (e.g. Vertex / Bedrock) can slot
in beside this one without growing synthesis.py further.
"""

import os
import pathlib
import shutil
import subprocess


# Default model is whatever the user's Claude Code defaults to (the CLI
# picks). Callers can override per-call with the `model` argument.
DEFAULT_MODEL = None  # let `claude` decide
DEFAULT_TIMEOUT = 180  # seconds — large prompts can take a while

class ClaudeCliError(Exception):
    """Raised when the CLI is missing, errors out, or returns no text."""


def _candidate_install_roots():
    """Yield possible Claude Code install directory roots on Windows.

    Two install shapes seen in the wild:

    1. Regular Win32 install at ``%APPDATA%\\Claude\\claude-code\\<version>\\claude.exe``.
       Also the path visible to *packaged* callers running inside Claude
       Code Desktop's MSIX container, as a reflection of (2).

    2. MSIX / Microsoft Store install at
       ``%LOCALAPPDATA%\\Packages\\Claude_<hash>\\LocalCache\\Roaming\\Claude\\claude-code\\<version>\\claude.exe``.
       This is the canonical on-disk location for the Store-distributed
       Claude Code Desktop; the Roaming reflection in (1) is virtualized
       and may not be visible to non-packaged processes (e.g. an
       interactive PowerShell window or a Task Scheduler job).

    Yielding both means ``find_cli()`` works regardless of which install
    shape the user has and which process context is calling us.
    """
    appdata = os.environ.get("APPDATA")
    if appdata:
        yield pathlib.Path(appdata) / "Claude" / "claude-code"
    local = os.environ.get("LOCALAPPDATA")
    if local:
        packages = pathlib.Path(local) / "Packages"
        if packages.exists():
            for pkg in sorted(packages.glob("Claude_*")):
                root = pkg / "LocalCache" / "Roaming" / "Claude" / "claude-code"
                if root.exists():
                    yield root


def find_cli():
    """Return the absolute path to the `claude` executable, or None.

    Checks $PATH first (which is what any well-installed CLI should be
    on), then iterates the Windows install candidates from
    `_candidate_install_roots()`. Picks the highest version directory
    within each root.
    """
    on_path = shutil.which("claude")
    if on_path:
        return on_path
    for root in _candidate_install_roots():
        # Pick the highest version dir. Tuple-of-ints sort handles
        # 2.10.x > 2.9.x correctly; the lexical fallback (0 for
        # non-numeric parts) keeps oddly-named dirs from crashing the
        # sort.
        try:
            versions = sorted(
                (p for p in root.iterdir() if p.is_dir()),
                key=lambda p: tuple(
                    int(x) if x.isdigit() else 0
                    for x in p.name.split(".")
                ),
                reverse=True,
            )
        except OSError:
            continue
        for version_dir in versions:
            candidate = version_dir / "claude.exe"
            if candidate.exists():
                return str(candidate)
    return None


def is_available():
    """True if the Claude Code CLI can be invoked from this process."""
    return find_cli() is not None


def summarize(prompt, model=None, timeout=DEFAULT_TIMEOUT):
    """Send `prompt` to the Claude Code CLI in one-shot mode, return text.

    Equivalent to `synthesis._call_anthropic` but billed to the user's
    Claude Code subscription instead of an Anthropic API key. Raises
    `ClaudeCliError` if the CLI isn't installed, the subprocess fails,
    or stdout is empty.

    The prompt is passed as a positional argument (the CLI's documented
    interface). On Windows the CreateProcessW 32K-character limit caps
    prompt size; the synthesis prompts in this project are a few KB at
    most and fit comfortably.
    """
    cli = find_cli()
    if cli is None:
        raise ClaudeCliError(
            "claude CLI not found on PATH or under %APPDATA%\\Claude\\claude-code; "
            "is Claude Code installed?"
        )
    cmd = [cli, "--print", "--output-format", "text"]
    if model is not None:
        cmd += ["--model", model]
    # The prompt goes last (CLI positional). Using a list arg avoids shell
    # quoting issues with the model's quotes / newlines.
    cmd.append(prompt)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            # Explicitly close stdin. The CLI emits a 3-second "no stdin
            # data received" warning otherwise — annoying every-call
            # latency, especially in the cron path.
            stdin=subprocess.DEVNULL,
            # No cwd override — the CLI's project-context auto-discovery
            # (CLAUDE.md, plugins) is fine; the prompts in this project
            # are self-contained and don't depend on additional context.
        )
    except subprocess.TimeoutExpired as exc:
        raise ClaudeCliError(
            "claude CLI timed out after %d seconds" % timeout
        ) from exc
    except OSError as exc:
        raise ClaudeCliError(
            "claude CLI launch failed: %s" % exc
        ) from exc
    if result.returncode != 0:
        # The CLI sometimes writes diagnostics to stdout instead of stderr
        # (e.g. "Not logged in · Please run /login" lands on stdout), so
        # consider both streams when explaining the failure.
        err = (result.stderr or "").strip()[:400]
        out = (result.stdout or "").strip()[:400]
        diagnostic = err or out or "(no output)"
        # Surface the most common cause — an unauthenticated CLI — with a
        # tailored hint so the operator doesn't have to guess.
        if "logged in" in diagnostic.lower() or "/login" in diagnostic:
            raise ClaudeCliError(
                "claude CLI is not logged in. Run `claude` interactively "
                "once and execute `/login` to authenticate this binary. "
                "Underlying message: %s" % diagnostic
            )
        raise ClaudeCliError(
            "claude CLI exited with code %d: %s"
            % (result.returncode, diagnostic)
        )
    text = (result.stdout or "").strip()
    if not text:
        raise ClaudeCliError("claude CLI returned empty output")
    return text
