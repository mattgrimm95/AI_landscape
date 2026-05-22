"""Optional LLM narrative synthesis.

Turns a generated briefing into a short analyst-style narrative by calling
the Anthropic Messages API. Strictly opt-in: it does nothing unless an
ANTHROPIC_API_KEY is present in the environment, so the deterministic core
of the pipeline never depends on it and the project runs fully without it.

The API key is read from the environment only — it is never stored, logged,
written to disk, or returned to a caller.
"""

import json
import os
import urllib.error
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"
# Overridable via the ANTHROPIC_MODEL environment variable.
_DEFAULT_MODEL = "claude-sonnet-4-6"


class SynthesisError(Exception):
    """Raised when narrative synthesis is unavailable or the API call fails."""


def is_configured():
    """True if an Anthropic API key is present in the environment."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


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

    Raises SynthesisError if no API key is configured or the call fails.
    """
    return _call_anthropic(
        _briefing_prompt(briefing_data), max_tokens=max_tokens
    )
