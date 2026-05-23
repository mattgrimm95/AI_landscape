"""Optional LLM narrative synthesis.

Turns a generated briefing into a short analyst-style narrative. Two
transports are supported, picked automatically in this order:

  1. **Claude Code CLI** (preferred when available) — shells out to the
     `claude` binary, billed to the user's Claude Code subscription. No
     API key needed; this is the right path for a Claude Code Max user.
  2. **Anthropic Messages API** — direct HTTP call using ANTHROPIC_API_KEY
     from the environment. The legacy path; kept so users without Claude
     Code installed can still opt in by setting a standalone API key.

Strictly opt-in: it does nothing unless one of the two transports is
available, so the deterministic core of the pipeline never depends on it
and the project runs fully without either.

The API key is read from the environment only — it is never stored,
logged, written to disk, or returned to a caller.
"""

import json
import os
import urllib.error
import urllib.request

from . import claude_cli

API_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"
# Overridable via the ANTHROPIC_MODEL environment variable.
_DEFAULT_MODEL = "claude-sonnet-4-6"

TRANSPORT_CLI = "claude-code-cli"
TRANSPORT_API = "anthropic-api"


class SynthesisError(Exception):
    """Raised when narrative synthesis is unavailable or the call fails."""


def transport():
    """Return the name of the preferred synthesis transport, or None.

    Picks the Claude Code CLI first (subscription-billed, no key needed),
    falls back to the Anthropic API only when the CLI is unavailable.
    Returns one of ``TRANSPORT_CLI``, ``TRANSPORT_API``, or ``None``.
    """
    if claude_cli.is_available():
        return TRANSPORT_CLI
    if os.environ.get("ANTHROPIC_API_KEY"):
        return TRANSPORT_API
    return None


def is_configured():
    """True if some synthesis transport is available on this machine."""
    return transport() is not None


def _call(prompt, max_tokens=700, timeout=60):
    """Route `prompt` through whichever synthesis transport is available.

    Tries the Claude Code CLI first (uses the operator's subscription, no
    billing on a separate API key), then the Anthropic API. Raises
    SynthesisError if neither transport is configured, or if the chosen
    transport errors out.

    `max_tokens` is honored by the API path; the CLI path doesn't take
    one (Claude Code controls response length itself), but the argument
    is accepted for signature compatibility.
    """
    t = transport()
    if t == TRANSPORT_CLI:
        try:
            return claude_cli.summarize(prompt, timeout=max(timeout, 180))
        except claude_cli.ClaudeCliError as exc:
            raise SynthesisError(str(exc)) from exc
    if t == TRANSPORT_API:
        return _call_anthropic(prompt, max_tokens=max_tokens, timeout=timeout)
    raise SynthesisError(
        "no synthesis transport available: install Claude Code (preferred) "
        "or set ANTHROPIC_API_KEY"
    )


