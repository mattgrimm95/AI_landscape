"""The corpus: the version-controlled source of truth.

`corpus/documents.jsonl` is an append-only file with one JSON object per
line, each a scraped document. It is committed to git. Both SQLite databases
are derived caches that `pipeline.rebuild` regenerates deterministically from
this file, so the whole pipeline is reproducible from version-controlled text.

A document record has a fixed set of keys; `fetched_at` and `content_hash`
are captured once at scrape time and never change, which is what makes a
rebuild reproducible.
"""

import json
import pathlib

DOCUMENT_FIELDS = (
    "source",
    "url",
    "title",
    "published",
    "fetched_at",
    "content_hash",
    "raw_text",
)


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
                documents.append(json.loads(line))
    return documents


def hashes(path):
    """Return the set of content hashes already present in the corpus."""
    return {doc["content_hash"] for doc in load(path)}


def append(path, document):
    """Append one document record as a JSON line."""
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    record = {field: document.get(field, "") for field in DOCUMENT_FIELDS}
    with p.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def count(path):
    return len(load(path))


def document_text(doc):
    """The text NER and relation extraction operate on: title + body.

    Defined once so the entity offsets recorded by NER stay valid for the
    relation extractor, which works on the same string.
    """
    return (doc.get("title", "") + ". " + doc.get("raw_text", "")).strip()
