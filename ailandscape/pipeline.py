"""Orchestrates the full app_plan flow.

The corpus (`corpus/documents.jsonl`) is the version-controlled source of
truth. `run` scrapes new documents into the corpus; `rebuild` regenerates the
NER output log and the knowledge graph from the corpus deterministically —
the same corpus always yields the same outputs.
"""

import datetime
import json
import time

from . import config, corpus, jbooks, ner, reconcile, sbir, scraper

# Polite pause between article-page fetches during scraping.
ARTICLE_FETCH_DELAY = 1.0

# Corpus documents with less body text than this stored only a feed teaser
# (their article page failed to extract at scrape time) and are candidates
# for a re-fetch by `backfill_corpus_text`.
SHORT_TEXT_THRESHOLD = 400


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def make_record(article):
    """Turn a freshly scraped article into a corpus document record.

    `fetched_at` and `content_hash` are captured once here and then live in
    the corpus unchanged, which is what keeps a later rebuild reproducible.
    """
    return {
        "source": article.get("source", ""),
        "url": article.get("url", ""),
        "title": article.get("title", ""),
        "published": article.get("published", ""),
        "fetched_at": _utcnow(),
        "content_hash": scraper.content_hash(article),
        "raw_text": article.get("raw_text", ""),
        "metadata": article.get("metadata", {}),
    }


def fetch_all(feeds, log=None):
    """Step 1: fetch and parse every feed, tolerating per-feed failures."""
    log = log or (lambda *_a: None)
    articles = []
    for feed in feeds:
        try:
            feed_articles = scraper.fetch_feed(feed)
        except scraper.FeedError as exc:
            log("WARN feed failed: %s" % exc)
            continue
        log("fetched %d articles from %s" % (len(feed_articles), feed["name"]))
        articles.extend(feed_articles)
    return articles


def scrape_into_corpus(feeds, corpus_path, log=None):
    """Step 1: fetch feeds, then for each previously unseen article fetch its
    page, extract the clean main text, and append it to the corpus.

    Articles are de-duplicated by URL+title *before* their pages are fetched,
    so known articles are never re-downloaded.
    """
    log = log or (lambda *_a: None)
    articles = fetch_all(feeds, log=log)
    known = corpus.hashes(corpus_path)
    added = 0
    extracted = 0
    for article in articles:
        chash = scraper.content_hash(article)
        if chash in known:
            continue
        known.add(chash)
        # New article: fetch its page and extract the clean main text,
        # falling back to the feed's embedded content if that fails.
        feed_text = article.get("raw_text", "")
        article["raw_text"] = scraper.extract_article(
            article["url"], fallback=feed_text
        )
        if article["raw_text"] != feed_text:
            extracted += 1
        corpus.append(corpus_path, make_record(article))
        added += 1
        log("corpus += %s" % article.get("title", "")[:70])
        time.sleep(ARTICLE_FETCH_DELAY)
    return {"fetched": len(articles), "added": added, "extracted": extracted}


def scrape_sbir_into_corpus(sbir_queries, corpus_path, log=None):
    """Step 1 (non-RSS): pull AI-related SBIR/STTR awards into the corpus.

    For each query the SBIR API is paged, awards are filtered to AI-related
    ones, and previously unseen awards are appended to the corpus. The
    public API is aggressively gateway-throttled and can return HTTP 429
    for every request, so this is tolerant of failure: if the first query
    fails, SBIR is skipped for the run and the rest of the pipeline
    continues — the same way a single failed RSS feed is tolerated. New
    additions are capped per run so SBIR cannot dominate the corpus.
    """
    log = log or (lambda *_a: None)
    known = corpus.hashes(corpus_path)
    added = 0
    for index, query in enumerate(sbir_queries):
        if added >= sbir.MAX_AI_AWARDS:
            break
        agency = query.get("agency", "DOD")
        year = query.get("year")
        try:
            awards = sbir.fetch_awards(
                agency=agency,
                year=year,
                max_records=query.get("max_records", 200),
            )
        except sbir.SBIRError as exc:
            log("WARN SBIR query (%s %s) failed: %s"
                % (agency, year or "all years", exc))
            if index == 0:
                log("WARN skipping SBIR for this run (API unavailable)")
                break
            continue
        articles = sbir.ai_articles(awards)
        log("SBIR %s %s: %d awards fetched, %d AI-related"
            % (agency, year or "all years", len(awards), len(articles)))
        for article in articles:
            if added >= sbir.MAX_AI_AWARDS:
                break
            chash = scraper.content_hash(article)
            if chash in known:
                continue
            known.add(chash)
            corpus.append(corpus_path, make_record(article))
            added += 1
            log("corpus += [SBIR] %s" % article.get("title", "")[:64])
    return {"sbir_added": added}


