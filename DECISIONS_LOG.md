# Decisions Log

A high-level record of steps taken and decisions made while implementing the
`app_plan.md` flow. Newest entries at the bottom.

## 2026-05-21 — Initial implementation

### Step 1 — Environment review
- Python 3.9.5 available; network reachable; `beautifulsoup4` already installed.
- `feedparser` and `spacy` not installed — scheduled for install.

### Step 2 — Architecture decisions
- **Language: Python.** Best ecosystem for scraping + NER; already present.
- **Storage: two SQLite databases** (the "multiple databases" from the flow):
  - `data/raw_log.db` — the *database log*: scraped documents + raw NER output.
  - `data/knowledge_graph.db` — *knowledge graph*: nodes, edges, aliases.
  - SQLite chosen for **minimal-to-no-cost** hosting (file-based, no server)
    and low operational complexity.
- **Version-controllable / revertible:** `data/*.db` are gitignored (volatile),
  but a `snapshot` command exports both databases to JSON in `snapshots/`,
  which *is* committed. Restoring = re-importing a snapshot. This satisfies
  "version-controllable objects including the database to revert".
- **Non-destructive by default:** the raw log is append-only; documents are
  de-duplicated by content hash; graph writes are upserts (never deletes).
  The only destructive command (`reset`) requires an explicit `--confirm`
  flag — i.e. it must be clearly initiated by a human prompt.
- **NER: pluggable backends.** `spaCy` (`en_core_web_sm`) when available; a
  deterministic rule-based extractor as fallback. Keeps tests fast and the
  system runnable even before the heavy model is installed.

### Step 2b — Dependency pivot (no heavy deps)
- `pip` cannot reach PyPI from this machine — SSL certificate verification
  fails for `pypi.org`. `feedparser` and `spacy` could not be installed.
- **Decision:** build entirely on the **Python standard library +
  `beautifulsoup4`** (already installed). Rather than disable TLS verification
  to force the installs, the system is designed with no heavy dependencies:
  - Feeds parsed with stdlib `xml.etree.ElementTree` (RSS 2.0 + Atom).
  - HTTP fetched with stdlib `urllib.request`.
  - NER done by the rule-based extractor plus a curated national-security
    **gazetteer** (high-precision domain matching).
  This *improves* the system against the skills_plan paradigms — zero install
  friction, minimal cost, low computational complexity, faster MVP — so it is
  adopted as the primary design, not a fallback. The NER layer keeps a
  pluggable `spacy` backend that is used automatically if the package is ever
  installed.

### Step 3 — Flow / guideline adjustments
- **Modular monolith instead of literal microservices.** The plan lists a
  microservices paradigm; for the MVP the modules (`scraper`, `ner`,
  `storage`, `reconcile`, `pipeline`) are kept as separate, independently
  testable units inside one package. This reaches MVP faster and keeps cost
  near zero; the seams are clean enough to split into services later.
- **Scraping uses feed-provided summaries/content** (RSS/Atom) rather than
  fetching and parsing every full article page. This is more stable, far
  lighter, and keeps the focus on the databases + NER as requested.
  Full-article fetching is recorded as a future extension.
- **Relationships are co-occurrence edges** for the MVP: entities appearing in
  the same document are linked (`co_occurs_with`), with edge weight =
  co-occurrence count. Typed/semantic relations are a future extension.

### Step 4 — Implementation
- Package `ailandscape/`: `scraper`, `ner` (+ `gazetteer`), `storage_raw`,
  `storage_kg`, `reconcile`, `pipeline`, `cli`.
- CLI verbs: `run` (live feeds), `demo` (bundled `samples/sample_feed.xml`,
  no network), `stats`, `snapshot`, `reset --confirm`.
- 24 `unittest` tests in `tests/` (storage, scraper, NER, reconcile,
  end-to-end pipeline) — all passing.

### Step 5 — Verification and an NER-quality iteration
- First live run: 3 feeds, 82 articles → 82 documents, ~8.2k raw entities.
  The graph had ~2.9k nodes — clearly inflated, because the greedy
  rule-based NER turns every sentence-initial capitalized word into an
  entity.
- **Adjustment (NER precision):** the app_plan flow already separates "NER /
  data creation" (raw) from step 4 "filter / de-duplicate / reconcile". So
  the NER stage is kept deliberately greedy and faithful in the raw log, and
  a precision filter was added to `reconcile`: untyped single-word ("misc")
  entities are dropped unless several documents mention them (document
  frequency >= 2). Typed gazetteer hits, multi-word phrases, and
  human-curated merges are always kept. The gazetteer was also expanded
  (more countries, commands, and systems).
- Second live run after the adjustment: 82 documents, ~8.3k raw entities ->
  1,860 nodes, 26,368 co-occurrence edges. Top entities (United States,
  Iran, U.S. Navy/Air Force/Army, Trump, Israel, Ukraine, Pentagon) are all
  legitimate and correctly typed.
- `snapshot` verified: both databases export to a single JSON file under
  `snapshots/` and round-trip cleanly.
