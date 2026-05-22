"""Orchestrates the full app_plan flow.

The corpus (`corpus/documents.jsonl`) is the version-controlled source of
truth. `run` scrapes new documents into the corpus; `rebuild` regenerates the
NER output log and the knowledge graph from the corpus deterministically —
the same corpus always yields the same outputs.
"""

import datetime
import time

from . import corpus, ner, reconcile, scraper

# Polite pause between article-page fetches during scraping.
ARTICLE_FETCH_DELAY = 1.0


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
        text = (
            doc.get("title", "") + ". " + doc.get("raw_text", "")
        ).strip()
        extracted = ner.extract(text, backend=ner_backend)
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


def run(
    feeds,
    corpus_path,
    ner_log,
    kg_store,
    ner_backend=None,
    corrections=None,
    log=None,
):
    """Run the entire flow: scrape into the corpus, then rebuild everything."""
    log = log or (lambda *_a: None)
    scrape = scrape_into_corpus(feeds, corpus_path, log=log)
    rebuilt = rebuild(
        corpus_path,
        ner_log,
        kg_store,
        ner_backend=ner_backend,
        corrections=corrections,
        log=log,
    )
    return {
        "scrape": scrape,
        "documents": rebuilt["documents"],
        "entities": rebuilt["entities"],
        "graph": rebuilt["graph"],
    }
