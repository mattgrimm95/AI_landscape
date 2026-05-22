"""Filesystem paths and shared constants."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SNAPSHOT_DIR = ROOT / "snapshots"
CORPUS_DIR = ROOT / "corpus"

# The version-controlled source of truth: an append-only JSONL of scraped
# documents. Both SQLite databases are derived caches rebuilt from this file.
CORPUS_FILE = CORPUS_DIR / "documents.jsonl"

NER_OUTPUT_DB = DATA_DIR / "ner_output_log.db"
KG_DB = DATA_DIR / "knowledge_graph.db"

HTTP_USER_AGENT = (
    "AILandscapeBot/0.1 (research project; "
    "+https://github.com/mattgrimm95/AI_landscape)"
)
HTTP_TIMEOUT = 20

# Default NER backend: "rule" (gazetteer + proper-noun extractor — fast, no
# heavy dependencies, canonicalizes known defense entities) or "spacy"
# (statistical model — types every entity, higher recall, but slower and
# without gazetteer canonicalization). An explicit choice, not inferred from
# whichever package happens to be installed.
DEFAULT_NER_BACKEND = "rule"


def ensure_dirs():
    """Create the data, snapshot, and corpus directories if they are missing."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
