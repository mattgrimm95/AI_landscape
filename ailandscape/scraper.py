"""Step 1 of the flow: scrape articles.

Feeds (RSS/Atom) are parsed with `feedparser`, which tolerates malformed
feeds and the many feed dialects. Each article page is then fetched and its
main text extracted with `trafilatura`, which strips navigation, ads,
captions, and other boilerplate. If an article page cannot be fetched or
extracted, the feed's embedded content is used instead.
"""

import hashlib
import urllib.request

import feedparser
from bs4 import BeautifulSoup

from . import config

# Cap on how many (most recent) entries to keep from a single feed, so one
# large feed cannot dominate the corpus.
MAX_ARTICLES_PER_FEED = 50


class FeedError(Exception):
    """Raised when a feed cannot be fetched."""


def _fetch_url(url, timeout=None):
    timeout = config.HTTP_TIMEOUT if timeout is None else timeout
    req = urllib.request.Request(
        url, headers={"User-Agent": config.HTTP_USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def html_to_text(html):
    """Strip HTML tags and collapse whitespace into a plain-text string."""
    if not html:
        return ""
    return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)


def _entry_body_html(entry):
    """Return the richest body HTML available for a feed entry."""
    content = entry.get("content")
    if content:
        value = content[0].get("value", "")
        if value:
            return value
    return entry.get("summary", "") or entry.get("description", "")


def parse_feed(raw, source_name):
    """Parse RSS/Atom feed content into a list of article dicts.

    `raw` may be feed bytes or text. Malformed feeds are tolerated —
    feedparser returns whatever entries it can recover.
    """
    parsed = feedparser.parse(raw)
    articles = []
    for entry in parsed.entries:
        article = {
            "source": source_name,
            "url": (entry.get("link") or "").strip(),
            "title": (entry.get("title") or "").strip(),
            "published": (
                entry.get("published")
                or entry.get("updated")
                or entry.get("created")
                or ""
            ).strip(),
            "raw_text": html_to_text(_entry_body_html(entry)),
        }
        if article["title"] or article["raw_text"]:
            articles.append(article)
    return articles


def content_hash(article):
    """Stable id for an article, derived from its URL and title.

    The body text is deliberately excluded so a document can be de-duplicated
    (and skipped) before its page is fetched.
    """
    payload = "|".join([article.get("url", ""), article.get("title", "")])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fetch_feed(feed):
    """Fetch and parse a live feed. `feed` is a dict with 'name' and 'url'.

    Only the most recent MAX_ARTICLES_PER_FEED entries are kept so a single
    large feed cannot dominate the corpus.
    """
    try:
        raw = _fetch_url(feed["url"])
    except Exception as exc:  # network/HTTP errors vary widely
        raise FeedError("could not fetch %s: %s" % (feed["url"], exc)) from exc
    return parse_feed(raw, feed["name"])[:MAX_ARTICLES_PER_FEED]


def extract_text_from_html(html, fallback=""):
    """Extract an article's main text from page HTML using trafilatura.

    trafilatura strips navigation, ads, captions, and other boilerplate.
    Returns `fallback` if trafilatura is unavailable or finds no usable text.
    """
    if not html:
        return fallback
    try:
        import trafilatura
    except ImportError:
        return fallback
    try:
        text = trafilatura.extract(
            html, include_comments=False, favor_precision=True
        )
    except Exception:
        return fallback
    return text or fallback


def extract_article(url, fallback=""):
    """Fetch an article page and return its clean main text.

    Falls back to `fallback` (typically the feed's embedded content) if the
    page cannot be fetched or no main text can be extracted.
    """
    if not url:
        return fallback
    try:
        html = _fetch_url(url)
    except Exception:  # network/HTTP errors vary widely
        return fallback
    return extract_text_from_html(html, fallback=fallback)


def scrape_fixture(path, source_name):
    """Parse a feed from a local file (used for deterministic tests)."""
    from pathlib import Path

    raw = Path(path).read_bytes()
    return parse_feed(raw, source_name)
