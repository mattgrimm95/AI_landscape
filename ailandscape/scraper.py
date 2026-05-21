"""Step 1 of the flow: scrape web pages from RSS/Atom feeds.

Parsing uses only the standard library (`urllib`, `xml.etree`) plus
BeautifulSoup for turning embedded HTML into plain text.
"""

import hashlib
import urllib.request
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup

from . import config


class FeedError(Exception):
    """Raised when a feed cannot be fetched or parsed."""


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


def _strip_namespaces(root):
    """Drop XML namespaces so RSS and Atom tags can be found by local name."""
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _text_of(parent, *tag_names):
    """Return the text of the first child matching any of tag_names."""
    for name in tag_names:
        el = parent.find(name)
        if el is not None and el.text and el.text.strip():
            return el.text.strip()
    return ""


def _link_of(item):
    """Extract a link from an RSS <link>text</link> or Atom <link href=...>."""
    el = item.find("link")
    if el is not None:
        if el.text and el.text.strip():
            return el.text.strip()
        href = el.get("href")
        if href:
            return href.strip()
    return ""


def parse_feed(raw, source_name):
    """Parse RSS 2.0 or Atom feed content into a list of article dicts."""
    # ElementTree rejects str input carrying an XML encoding declaration,
    # so always hand it bytes.
    data = raw if isinstance(raw, bytes) else raw.encode("utf-8")
    try:
        root = _strip_namespaces(ET.fromstring(data))
    except ET.ParseError as exc:
        raise FeedError("could not parse feed XML: %s" % exc) from exc

    items = root.findall(".//item") or root.findall(".//entry")
    articles = []
    for item in items:
        title = _text_of(item, "title")
        body_html = _text_of(item, "encoded", "content", "description", "summary")
        article = {
            "source": source_name,
            "url": _link_of(item),
            "title": title,
            "published": _text_of(item, "pubDate", "published", "updated", "date"),
            "raw_text": html_to_text(body_html),
        }
        if article["title"] or article["raw_text"]:
            articles.append(article)
    return articles


def content_hash(article):
    """Stable hash identifying an article, used for de-duplication."""
    payload = "|".join(
        [article.get("url", ""), article.get("title", ""), article.get("raw_text", "")]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fetch_feed(feed):
    """Fetch and parse a live feed. `feed` is a dict with 'name' and 'url'."""
    try:
        raw = _fetch_url(feed["url"])
    except Exception as exc:  # network/HTTP errors vary widely
        raise FeedError("could not fetch %s: %s" % (feed["url"], exc)) from exc
    return parse_feed(raw, feed["name"])


def scrape_fixture(path, source_name):
    """Parse a feed from a local file (used for deterministic tests)."""
    from pathlib import Path

    raw = Path(path).read_bytes()
    return parse_feed(raw, source_name)
