"""Step 3 of the flow: the NER output log.

`ner_output_log.db` is a derived cache of raw named-entity-recognition
output — one row per extracted entity mention, keyed by the corpus
document's `content_hash`. It holds no document text or metadata of its own;
that lives in the corpus (the source of truth). The log is regenerated from
the corpus on every rebuild, so it is always reproducible.
"""

import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash  TEXT NOT NULL,
    text          TEXT NOT NULL,
    label         TEXT NOT NULL,
    start_char    INTEGER,
    end_char      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_entities_doc ON entities(content_hash);
"""


class NEROutputLog:
    """Derived store for raw NER output, keyed to corpus documents."""

    def __init__(self, path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def clear(self):
        """Remove all rows so the log can be rebuilt from the corpus.

        The autoincrement counter is reset so entity ids are reproducible
        across rebuilds.
        """
        self.conn.execute("DELETE FROM entities")
        try:
            self.conn.execute(
                "DELETE FROM sqlite_sequence WHERE name = 'entities'"
            )
        except sqlite3.OperationalError:
            pass  # sqlite_sequence does not exist until the first insert
        self.conn.commit()

    def add_entities(self, content_hash, entities):
        """Append raw entity records for one corpus document."""
        self.conn.executemany(
            "INSERT INTO entities "
            "(content_hash, text, label, start_char, end_char) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (
                    content_hash,
                    e["text"],
                    e["label"],
                    e.get("start"),
                    e.get("end"),
                )
                for e in entities
            ],
        )
        self.conn.commit()

    def entities_for(self, content_hash):
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM entities WHERE content_hash = ? ORDER BY id",
                (content_hash,),
            )
        ]

    def all_entities(self):
        return [
            dict(r)
            for r in self.conn.execute("SELECT * FROM entities ORDER BY id")
        ]

    def count_entities(self):
        return self.conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