def _call_anthropic(prompt, max_tokens=700, timeout=60):
    """Send one prompt to the Anthropic Messages API and return the reply text.

    Raises SynthesisError if no key is configured or the request fails.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SynthesisError(
            "narrative synthesis is opt-in: set ANTHROPIC_API_KEY to enable it"
        )
    payload = json.dumps(
        {
            "model": os.environ.get("ANTHROPIC_MODEL", _DEFAULT_MODEL),
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "x-api-key": key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raise SynthesisError(
            "Anthropic API returned HTTP %s" % exc.code
        ) from exc
    except (urllib.error.URLError, ValueError, OSError) as exc:
        raise SynthesisError("Anthropic API request failed: %s" % exc) from exc
    parts = body.get("content") or []
    text = "".join(
        p.get("text", "") for p in parts if p.get("type") == "text"
    )
    if not text:
        raise SynthesisError("Anthropic API returned no usable text")
    return text.strip()


def _briefing_prompt(briefing_data):
    """Build the synthesis prompt from a briefing dict."""
    totals = briefing_data["totals"]
    lines = [
        "You are a defense-technology analyst. From the structured data",
        "below, write a concise intelligence-brief-style summary (at most",
        "180 words) of the AI national-security landscape. Lead with what",
        "matters most. Use only the data given — do not invent facts.",
        "",
        "Totals: %d documents, %d entities, %d typed relationships."
        % (totals["documents"], totals["entities"],
           totals["typed_relations"]),
        "Trending AI topics: "
        + ", ".join(c["name"] for c in briefing_data["trending_topics"]),
        "Most active entities: "
        + ", ".join(n["name"] for n in briefing_data["top_entities"]),
    ]
    if briefing_data["contract_awards"]:
        lines.append("Contract awards and deals:")
        for edge in briefing_data["contract_awards"]:
            lines.append(
                "  - %s %s %s"
                % (edge["subject"], edge["relation"].replace("_", " "),
                   edge["object"])
            )
    if briefing_data["key_relationships"]:
        lines.append("Other key relationships:")
        for edge in briefing_data["key_relationships"]:
            lines.append(
                "  - %s %s %s"
                % (edge["subject"], edge["relation"].replace("_", " "),
                   edge["object"])
            )
    return "\n".join(lines)


def summarize_briefing(briefing_data, max_tokens=700):
    """Return an LLM-written narrative summary of a briefing dict.

    Routes through the preferred transport (Claude Code CLI when
    available, Anthropic API otherwise). Raises SynthesisError if no
    transport is configured or the call fails.
    """
    return _call(
        _briefing_prompt(briefing_data), max_tokens=max_tokens
    )


def _hype_prompt(documents, sbir_funding):
    """Build a "hype" prompt from the most recent day's documents.

    The prompt asks for an energetic, 30-second-read style summary — the
    opposite tone from the analyst-briefing prompt. Same data-only
    discipline: the model is told to use only the facts it's handed, so a
    quiet news day still produces a sober (but lively) summary instead of
    invented excitement.
    """
    lines = [
        "You are an enthusiastic AI/defense-tech journalist.",
        "Write a SHORT, exciting, 30-second read (120-160 words max)",
        "that hypes up the reader about AI right now — concrete momentum,",
        "specific breakthroughs, named players, real deals.",
        "Lead with the single most exciting thing. Be vivid and direct.",
        "Use ONLY the facts in the headlines below — do not invent.",
        "If the day is quiet, say so plainly and pick the most notable item.",
        "No bullet points. No section headers. One flowing piece of prose.",
        "",
        "Today's headlines (most recent first):",
    ]
    if not documents:
        lines.append("  (no documents in the recent window)")
    for doc in documents[:18]:
        title = (doc.get("title") or "").strip()
        source = (doc.get("source") or "").strip()
        if not title:
            continue
        snippet = (doc.get("raw_text") or "").strip().replace("\n", " ")
        snippet = snippet[:220]
        lines.append("  - %s [%s]" % (title, source))
        if snippet:
            lines.append("      %s" % snippet)
    if sbir_funding and sbir_funding.get("awards"):
        lines.append("")
        lines.append(
            "Tracked SBIR/STTR funding total: $%s across %d AI-related awards."
            % (
                format(int(sbir_funding.get("total_amount", 0)), ",d"),
                sbir_funding["awards"],
            )
        )
    return "\n".join(lines)


def summarize_hype(documents, sbir_funding=None, max_tokens=400):
    """Return an exciting, 30-second-read hype summary of recent AI news.

    `documents` should be the most recent day's (or few days') docs from
    the corpus — caller-selected so this stays single-purpose. Routes
    through the preferred transport (Claude Code CLI when available,
    Anthropic API otherwise). Raises SynthesisError if no transport is
    configured or the call fails. Callers handle the graceful no-op via
    `is_configured()`.
    """
    return _call(
        _hype_prompt(documents, sbir_funding or {}),
        max_tokens=max_tokens,
    )
