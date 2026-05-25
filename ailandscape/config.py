"""Filesystem paths and shared constants."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SNAPSHOT_DIR = ROOT / "snapshots"
CORPUS_DIR = ROOT / "corpus"

# The version-controlled source of truth: an append-only JSONL of scraped
# documents. Both SQLite databases are derived caches rebuilt from this file.
CORPUS_FILE = CORPUS_DIR / "documents.jsonl"

# Sidecar archive for documents pruned out of the active corpus (e.g. by
# `audit-corpus-ai --prune` when they fail the AI relevance gate). Same
# JSONL format as CORPUS_FILE with two extra fields per record:
# `archived_at` (ISO timestamp) and `archived_reason` (short string).
# NOT read by NER / reconcile / any analysis path -- it's a preservation
# layer so a future filter tweak can re-ingest historical docs via
# `audit-corpus-ai --reinstate` without re-scraping.
CORPUS_ARCHIVE_FILE = CORPUS_DIR / "archived.jsonl"

NER_OUTPUT_DB = DATA_DIR / "ner_output_log.db"
KG_DB = DATA_DIR / "knowledge_graph.db"

# Manual corrections (merge/ignore). Version-controlled and consumed by
# reconcile, so corrections survive and reconstruction stays deterministic.
CORRECTIONS_FILE = ROOT / "corrections.json"

# Accumulating store of automated quality-review findings (merge candidates,
# data-quality history). Version-controlled and never overwritten by the
# `review` routine — each run merges new findings in.
REVIEW_FILE = ROOT / "review.json"

# Private recipient list for the daily email digest — lives under the
# gitignored data/ directory so addresses are never pushed.
EMAIL_RECIPIENTS_FILE = DATA_DIR / "email_recipients.txt"

# Append-only log of pipeline runs (timing, counts, per-feed scorecards,
# errors, quality KPIs — one JSON line per run). Lives under snapshots/
# (tracked, not gitignored) so the daily commit carries the run record
# and the operator can audit ingestion health over time from any clone.
# A legacy data/run_history.jsonl is auto-migrated on first read.
RUN_HISTORY_FILE = SNAPSHOT_DIR / "run-history.jsonl"
_LEGACY_RUN_HISTORY = DATA_DIR / "run_history.jsonl"

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

# spaCy model preference for the hybrid + spacy backends. The list is tried
# in order until one loads, so opting into a larger model is as simple as
# making sure it's installed — and a missing optional model never breaks the
# pipeline. The default order prefers a larger model (higher recall) while
# falling back to the small one that ships with the project's docs.
SPACY_MODELS = ("en_core_web_md", "en_core_web_sm")


def ensure_dirs():
    """Create the data, snapshot, and corpus directories if they are missing."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
