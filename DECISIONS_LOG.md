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

## 2026-05-21 — Source-of-truth refactor (reproducible databases)

### Why
- Question raised: would `data/*.db` really "regenerate the same"? It would
  not — `run` fetches whatever the live feeds currently hold, and feed
  contents roll over time. The graph was deterministic *given* the raw log,
  but the raw log itself was not reproducible, and it was gitignored — so
  losing it meant losing unrecoverable scraped history. The skills_plan
  "version-controllable / revertible" requirement was therefore not met.

### Change
- Added a **corpus**: `corpus/documents.jsonl`, an append-only, one-object-
  per-line file of scraped documents. It is **committed to git** and is the
  single source of truth.
- Both SQLite databases are now **derived caches**. `pipeline.rebuild`
  regenerates them from the corpus; `data/` stays gitignored.
- Determinism work so the same corpus always yields the same databases:
  - `fetched_at` and `content_hash` are captured once at scrape time and
    stored in the corpus (not regenerated on rebuild).
  - entity `extracted_at` is derived from the document's `fetched_at`, not
    the wall clock.
  - `clear()` resets SQLite autoincrement counters, so document/node/edge
    ids are reproducible across rebuilds.
- New CLI verb `rebuild` regenerates the databases from the corpus with no
  network. `run` = scrape-into-corpus + rebuild. `demo` now runs fully in a
  throwaway directory. `reset` deletes only the derived databases and leaves
  the corpus intact.

### Verification
- Live run: 81 documents into the corpus -> 1,786 nodes, 27,912 edges.
- Reproducibility proven: SHA-256 of the knowledge graph and of the raw log
  are byte-identical before and after a `rebuild` from the same corpus.
- Test suite expanded to 31 tests, including corpus round-trip and a
  rebuild-determinism check; all passing.

## 2026-05-21 — NER output log refactor

### Why
- After the corpus became the source of truth, `raw_log.db` was no longer a
  source of truth — just a derived cache. Its `documents` table was a near
  exact duplicate of the corpus, which is redundant.

### Change
- Renamed `raw_log.db` -> `ner_output_log.db` (module `storage_raw.py` ->
  `storage_ner.py`, class `RawLogStore` -> `NEROutputLog`).
- **Dropped the `documents` table.** The store now holds only the `entities`
  table — raw NER output keyed by the corpus document's `content_hash`.
- Dropped the `extracted_at` column (the corpus already records `fetched_at`).
- `reconcile` now takes the corpus document list plus the NER log and joins
  them on `content_hash`; `pipeline.rebuild` runs NER straight into the log.
- Net effect: one clear chain — corpus (source of truth) -> NER output log
  (entity cache) -> knowledge graph — with no duplicated document storage.

### Verification
- All 31 tests updated and passing.
- Behaviour preserved: `demo` still yields 18 nodes / 49 edges, and a
  `rebuild` from the existing 81-document corpus still yields 1,786 nodes /
  27,912 edges — identical to before the refactor.

## 2026-05-21 — Daily scrape automation

- Added `scripts/daily_scrape.ps1`: runs `ailandscape.cli run`, then commits
  and pushes the corpus when new articles were added. Paths are resolved
  relative to the script's own location (repo) and from `PATH` (python/git),
  so nothing environment-specific is hardcoded — the script leaks no username
  or machine layout and is safe to commit to the public repo.
- Registered a Windows Task Scheduler job ("AI_landscape Daily Scrape") that
  runs the script daily at 19:00 local time (7PM Eastern; the OS handles the
  EST/EDT switch). `StartWhenAvailable` lets a missed run catch up when the
  PC next wakes. The scheduled-task entry is machine-local config, not part
  of the repo.
- Chose Windows Task Scheduler over a cloud scheduled agent: the job is a
  fixed, deterministic command that needs no AI reasoning, so a local
  cron-style task is free, has no billing question, and is the right tool.

## 2026-05-21 — AI-focused feeds and corpus recreation

- Replaced the 3 general feeds with **12 AI-focused feeds**, each verified to
  return a parseable RSS/Atom feed. Split **8 national-defense : 4 public AI
  = 2:1** by feed count, as requested. The public set includes MIT News (AI
  topic feed) and the Stanford AI Lab blog.
- Added `scraper.MAX_ARTICLES_PER_FEED` (50): only the 50 most-recent entries
  of any feed are kept, so one large feed cannot dominate the corpus. This
  was necessary — OpenAI's feed alone returned 971 entries, which uncapped
  would have flooded the corpus and wrecked the intended defense focus.
- Recreated the corpus from scratch (deleted and rebuilt): 378 documents
  across the 12 feeds (213 defense / 165 public) -> 18,495 entities ->
  4,573 graph nodes, 72,167 edges.

## 2026-05-22 — Per-article extraction with trafilatura

