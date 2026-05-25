"""Entity enrichment — pull targeted web content into the corpus.

The corpus is the source of truth; every downstream artefact (NER log,
knowledge graph, dossiers, briefings) is derived from it. Enrichment is
just: append more documents about the entity you want better coverage of,
then re-run the standard rebuild. NER and the typed-relation extractor
do the rest, surfacing adjacent entities (other organizations, products,
people, contracts) as a natural side-effect of scanning the new text.

This module is intentionally small. It does NOT decide which articles to
fetch — that's the caller's job, because the right sources are entity-
specific (a company blog, a regulator filing, a major outlet's profile
piece). It handles the mechanics: fetch, clean, dedupe, and append as
properly-shaped corpus records.

Three entry points:

  * `add_article(corpus_path, url, title, source, ...)` — fetch one URL,
    extract its main text, append as a corpus record. Skips silently if
    the URL was already ingested.

  * `add_synthesis(corpus_path, entity, body, ...)` — append a synthesized
    overview document (e.g. a Claude-written entity profile) to the corpus
    so its named entities + stated relationships propagate into the graph.

  * `enrich_from_plan(corpus_path, plan)` — execute a full enrichment
    plan: a list of articles to fetch plus an optional synthesis body.
    Returns counts so the caller can report what landed.

A `plan` is a dict shaped:
    {
        "entity": "Palantir",
        "articles": [
            {"url": "...", "title": "...", "source": "...",
             "published": "YYYY-MM-DD"},
            ...
        ],
        "synthesis": {
            "title": "Palantir landscape overview (Claude synthesis)",
            "body": "Multi-paragraph fact-dense text ...",
            "source": "Claude synthesis",
        },
    }

`published` is optional; an empty string is fine. Articles whose content
hash already exists in the corpus are skipped (dedup by URL+title, same
as the rest of the pipeline). The synthesis is keyed on a deterministic
URL so re-running with the same body is a no-op too.
"""

import datetime
import hashlib

from . import ai_terms, corpus, scraper


# ---- AI-relevance gate -----------------------------------------------------
#
# The whole project is scoped to AI national-security reporting; an enrich
# run that quietly drops non-AI articles into the corpus would dilute that
# focus and pollute the graph with off-topic entities. The lexicon lives
# in ailandscape/ai_terms.py and is shared with SBIR and pipeline scrape
# filters so the bar is consistent across all data sources -- one
# term-list tweak lifts every gate at once.
#
# Plan-level (not per-article) granularity: an enrichment plan is about
# one entity / topic, and the synthesis is the operator's canonical
# statement of WHY this batch is being added. If the synthesis OR any
# article shows AI signal, the plan is admitted in full -- this matches
# the real-world case where a B-21 plan contains production-deal stories
# (no explicit "AI" term) but the synthesis frames B-21 as an AI-enabled
# platform. The escape hatch is `enrich_from_plan(..., allow_non_ai=True)`,
# used by the CLI's --allow-non-ai flag for deliberate exceptions.


def _is_ai_relevant(text):
    """True if ``text`` contains an AI/ML/autonomy term — delegates to the
    shared ``ai_terms.is_ai_relevant`` so the bar matches every other gate."""
    return ai_terms.is_ai_relevant(text)


def plan_ai_signal(plan):
    """Return the AI-relevance signal for an enrichment plan as a dict.

    Returns {"ok", "matched_in", "matched_terms"}:
      * `ok` is True if any article (title + html) OR the synthesis body
        contains an AI/ML/autonomy term.
      * `matched_in` is a short tag identifying where the signal came from
        ("synthesis", "article: <url>", or "").
      * `matched_terms` is a deduped list of the actual term hits (handy
        for an "AI relevance: ..." log line).
    """
    synthesis = plan.get("synthesis") or {}
    syn_text = " ".join([
        str(synthesis.get("title", "")),
        str(synthesis.get("body", "")),
    ])
    if _is_ai_relevant(syn_text):
        return {
            "ok": True,
            "matched_in": "synthesis",
            "matched_terms": _ai_terms_in(syn_text),
        }
    for article in plan.get("articles") or []:
        text = " ".join([
            str(article.get("title", "")),
            str(article.get("html", "")),
        ])
        if _is_ai_relevant(text):
            return {
                "ok": True,
                "matched_in": "article: %s" % article.get("url", "?"),
                "matched_terms": _ai_terms_in(text),
            }
    return {"ok": False, "matched_in": "", "matched_terms": []}


