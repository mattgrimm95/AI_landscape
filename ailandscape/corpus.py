"""The corpus: the version-controlled source of truth.

`corpus/documents.jsonl` is an append-only file with one JSON object per
line, each a scraped document. It is committed to git. Both SQLite databases
are derived caches that `pipeline.rebuild` regenerates deterministically from
this file, so the whole pipeline is reproducible from version-controlled text.

A document record has a fixed set of keys; `fetched_at` and `content_hash`
are captured once at scrape time and never change, which is what makes a
rebuild reproducible.
"""

import datetime
import email.utils
import json
import pathlib
import re

DOCUMENT_FIELDS = (
    "source",
    "url",
    "title",
    "published",
    "fetched_at",
    "content_hash",
    "raw_text",
    "metadata",
    # Claude reading tracker. `claude_read_count` is how many times Claude
    # has read this article end-to-end during a corpus survey. `claude_read_fresh`
    # is True iff Claude has read it since the article was added (resetting it
    # — via `corpus invalidate_freshness` / `python -m ailandscape.cli reading
    # --reset` — signals a major corpus update that warrants re-reading).
    # `claude_last_read` is an ISO timestamp of the most recent read, or "".
    "claude_read_count",
    "claude_read_fresh",
    "claude_last_read",
)

# `metadata` is a JSON object (source-specific structured data, e.g. an SBIR
# award amount); every other field is a string. The Claude reading fields are
# typed: an int counter, a bool freshness flag, an ISO-8601 timestamp string.
_FIELD_DEFAULTS = {
    "metadata": {},
    "claude_read_count": 0,
    "claude_read_fresh": False,
    "claude_last_read": "",
}


def _project(document):
    """Project a document onto the fixed field set, with typed defaults."""
    return {
        field: document.get(field, _FIELD_DEFAULTS.get(field, ""))
        for field in DOCUMENT_FIELDS
    }


def load(path):
    """Return every document record in the corpus, in append order."""
    p = pathlib.Path(path)
    if not p.exists():
        return []
    documents = []
    with p.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                record = json.loads(line)
                # Corpus lines written before a field was introduced
                # (`metadata`, the `claude_read_*` tracker) still load with
                # the typed default, so consumers can rely on every key in
                # `_FIELD_DEFAULTS` being present.
                for field, default in _FIELD_DEFAULTS.items():
                    if field not in record:
                        # Copy mutable defaults (the `metadata` dict) so each
                        # record gets its own; the int/bool/str defaults are
                        # immutable and can be shared.
                        record[field] = (
                            default.copy() if isinstance(default, dict) else default
                        )
                documents.append(record)
    return documents


def hashes(path):
    """Return the set of content hashes already present in the corpus."""
    return {doc["content_hash"] for doc in load(path)}


def append(path, document):
    """Append one document record as a JSON line."""
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    record = _project(document)
    with p.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def save(path, documents):
    """Overwrite the corpus with `documents`, atomically.

    Used by maintenance repairs (e.g. backfilling article text). Records are
    projected to the fixed field set exactly as `append` writes them, so a
    rewritten corpus is byte-compatible with an appended one. The write goes
    to a temp file that is then renamed, so a crash mid-write cannot leave a
    truncated corpus.
    """
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for document in documents:
            record = _project(document)
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    tmp.replace(p)


def count(path):
    return len(load(path))


def mark_read(path, content_hashes, when_iso):
    """Mark the documents identified by `content_hashes` as Claude-read.

    Increments `claude_read_count`, sets `claude_read_fresh=True`, and stamps
    `claude_last_read` to `when_iso` for each matched document. Returns the
    number of documents updated. Other documents are written back unchanged
    so the corpus stays byte-stable for unaffected lines.
    """
    targets = set(content_hashes)
    if not targets:
        return 0
    documents = load(path)
    updated = 0
    for doc in documents:
        if doc.get("content_hash") in targets:
            doc["claude_read_count"] = int(doc.get("claude_read_count", 0) or 0) + 1
            doc["claude_read_fresh"] = True
            doc["claude_last_read"] = when_iso
            updated += 1
    if updated:
        save(path, documents)
    return updated


def invalidate_freshness(path):
    """Flip `claude_read_fresh` to False on every document.

    Use after a major corpus update (new feed sources, schema migration, large
    gazetteer overhaul) when Claude's prior reads no longer reflect the
    current corpus context and a fresh survey is warranted. The read counter
    and last-read timestamp are preserved.
    """
    documents = load(path)
    changed = 0
    for doc in documents:
        if doc.get("claude_read_fresh"):
            doc["claude_read_fresh"] = False
            changed += 1
    if changed:
        save(path, documents)
    return changed


def reading_stats(path):
    """Return a small dict summarizing Claude read coverage of the corpus."""
    documents = load(path)
    total = len(documents)
    ever_read = sum(
        1 for d in documents if int(d.get("claude_read_count", 0) or 0) > 0
    )
    fresh = sum(1 for d in documents if d.get("claude_read_fresh"))
    counts = [int(d.get("claude_read_count", 0) or 0) for d in documents]
    return {
        "documents": total,
        "ever_read": ever_read,
        "fresh": fresh,
        "never_read": total - ever_read,
        "stale": ever_read - fresh,
        "total_reads": sum(counts),
        "max_reads_one_doc": max(counts) if counts else 0,
    }


def document_text(doc):
    """The text NER and relation extraction operate on: title + body.

    Defined once so the entity offsets recorded by NER stay valid for the
    relation extractor, which works on the same string.
    """
    return (doc.get("title", "") + ". " + doc.get("raw_text", "")).strip()


def published_date(doc):
    """Best-effort parse of a document's published date to 'YYYY-MM-DD'.

    Feeds report dates in several formats — RFC-822 for most RSS, ISO-8601,
    a bare date, or just a year for SBIR awards — so whatever is present is
    normalized. This lets node first/last-seen reflect when news happened,
    not when it was scraped. Returns '' if no date can be recovered.
    """
    text = (doc.get("published") or "").strip()
    if not text:
        return ""
    try:
        parsed = email.utils.parsedate_to_datetime(text)
        if parsed is not None:
            return parsed.date().isoformat()
    except (TypeError, ValueError):
        pass
    try:
        iso = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        return iso.date().isoformat()
    except ValueError:
        pass
    match = re.match(r"\d{4}-\d{2}-\d{2}", text)
    if match:
        return match.group(0)
    match = re.match(r"(\d{4})$", text)
    if match:
        return match.group(1) + "-01-01"
    return ""
