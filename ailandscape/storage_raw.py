"""Step 3 of the flow: the database log.

`raw_log.db` is an append-only record of every scraped document and every
raw entity extracted from it. It is the durable source of truth; the
knowledge graph is a derived view rebuilt from this log.
"""

import datetime
import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,
    url           TEXT,
    title         TEXT,
    published     TEXT,
    fetched_at    TEXT NOT NULL,
    content_hash  TEXT NOT NULL UNIQUE,
    raw_text      TEXT
);
CREATE TABLE IF NOT EXISTS entities (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id   INTEGER NOT NULL REFERENCES documents(id),
    text          TEXT NOT NULL,
    label         TEXT NOT NULL,
    start_char    INTEGER,
    end_char      INTEGER,
    extracted_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entities_doc ON entities(document_id);
"""


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class RawLogStore:
    """Append-only store for scraped documents and raw entities."""

    def __init__(self, path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def add_document(self, article, content_hash):
        """Insert a document if it is new. Returns (document_id, is_new)."""
        row = self.conn.execute(
            "SELECT id FROM documents WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        if row:
            return row["id"], False
        cur = self.conn.execute(
            "INSERT INTO documents "
            "(source, url, title, published, fetched_at, content_hash, raw_text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                article.get("source", ""),
                article.get("url", ""),
                article.get("title", ""),
                article.get("published", ""),
                _utcnow(),
                content_hash,
                article.get("raw_text", ""),
            ),
        )
        self.conn.commit()
        return cur.lastrowid, True

    def add_entities(self, document_id, entities):
        """Append raw entity records for a document."""
        now = _utcnow()
        self.conn.executemany(
            "INSERT INTO entities "
            "(document_id, text, label, start_char, end_char, extracted_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    document_id,
                    e["text"],
                    e["label"],
                    e.get("start"),
                    e.get("end"),
                    now,
                )
                for e in entities
            ],
        )
        self.conn.commit()

    def documents(self):
        return [
            dict(r)
            for r in self.conn.execute("SELECT * FROM documents ORDER BY id")
        ]

    def entities_for(self, document_id):
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM entities WHERE document_id = ? ORDER BY id",
                (document_id,),
            )
        ]

    def all_entities(self):
        return [
            dict(r)
            for r in self.conn.execute("SELECT * FROM entities ORDER BY id")
        ]

    def count_documents(self):
        return self.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

    def count_entities(self):
        return self.conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
