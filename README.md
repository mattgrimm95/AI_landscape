# AI Landscape

An accurate, navigable **knowledge graph of the AI national-security
landscape**, built by scraping defense and AI news, extracting entities and
relationships, and reconciling them into a graph you can explore in a
browser.

## How it works

The pipeline (see `app_plan.md`) runs in stages:

```
RSS/Atom feeds + SBIR/STTR awards    ailandscape/feeds.py
  -> scrape articles / fetch awards  ailandscape/scraper.py · sbir.py
  -> corpus/documents.jsonl          the version-controlled source of truth
  -> named-entity recognition        ailandscape/ner.py       (gazetteer + spaCy)
  -> NER output log (SQLite)         ailandscape/storage_ner.py
  -> reconcile / de-dup / coref      ailandscape/reconcile.py
  -> typed relationships             ailandscape/relations.py
  -> knowledge graph (SQLite)        ailandscape/storage_kg.py
  -> explore                         web app / visualize / overview
```

**Data sources.** Two kinds feed the corpus: defense and AI **news feeds**
(RSS/Atom, roughly 2:1 defense-to-public), and **SBIR/STTR award records**
from the SBIR.gov public API — awarded contracts are a concrete primary
source for where defense AI funding goes. Awards have no keyword search, so
`ailandscape/sbir.py` filters them to AI-related ones locally.

**The corpus is the source of truth.** `corpus/documents.jsonl` is an
append-only, committed file of scraped documents. Both SQLite databases
(`data/ner_output_log.db`, `data/knowledge_graph.db`) are *derived caches* —
`rebuild` regenerates them deterministically, so the graph is always
reproducible from version-controlled text.

## Setup

Requires **Python 3.9+**.

```
pip install -r requirements.txt
```

`spaCy` is optional but recommended — it powers the default `hybrid` NER
backend (gazetteer + statistical model). Without it the pipeline falls back
to the rule-based backend automatically.

```
pip install "spacy<3.8"            # 3.8+ needs Python 3.10+
python -m spacy download en_core_web_sm
```

## Usage

```
python -m ailandscape.cli run        # scrape articles + SBIR awards, then rebuild
python -m ailandscape.cli rebuild    # rebuild the databases from the corpus
python -m ailandscape.cli sbir       # pull AI-related SBIR/STTR awards, then rebuild
python -m ailandscape.cli demo       # run the flow on the bundled sample feed
python -m ailandscape.cli stats      # quick corpus / database counts
python -m ailandscape.cli overview   # full statistical overview of the data
python -m ailandscape.cli serve      # interactive web app at 127.0.0.1:8000
python -m ailandscape.cli visualize  # export a static interactive HTML graph
python -m ailandscape.cli correct merge "DoD" "Department of Defense"
python -m ailandscape.cli snapshot   # export corpus + databases to snapshots/
python -m ailandscape.cli reset --confirm   # delete the derived databases
```

### Web app

`serve` starts a FastAPI backend + Cytoscape.js frontend. The backend
queries and subsets the full graph; the browser only ever renders a focused
slice, so it stays smooth. Search, filter, click a node for its
neighborhood, and make corrections (merge / ignore) in the UI — corrections
are written to `corrections.json` and applied by re-running reconcile.

## Manual corrections

`corrections.json` (`{"merge": {...}, "ignore": [...]}`) is consumed by
reconcile. It is version-controlled, so corrections persist and the graph
can still be reconstructed deterministically from corpus + corrections.

## Automation

`scripts/daily_scrape.ps1` runs `run` and commits the corpus; it is wired to
a Windows Task Scheduler job that fires daily.

## Testing

```
python -m unittest discover -s tests -t .
```

## Decisions

`DECISIONS_LOG.md` records the architecture choices and notable changes.
`skills_plan.md` and `TODO.txt` track design paradigms and future work.