def _ai_terms_in(text):
    """Distinct AI term hits in ``text``, for diagnostic logging."""
    return ai_terms.ai_terms_in(text)


def _utcnow_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _synth_url(entity, body):
    """A deterministic URL for a Claude-written synthesis of an entity.

    Hashing the body too means re-running with the same synthesis is
    deduplicated, but a refreshed synthesis (different body) becomes a
    new document instead of silently overwriting the old one.
    """
    digest = hashlib.sha256((entity + "::" + body).encode("utf-8")).hexdigest()[:16]
    return "claude-synthesis://" + entity.replace(" ", "-").lower() + "/" + digest


def _make_record(*, source, url, title, body, published="", metadata=None):
    """Build a corpus record in the same shape `pipeline.make_record` does."""
    article = {
        "source": source,
        "url": url,
        "title": title,
        "published": published,
        "raw_text": body,
    }
    return {
        "source": source,
        "url": url,
        "title": title,
        "published": published,
        "fetched_at": _utcnow_iso(),
        "content_hash": scraper.content_hash(article),
        "raw_text": body,
        "metadata": metadata or {},
    }


def add_article(corpus_path, url, title, source, published="",
                metadata=None, prefetched_html=None, log=None):
    """Fetch a URL, extract its main text, append to the corpus.

    `prefetched_html` lets the caller supply HTML it already has on hand
    (e.g. from WebFetch), bypassing the live fetch — the extraction
    pipeline is the same either way. Returns the content_hash if the
    article was added, or None if it was already in the corpus or
    extraction yielded no text.
    """
    log = log or (lambda *_a: None)
    known = corpus.hashes(corpus_path)
    # Compose a placeholder so we can compute the dedup hash without text.
    candidate_hash = scraper.content_hash(
        {"url": url, "title": title}
    )
    if candidate_hash in known:
        log("skip (already in corpus): %s" % (title or url)[:70])
        return None
    if prefetched_html is not None:
        # Trafilatura wants article-shaped HTML (it looks for an <article>
        # tag, multiple paragraphs, etc.) and returns nothing for a short
        # snippet -- which is exactly the shape WebFetch / an LLM tool
        # often hands us. Fall back to BeautifulSoup's get_text so a
        # well-formed short paragraph isn't silently dropped.
        body = scraper.extract_text_from_html(prefetched_html, fallback="")
        if not body.strip():
            body = scraper.html_to_text(prefetched_html)
    else:
        body = scraper.extract_article(url, fallback="")
    if not body.strip():
        log("skip (no extracted text): %s" % (title or url)[:70])
        return None
    record = _make_record(
        source=source, url=url, title=title, body=body,
        published=published, metadata=metadata,
    )
    corpus.append(corpus_path, record)
    log("corpus += [enrich] %s" % (title or url)[:70])
    return record["content_hash"]