### Why
- The scraper only parsed RSS/Atom feeds and took whatever they embedded —
  often just a teaser, and always carrying boilerplate (photo credits,
  "appeared first on …", author contact lines) that polluted NER.

### Change
- `pip` could not reach PyPI: its bundled `certifi` lacked CA roots present
  in the Windows trust store. Fixed by installing with `--cert` pointed at an
  exported Windows CA bundle — TLS verification stays fully on (no
  `--trusted-host` bypass). The bundle was a one-off and has been deleted.
- Added `trafilatura` (+ `lxml_html_clean`). The scraper now fetches each
  article's own page and extracts the main text with trafilatura, which
  strips navigation, ads, captions, and footers. The feed's embedded content
  is the fallback when a page cannot be fetched or extracted.
- De-duplication is now by URL+title (`content_hash` no longer includes body
  text), so a known article is skipped *before* its page is fetched.
- A 1s polite delay separates article-page fetches.
- Fixed a Windows crash: a `print` of a title containing characters outside
  cp1252 killed the run; the CLI now forces UTF-8 stdout/stderr.

### Result
- Recreated corpus: 378 documents; 278 of the 354 freshly scraped docs got
  clean trafilatura extraction (the rest fell back to feed content — blocked
  or empty pages). Median document length rose to ~4,500 characters.
  25,016 entities -> 5,628 nodes, 116,929 edges.

## 2026-05-22 — feedparser, and a spaCy NER measurement

### feedparser
- Replaced the hand-rolled `ElementTree` feed parsing with `feedparser`,
  removing ~40 lines (`_strip_namespaces`, `_text_of`, `_link_of`) and
  tolerating malformed feeds instead of crashing on them. `pip` itself was
  also upgraded (21.1.1 -> 26.0.1).

### spaCy — measured, not adopted as default
- The latest spaCy (3.8) dropped Python 3.9 support, so `spacy<3.8` (3.7.5)
  plus `en_core_web_sm` were installed.
- Rebuild measurement on the 378-document corpus:
  - rule:  ~5 s  -> 25,016 entities, 5,628 nodes, 116,929 edges
  - spaCy: ~58 s -> 21,394 entities, 6,601 nodes,  98,409 edges
- spaCy types every node (no "misc" bucket) and finds more distinct
  entities, but it returns raw surface forms — losing the gazetteer's
  canonicalization ("U.S." and "The United States" become separate nodes) —
  and the small model mislabels some organizations as people. Neither
  backend is a clear win; a gazetteer + spaCy hybrid would be.
- Decision: the rule backend stays the default. The default is now an
  explicit setting (`config.DEFAULT_NER_BACKEND`) rather than being inferred
  from whichever package is installed — installing spaCy must not silently
  change pipeline behaviour. spaCy is available on demand via `--ner spacy`.

## 2026-05-22 — Hybrid NER backend and stronger de-duplication

### Hybrid NER (new default)
- Added a "hybrid" NER backend: the gazetteer runs first (precise, canonical
  names for curated defense entities), and spaCy supplies typed entities for
  the rest of the text; where a spaCy span overlaps a gazetteer span the
  gazetteer wins. It degrades to the rule backend if spaCy is absent.
- Made "hybrid" the default. Rebuild: 6,000 nodes (rule 5,628 /
  pure-spaCy 6,601). Every node is typed — there is no "misc" bucket. The
  gazetteer's canonicalization fixes pure-spaCy's fragmentation: "United
  States" is now a single node (1,048 mentions) rather than separate
  "U.S." and "The United States" nodes.

### Stronger de-duplication
- `reconcile.normalize` now collapses more wording variants onto the same
  node key, so the variants merge and their relationship edges merge with
  them: possessive "'s", acronym dots ("U.S." == "US"), and a trailing
  plural on the final word ("drone swarms" == "drone swarm", "F-35s" ==
  "F-35").
- Default ignore terms are normalized before matching so they keep working
  under the new normalization.

## 2026-05-22 — Statistical overview report (first attempt)

- Added `report.py` and an `overview` CLI command: a readable statistical
  summary of the pipeline data — the funnel (articles -> raw mentions ->
  nodes -> edges), scrape recency / "scraped in the last 24h", scrape
  duration, entity- and relationship-type breakdowns, the most prominent and
  most-connected entities, and data-quality signals (single-mention nodes,
  possible partial-name duplicates).
- `pipeline.run` now records each run's timing and counts to
  `data/run_history.jsonl`; the overview reports the latest run.
- First-attempt findings on the 378-document corpus: 22,091 raw mentions
  collapse to 6,000 nodes, but ~69% of nodes are single-mention (long tail /
  noise) and ~277 look like partial-name duplicates — both point at
  entity-resolution as the next improvement (tracked in TODO).
