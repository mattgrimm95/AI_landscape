"""Daily "hype read" generation and persistence.

A small wrapper around `synthesis.summarize_hype` that knows how to pick
the most-recent-day's documents from the corpus, call Claude, and write
the result to a JSON file with a generated_at timestamp. Shared by the
CLI `hype` command (driven nightly by the daily-scrape scheduler) and
the `/api/hype` server endpoint (which now reads the cached file
instead of always paying for a live generation).

Persisted at `config.DAILY_HYPE_FILE`. Version-controlled under
`corpus/` so the artifact and its timestamp survive container rebuilds
and so every browser tab sees the same hype piece.
"""

import datetime
import json

from . import corpus, synthesis


def _recent_documents(documents, days):
    """Return docs published / fetched within `days` days, newest first.

    A short fallback to 3 days kicks in when the requested window is empty
    so a quiet 24-hour news cycle still produces output. Date parsing is
    forgiving (published-date preferred, fetched_at as fallback) so
    backfilled or SBIR docs with non-standard date shapes still count.
    """
    today = datetime.date.today()
    cutoff_main = today - datetime.timedelta(days=days)
    cutoff_fallback = today - datetime.timedelta(days=max(days, 3))

    def doc_date(doc):
        date_str = corpus.published_date(doc) or (doc.get("fetched_at") or "")[:10]
        if not date_str:
            return None
        try:
            return datetime.date.fromisoformat(date_str[:10])
        except ValueError:
            return None

    in_main = []
    in_fallback = []
    for doc in documents:
        d = doc_date(doc)
        if d is None:
            continue
        if d >= cutoff_main:
            in_main.append((d, doc))
        if d >= cutoff_fallback:
            in_fallback.append((d, doc))
    picks = in_main or in_fallback
    picks.sort(key=lambda pair: pair[0], reverse=True)
    return [doc for _date, doc in picks]


def _sbir_funding(documents):
    """Aggregate SBIR/STTR funding so the hype prompt can lean on real $$."""
    awards = [
        d for d in documents
        if (d.get("metadata") or {}).get("data_source") == "SBIR"
    ]
    return {
        "awards": len(awards),
        "total_amount": int(sum(
            (d.get("metadata") or {}).get("award_amount") or 0 for d in awards
        )),
    }


def generate(documents, days=1, now=None):
    """Build a hype piece from the last `days` days of corpus documents.

    Returns the artifact dict (without saving it) so callers can decide
    whether to persist, surface, or both. `now` is injectable for tests.
    Raises `synthesis.SynthesisError` if the Claude call fails — callers
    are expected to catch and degrade gracefully (the CLI logs and exits
    0 so a hype failure never breaks the daily scrape).
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    recent = _recent_documents(documents, days=days)
    funding = _sbir_funding(documents)
    text = synthesis.summarize_hype(recent, sbir_funding=funding)
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "window_days": days,
        "documents_used": len(recent),
        "hype": text,
    }


def save(artifact, path):
    """Write the artifact dict to `path` as pretty JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(artifact, indent=2) + "\n", encoding="utf-8"
    )


def load(path):
    """Return the persisted artifact dict, or None if the file is missing
    or malformed. Never raises — a corrupt cache should degrade to "no
    hype yet" in the UI, not crash the server."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def generate_and_save(documents, path, days=1, now=None):
    """Convenience wrapper: build and persist in one call."""
    artifact = generate(documents, days=days, now=now)
    save(artifact, path)
    return artifact
