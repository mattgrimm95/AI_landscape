"""Daily snapshot store for LLM-written syntheses (hype + briefing narrative).

The synthesis modules in `synthesis.py` call the Anthropic API on every
invocation. That's the right shape for a script that runs once, but the
wrong shape for a web app served to anyone: every page view of "Today's
spotlight" would re-bill the operator's API key, and visitors without a
key would see nothing.

This module is the sidecar cache that fixes both. Once a day (typically
from the daily scrape job) `pipeline.generate_daily_syntheses` calls the
Anthropic API and writes the result here. The server's `/api/hype` and
`/api/briefing/narrative` endpoints then read from this cache so they're
free to call, deterministic across visitors, and work without a key.

## Storage layout

    snapshots/syntheses/YYYY-MM-DD.json

One file per calendar date (UTC). The directory is version-controlled
(unlike `data/`), so the canonical instance commits daily syntheses with
the daily corpus update and visitors who pull the repo get the history.

## Why a sidecar — and why NOT in the corpus

Putting LLM-written prose into `corpus/documents.jsonl` was deliberately
ruled out: it would feed back into NER and the typed-relation extractor,
slowly nudging the graph toward the model's phrasing instead of the
source articles'. Keeping syntheses out of the corpus is what makes this
safe — the corpus stays a strict record of human-written reporting, and
the syntheses live alongside it but are never read by the analysis
pipeline.

## File schema

    {
      "date": "YYYY-MM-DD",
      "generated_at": "<ISO-8601 UTC>",
      "corpus_mtime": "<ISO-8601 UTC or empty>",
      "corpus_documents": <int>,
      "hype": {
        "available": <bool>,
        "text": "...",
        "window_days": <int>,
        "documents_used": <int>,
        "error": "<str or empty>"
      },
      "briefing_narrative": {
        "available": <bool>,
        "text": "...",
        "window_days": <int>,
        "error": "<str or empty>"
      }
    }

A sub-section's `available=False` means generation failed (or was never
attempted) — the surrounding snapshot is still valid, just partial. This
lets a rate-limit on one synthesis not poison the other.
"""

import datetime
import json
import pathlib

from . import config

# The snapshots directory is committed to git (snapshots/ is NOT in
# .gitignore), so a daily-generated synthesis becomes part of the project
# history that visitors can pull. The per-date filename keeps the diffs
# tidy: one file = one day's snapshot.
SNAPSHOT_SUBDIR = config.SNAPSHOT_DIR / "syntheses"

# A snapshot is "stale" once it's been more than this many hours since it
# was generated. The threshold is generous enough that a once-a-day cron
# is comfortably inside the freshness window even on a missed run.
STALE_AFTER_HOURS = 36

# Subsections we expect inside every snapshot. Centralised so consumers
# can iterate them without hardcoding the keys.
SECTION_NAMES = ("hype", "briefing_narrative")


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def _utcnow_iso():
    return _utcnow().isoformat(timespec="seconds")


def snapshot_path(date=None):
    """Path the snapshot for `date` would live at (no I/O performed).

    `date` defaults to today's UTC date. Returns a pathlib.Path under
    `snapshots/syntheses/`.
    """
    if date is None:
        date = _utcnow().date()
    if isinstance(date, datetime.datetime):
        date = date.date()
    return SNAPSHOT_SUBDIR / ("%s.json" % date.isoformat())


def latest_snapshot_path():
    """Return the path of the most recent snapshot, or None if there isn't one.

    Snapshots are named by ISO date so lexical ordering equals chronological.
    """
    if not SNAPSHOT_SUBDIR.exists():
        return None
    candidates = sorted(SNAPSHOT_SUBDIR.glob("*.json"))
    return candidates[-1] if candidates else None


def load_snapshot(path):
    """Read and parse a snapshot file. Returns the dict or None if unreadable.

    `path` is a pathlib.Path. Tolerant of a missing file or malformed JSON
    so a corrupted snapshot can never crash the request path.
    """
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def latest_snapshot():
    """Load the most recent snapshot. Returns (snapshot, path) or (None, None)."""
    path = latest_snapshot_path()
    snap = load_snapshot(path)
    if snap is None:
        return None, None
    return snap, path


def save_snapshot(snapshot, date=None):
    """Atomically write a snapshot for `date` (defaults to today UTC).

    Writes to a `.tmp` sibling and renames into place, so a crash mid-write
    can never leave a half-written snapshot. Returns the path written to.
    """
    SNAPSHOT_SUBDIR.mkdir(parents=True, exist_ok=True)
    path = snapshot_path(date)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


def empty_section():
    """A blank section in the snapshot schema — for failed / never-tried sub-runs."""
    return {
        "available": False,
        "text": "",
        "window_days": 0,
        "documents_used": 0,
        "error": "",
    }


def make_snapshot(corpus_mtime=None, corpus_documents=0):
    """Initialise a snapshot dict for today with both sections blank.

    Callers fill the sections in-place via `set_section`, then pass the
    dict to `save_snapshot`.
    """
    now = _utcnow()
    return {
        "date": now.date().isoformat(),
        "generated_at": now.isoformat(timespec="seconds"),
        "corpus_mtime": corpus_mtime or "",
        "corpus_documents": int(corpus_documents or 0),
        "hype": empty_section(),
        "briefing_narrative": empty_section(),
    }


def set_section(snapshot, name, *, text="", available=True, error="",
                window_days=0, documents_used=0):
    """Write one section into an in-progress snapshot.

    Used by the daily-generation routine to record each sub-call's result.
    Calling this with available=False (and an error string) records a
    deliberate "we tried and it failed" rather than "never tried" — both
    show as available=False to the UI, but the error message lets the
    operator diagnose.
    """
    if name not in SECTION_NAMES:
        raise ValueError("unknown section %r (expected %s)"
                         % (name, SECTION_NAMES))
    snapshot[name] = {
        "available": bool(available),
        "text": text or "",
        "window_days": int(window_days or 0),
        "documents_used": int(documents_used or 0),
        "error": error or "",
    }


def age_seconds(snapshot, now=None):
    """How many seconds old this snapshot is, or None if it has no timestamp.

    `now` lets tests inject a clock. The timestamp on disk is always UTC
    ISO-8601, so a naive `fromisoformat` round-trips cleanly.
    """
    if not snapshot or not snapshot.get("generated_at"):
        return None
    try:
        gen = datetime.datetime.fromisoformat(snapshot["generated_at"])
    except ValueError:
        return None
    if gen.tzinfo is None:
        gen = gen.replace(tzinfo=datetime.timezone.utc)
    now = now or _utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    return (now - gen).total_seconds()


def is_stale(snapshot, max_age_hours=STALE_AFTER_HOURS, now=None):
    """True if the snapshot is older than `max_age_hours`.

    A missing timestamp or unparseable timestamp is treated as stale —
    safer for the UI to surface a "regenerate me" hint than to hide one.
    """
    age = age_seconds(snapshot, now=now)
    if age is None:
        return True
    return age > max_age_hours * 3600