def backfill_corpus_text(corpus_path, log=None):
    """Re-fetch the page text of corpus documents that stored only a short
    summary — articles whose pages failed to extract at scrape time and fell
    back to the feed's teaser.

    A document's `raw_text` is replaced only when the re-fetch yields
    substantially more text, so a backfill can only improve the corpus, never
    degrade it. `content_hash` is derived from URL + title (never the body),
    so repaired text keeps the document's identity and a rebuild stays
    deterministic.
    """
    log = log or (lambda *_a: None)
    documents = corpus.load(corpus_path)
    repaired = 0
    for doc in documents:
        body = (doc.get("raw_text") or "").strip()
        url = doc.get("url", "")
        if len(body) >= SHORT_TEXT_THRESHOLD or not url:
            continue
        better = scraper.extract_article(url, fallback=body).strip()
        # Replace only on a clear improvement, so a failed re-fetch (which
        # returns the short fallback) leaves the document untouched.
        if len(better) > max(2 * len(body), SHORT_TEXT_THRESHOLD):
            doc["raw_text"] = better
            repaired += 1
            log("backfilled %s" % (doc.get("title", "")[:70]))
        time.sleep(ARTICLE_FETCH_DELAY)
    if repaired:
        corpus.save(corpus_path, documents)
    return {"scanned": len(documents), "repaired": repaired}


def scrape_jbooks_into_corpus(jbook_sources, corpus_path, log=None):
    """Step 1 (non-RSS): pull AI-related R&D items from DoD J-Books.

    Each source's budget-materials index page is crawled for PDFs; PDFs are
    fetched, their text extracted, and the AI-related R&D program elements
    appended to the corpus. Tolerant of failure: if a source page fails or
    pypdf is not installed, that source is skipped for the run, the rest of
    the pipeline continues. New additions are capped per run.
    """
    log = log or (lambda *_a: None)
    known = corpus.hashes(corpus_path)
    added = 0
    for source in jbook_sources:
        if added >= jbooks.MAX_JBOOK_PROJECTS:
            break
        try:
            articles = jbooks.fetch_jbook_articles(
                source["url"], source["fiscal_year"], source["agency"],
                log=log,
            )
        except jbooks.JBookError as exc:
            log("WARN J-Book source (%s) failed: %s"
                % (source["url"], exc))
            continue
        log(
            "J-Book %s %s: %d AI-related items"
            % (source["agency"], source["fiscal_year"], len(articles))
        )
        for article in articles:
            if added >= jbooks.MAX_JBOOK_PROJECTS:
                break
            chash = scraper.content_hash(article)
            if chash in known:
                continue
            known.add(chash)
            corpus.append(corpus_path, make_record(article))
            added += 1
            log("corpus += [J-Book] %s" % (article.get("title", "")[:64]))
    return {"jbooks_added": added}


def rebuild(
    corpus_path, ner_log, kg_store, ner_backend=None, corrections=None, log=None
):
    """Regenerate the NER output log and knowledge graph from the corpus
    (steps 2-5).

    Deterministic: the same corpus always produces the same NER log and the
    same knowledge graph.
    """
    log = log or (lambda *_a: None)
    documents = corpus.load(corpus_path)
    ner_log.clear()
    entity_count = 0
    for doc in documents:
        extracted = ner.extract(
            corpus.document_text(doc), backend=ner_backend
        )
        ner_log.add_entities(doc["content_hash"], extracted)
        entity_count += len(extracted)
    log(
        "ran NER on %d documents (%d entities)"
        % (len(documents), entity_count)
    )
    graph = reconcile.reconcile(
        documents, ner_log, kg_store, corrections=corrections, log=log
    )
    return {
        "documents": len(documents),
        "entities": entity_count,
        "graph": graph,
    }


def _record_run(result, scrape_seconds, rebuild_seconds):
    """Append a timing + counts record for this run to the run-history log."""
    record = {
        "finished_at": _utcnow(),
        "scrape_seconds": round(scrape_seconds, 1),
        "rebuild_seconds": round(rebuild_seconds, 1),
        "fetched": result["scrape"]["fetched"],
        "added": result["scrape"]["added"],
        "sbir_added": result["scrape"].get("sbir_added", 0),
        "jbooks_added": result["scrape"].get("jbooks_added", 0),
        "documents": result["documents"],
        "entities": result["entities"],
        "nodes": result["graph"]["nodes"],
        "edges": result["graph"]["edges"],
    }
    config.RUN_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with config.RUN_HISTORY_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def run(
    feeds,
    corpus_path,
    ner_log,
    kg_store,
    sbir_queries=None,
    jbook_sources=None,
    ner_backend=None,
    corrections=None,
    log=None,
):
    """Run the entire flow: scrape into the corpus, then rebuild everything.

    Scrapes RSS/Atom feeds and, when given, AI-related SBIR/STTR awards and
    DoD J-Books. Records the run's timing and counts to the run-history log.
    """
    log = log or (lambda *_a: None)
    started = time.time()
    scrape = scrape_into_corpus(feeds, corpus_path, log=log)
    if sbir_queries:
        scrape.update(scrape_sbir_into_corpus(sbir_queries, corpus_path, log=log))
    if jbook_sources:
        scrape.update(
            scrape_jbooks_into_corpus(jbook_sources, corpus_path, log=log)
        )
    scrape_seconds = time.time() - started

    started = time.time()
    rebuilt = rebuild(
        corpus_path,
        ner_log,
        kg_store,
        ner_backend=ner_backend,
        corrections=corrections,
        log=log,
    )
    rebuild_seconds = time.time() - started

    result = {
        "scrape": scrape,
        "documents": rebuilt["documents"],
        "entities": rebuilt["entities"],
        "graph": rebuilt["graph"],
    }
    _record_run(result, scrape_seconds, rebuild_seconds)
    return result
