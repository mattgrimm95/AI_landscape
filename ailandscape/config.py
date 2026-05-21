"""Filesystem paths and shared constants."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SNAPSHOT_DIR = ROOT / "snapshots"

RAW_LOG_DB = DATA_DIR / "raw_log.db"
KG_DB = DATA_DIR / "knowledge_graph.db"

HTTP_USER_AGENT = (
    "AILandscapeBot/0.1 (research project; "
    "+https://github.com/mattgrimm95/AI_landscape)"
)
HTTP_TIMEOUT = 20


def ensure_dirs():
    """Create the data and snapshot directories if they do not exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
