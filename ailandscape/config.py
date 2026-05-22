"""Filesystem paths and shared constants."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SNAPSHOT_DIR = ROOT / "snapshots"
CORPUS_DIR = ROOT / "corpus"

# The version-controlled source of truth: an append-only JSONL of scraped
# documents. Both SQLite databases are derived caches rebuilt from this file.
CORPUS_FILE = CORPUS_DIR / "documents.jsonl"

RAW_LOG_DB = DATA_DIR / "raw_log.db"
KG_DB = DATA_DIR / "knowledge_graph.db"

HTTP_USER_AGENT = (
    "AILandscapeBot/0.1 (research project; "
    "+https://github.com/mattgrimm95/AI_landscape)"
)
HTTP_TIMEOUT = 20


def ensure_dirs():
    """Create the data, snapshot, and corpus directories if they are missing."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
