"""Orchestrates the full app_plan flow: scrape -> NER -> log -> graph."""

from . import ner, reconcile, scraper


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


def ingest_articles(articles, raw_store, ner_backend=None, log=None):
    """Steps 2-3: run NER on each new article and append it to the raw log."""
    log = log or (lambda *_a: None)
    new_documents = 0
    skipped = 0
    entities = 0
    for article in articles:
        doc_id, is_new = raw_store.add_document(
            article, scraper.content_hash(article)
        )
        if not is_new:
            skipped += 1
            continue
        text = (
            article.get("title", "") + ". " + article.get("raw_text", "")
        ).strip()
        extracted = ner.extract(text, backend=ner_backend)
        raw_store.add_entities(doc_id, extracted)
        new_documents += 1
        entities += len(extracted)
        log(
            "ingested: %s (%d entities)"
            % (article.get("title", "")[:70], len(extracted))
        )
    return {
        "new_documents": new_documents,
        "skipped": skipped,
        "entities": entities,
    }


def run(feeds, raw_store, kg_store, ner_backend=None, corrections=None, log=None):
    """Run the entire flow end to end and return a summary dict."""
    log = log or (lambda *_a: None)
    articles = fetch_all(feeds, log=log)
    ingest = ingest_articles(
        articles, raw_store, ner_backend=ner_backend, log=log
    )
    graph = reconcile.reconcile(
        raw_store, kg_store, corrections=corrections, log=log
    )
    return {"fetched": len(articles), "ingest": ingest, "graph": graph}
