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

# Append-only log of pipeline runs (timing + counts), used by the overview
# report. Derived data — lives under the gitignored data/ directory.
RUN_HISTORY_FILE = DATA_DIR / "run_history.jsonl"

# Default output path for the interactive graph visualization (derived).
GRAPH_HTML = DATA_DIR / "knowledge_graph.html"

HTTP_USER_AGENT = (
    "AILandscapeBot/0.1 (research project; "
    "+https://github.com/mattgrimm95/AI_landscape)"
)
HTTP_TIMEOUT = 20

# Default NER backend. An explicit choice, not inferred from whichever
# package happens to be installed:
#   "hybrid" - gazetteer (precise, canonical) + spaCy (typed long tail)
#   "rule"   - gazetteer + proper-noun extractor (fast, no heavy deps)
#   "spacy"  - spaCy statistical model only
# "hybrid" degrades to "rule" automatically if spaCy is not installed.
DEFAULT_NER_BACKEND = "hybrid"


def ensure_dirs():
    """Create the data, snapshot, and corpus directories if they are missing."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
