"""Orchestrates the full app_plan flow.

The corpus (`corpus/documents.jsonl`) is the version-controlled source of
truth. `run` scrapes new documents into the corpus; `rebuild` regenerates
both SQLite databases from the corpus deterministically — the same corpus
always yields the same raw log and the same knowledge graph.
"""

import datetime

from . import corpus, ner, reconcile, scraper


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
    """Step 1: fetch feeds and append previously unseen documents to the
    corpus — the durable, version-controlled source of truth."""
    log = log or (lambda *_a: None)
    articles = fetch_all(feeds, log=log)
    known = corpus.hashes(corpus_path)
    added = 0
    for article in articles:
        record = make_record(article)
        if record["content_hash"] in known:
            continue
        corpus.append(corpus_path, record)
        known.add(record["content_hash"])
        added += 1
        log("corpus += %s" % article.get("title", "")[:70])
    return {"fetched": len(articles), "added": added}


def ingest_articles(articles, raw_store, ner_backend=None, log=None):
    """Steps 2-3: run NER on each article and write it to the raw log."""
    log = log or (lambda *_a: None)
    new_documents = 0
    skipped = 0
    entities = 0
    for article in articles:
        content_hash = (
            article.get("content_hash") or scraper.content_hash(article)
        )
        doc_id, is_new = raw_store.add_document(article, content_hash)
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
    log("ingested %d documents (%d entities)" % (new_documents, entities))
    return {
        "new_documents": new_documents,
        "skipped": skipped,
        "entities": entities,
    }


def rebuild(
    corpus_path, raw_store, kg_store, ner_backend=None, corrections=None, log=None
):
    """Regenerate both databases from the corpus (steps 2-5).

    Deterministic: the same corpus always produces the same raw log and the
    same knowledge graph.
    """
    log = log or (lambda *_a: None)
    documents = corpus.load(corpus_path)
    raw_store.clear()
    ingest = ingest_articles(
        documents, raw_store, ner_backend=ner_backend, log=log
    )
    graph = reconcile.reconcile(
        raw_store, kg_store, corrections=corrections, log=log
    )
    return {"documents": len(documents), "ingest": ingest, "graph": graph}


def run(
    feeds,
    corpus_path,
    raw_store,
    kg_store,
    ner_backend=None,
    corrections=None,
    log=None,
):
    """Run the entire flow: scrape into the corpus, then rebuild the databases."""
    log = log or (lambda *_a: None)
    scrape = scrape_into_corpus(feeds, corpus_path, log=log)
    rebuilt = rebuild(
        corpus_path,
        raw_store,
        kg_store,
        ner_backend=ner_backend,
        corrections=corrections,
        log=log,
    )
    return {
        "scrape": scrape,
        "documents": rebuilt["documents"],
        "ingest": rebuilt["ingest"],
        "graph": rebuilt["graph"],
    }
