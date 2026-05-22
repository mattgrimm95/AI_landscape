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
)

# `metadata` is a JSON object (source-specific structured data, e.g. an SBIR
# award amount); every other field is a string.
_FIELD_DEFAULTS = {"metadata": {}}


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
                # Corpus lines written before `metadata` existed still load
                # with the default, so consumers can rely on the key.
                record.setdefault("metadata", {})
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