def add_synthesis(corpus_path, entity, body, title=None,
                  source="Claude synthesis", log=None):
    """Append a Claude-written entity overview to the corpus.

    The synthesis is treated as a regular corpus document so NER and the
    relation extractor pick up the entities and stated relationships it
    contains. Its `url` is a stable `claude-synthesis://entity/<hash>`
    URI, which is purely an identifier — it never gets fetched.

    Returns the content_hash if the synthesis was added, or None if an
    identical synthesis is already in the corpus.
    """
    log = log or (lambda *_a: None)
    if not body.strip():
        log("skip synthesis (empty body)")
        return None
    url = _synth_url(entity, body)
    title = title or ("%s — synthesis" % entity)
    known = corpus.hashes(corpus_path)
    candidate_hash = scraper.content_hash({"url": url, "title": title})
    if candidate_hash in known:
        log("skip synthesis (identical body already in corpus)")
        return None
    record = _make_record(
        source=source, url=url, title=title, body=body,
        published=_utcnow_iso()[:10],
        metadata={"entity": entity, "synthesis": True},
    )
    corpus.append(corpus_path, record)
    log("corpus += [synthesis] %s" % title[:70])
    return record["content_hash"]


def enrich_from_plan(corpus_path, plan, log=None, allow_non_ai=False):
    """Execute an enrichment plan: fetch the articles + append the synthesis.

    AI-relevance gate: the plan is checked for AI/ML/autonomy signal in its
    synthesis OR any article. If no signal is found, the whole plan is
    rejected (skipped=count_of_articles, ai_relevant=False) so an off-topic
    enrichment can't quietly land in the AI-focused corpus. Pass
    ``allow_non_ai=True`` (or ``--allow-non-ai`` on the CLI) to bypass.

    Returns {"entity", "articles_added", "articles_skipped",
    "synthesis_added", "ai_relevant", "ai_matched_in", "ai_matched_terms"}.

    A per-article failure is non-fatal — fetch errors are caught so the
    rest of the plan still lands. The corpus stays consistent because
    every record is appended atomically (one JSON line at a time).
    """
    log = log or (lambda *_a: None)
    articles = plan.get("articles") or []

    signal = plan_ai_signal(plan)
    if not signal["ok"] and not allow_non_ai:
        log(
            "AI-relevance gate: REJECTED plan for %r -- no AI/ML/autonomy "
            "term found in synthesis or any article. Pass --allow-non-ai "
            "to override." % plan.get("entity", "?")
        )
        return {
            "entity": plan.get("entity", ""),
            "articles_added": 0,
            "articles_skipped": len(articles),
            "synthesis_added": False,
            "ai_relevant": False,
            "ai_matched_in": "",
            "ai_matched_terms": [],
        }
    if signal["ok"]:
        log("AI-relevance gate: accepted plan for %r (matched in %s: %s)"
            % (plan.get("entity", "?"), signal["matched_in"],
               ", ".join(signal["matched_terms"][:5]) or "(none)"))
    else:
        log("AI-relevance gate: BYPASSED via --allow-non-ai for plan %r"
            % plan.get("entity", "?"))

    added = 0
    skipped = 0
    for article in articles:
        try:
            result = add_article(
                corpus_path,
                url=article["url"],
                title=article.get("title", "") or article["url"],
                source=article.get("source", "Web enrichment"),
                published=article.get("published", ""),
                metadata=article.get("metadata"),
                prefetched_html=article.get("html"),
                log=log,
            )
        except Exception as exc:
            log("WARN article fetch failed (%s): %s"
                % (article.get("url", "?"), exc))
            skipped += 1
            continue
        if result is None:
            skipped += 1
        else:
            added += 1

    synthesis_added = False
    synthesis = plan.get("synthesis")
    if synthesis and synthesis.get("body"):
        result = add_synthesis(
            corpus_path,
            entity=plan["entity"],
            body=synthesis["body"],
            title=synthesis.get("title"),
            source=synthesis.get("source", "Claude synthesis"),
            log=log,
        )
        synthesis_added = result is not None

    return {
        "entity": plan.get("entity", ""),
        "articles_added": added,
        "articles_skipped": skipped,
        "synthesis_added": synthesis_added,
        "ai_relevant": signal["ok"],
        "ai_matched_in": signal["matched_in"],
        "ai_matched_terms": signal["matched_terms"],
    }
