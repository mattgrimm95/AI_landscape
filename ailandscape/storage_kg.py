"""Step 5 of the flow: the knowledge graph database.

`knowledge_graph.db` holds canonical nodes, the aliases that resolve to them,
and weighted relationship edges. It is a derived view: `reconcile` rebuilds it
in full from the raw log, so it can always be regenerated from the source of
truth.
"""

import json
import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name  TEXT NOT NULL,
    type            TEXT NOT NULL,
    first_seen      TEXT,
    last_seen       TEXT,
    mention_count   INTEGER NOT NULL DEFAULT 0,
    document_count  INTEGER NOT NULL DEFAULT 0,
    metadata        TEXT,
    UNIQUE(canonical_name, type)
);
CREATE TABLE IF NOT EXISTS aliases (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id   INTEGER NOT NULL REFERENCES nodes(id),
    alias     TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS edges (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    src_id     INTEGER NOT NULL REFERENCES nodes(id),
    dst_id     INTEGER NOT NULL REFERENCES nodes(id),
    relation   TEXT NOT NULL,
    weight     INTEGER NOT NULL DEFAULT 1,
    metadata   TEXT,
    UNIQUE(src_id, dst_id, relation)
);
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src_id);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst_id);
CREATE TABLE IF NOT EXISTS node_documents (
    node_id       INTEGER NOT NULL REFERENCES nodes(id),
    content_hash  TEXT NOT NULL,
    UNIQUE(node_id, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_node_docs_node ON node_documents(node_id);
"""


class KnowledgeGraphStore:
    """Store for knowledge-graph nodes, aliases, and relationship edges."""

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

    def clear(self):
        """Remove all graph data. Used by `reconcile` to rebuild from the log.

        Autoincrement counters are reset so a rebuild from the same raw log
        produces the same node and edge ids every time.
        """
        self.conn.execute("DELETE FROM edges")
        self.conn.execute("DELETE FROM node_documents")
        self.conn.execute("DELETE FROM aliases")
        self.conn.execute("DELETE FROM nodes")
        try:
            self.conn.execute(
                "DELETE FROM sqlite_sequence "
                "WHERE name IN ('nodes', 'aliases', 'edges')"
            )
        except sqlite3.OperationalError:
            pass  # sqlite_sequence does not exist until the first insert
        self.conn.commit()

    def insert_node(
        self,
        canonical_name,
        node_type,
        first_seen="",
        last_seen="",
        mention_count=0,
        document_count=0,
        metadata=None,
    ):
        cur = self.conn.execute(
            "INSERT INTO nodes "
            "(canonical_name, type, first_seen, last_seen, mention_count, "
            "document_count, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                canonical_name,
                node_type,
                first_seen,
                last_seen,
                mention_count,
                document_count,
                json.dumps(metadata or {}),
            ),
        )
        return cur.lastrowid

    def insert_alias(self, node_id, alias):
        self.conn.execute(
            "INSERT OR IGNORE INTO aliases (node_id, alias) VALUES (?, ?)",
            (node_id, alias),
        )

    def insert_edge(self, src_id, dst_id, relation, weight=1, metadata=None):
        self.conn.execute(
            "INSERT INTO edges (src_id, dst_id, relation, weight, metadata) "
            "VALUES (?, ?, ?, ?, ?)",
            (src_id, dst_id, relation, weight, json.dumps(metadata or {})),
        )

    def insert_node_documents(self, node_id, content_hashes):
        """Record which corpus documents a node was mentioned in."""
        self.conn.executemany(
            "INSERT OR IGNORE INTO node_documents (node_id, content_hash) "
            "VALUES (?, ?)",
            [(node_id, h) for h in content_hashes],
        )

    def documents_for_node(self, node_id):
        """Return the content hashes of the documents a node appears in."""
        return [
            r["content_hash"]
            for r in self.conn.execute(
                "SELECT content_hash FROM node_documents WHERE node_id = ? "
                "ORDER BY content_hash",
                (node_id,),
            )
        ]

    def commit(self):
        self.conn.commit()

    def node_by_alias(self, alias):
        row = self.conn.execute(
            "SELECT n.* FROM nodes n JOIN aliases a ON a.node_id = n.id "
            "WHERE a.alias = ?",
            (alias,),
        ).fetchone()
        return dict(row) if row else None

    def nodes(self):
        return [
            dict(r)
            for r in self.conn.execute("SELECT * FROM nodes ORDER BY id")
        ]

    def aliases(self):
        return [
            dict(r)
            for r in self.conn.execute("SELECT * FROM aliases ORDER BY id")
        ]

    def edges(self):
        return [
            dict(r)
            for r in self.conn.execute("SELECT * FROM edges ORDER BY id")
        ]

    def top_nodes(self, limit=10):
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM nodes ORDER BY mention_count DESC, id LIMIT ?",
                (limit,),
            )
        ]

    def count_nodes(self):
        return self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]

    def count_edges(self):
        return self.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
