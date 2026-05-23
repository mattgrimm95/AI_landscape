"""Discover new RSS/Atom feeds for the AI national-security landscape.

Two complementary pieces:

  * `verify_feed(url)` — fetches a URL and returns a small status record
    saying whether the response is a parseable RSS/Atom feed and how many
    entries it contains. Used both to gate candidates from
    `discover_candidates` before they're suggested to the user and to
    health-check the existing `feeds.FEEDS` list.

  * `discover_candidates(seeds)` — for each seed organization or topic,
    probes a small set of standard RSS URL templates (`/feed/`, `/rss/`,
    `/feed`, `/topic/<seed>/feed`, …) under the seed's likely homepage.
    No external search API is required — these templates cover most
    WordPress, Drupal, and custom CMSes that publish a national-security
    AI blog or news section.

The module is opinionated about scope: candidates that yield zero entries
or that fail to parse as a feed are dropped. Verified candidates can be
added to `feeds.FEEDS` manually (the file is version-controlled and the
list is curated by hand, so we don't auto-mutate it).
"""

import urllib.error
import urllib.parse
import urllib.request

import feedparser

from . import config


# Standard RSS/Atom URL templates that most CMSes expose. `{base}` is the
# seed's homepage (e.g. https://example.org), `{topic}` is a topic slug
# the seed cares about (e.g. "artificial-intelligence").
_FEED_TEMPLATES_NO_TOPIC = (
    "{base}/feed/",
    "{base}/feed",
    "{base}/rss/",
    "{base}/rss",
    "{base}/rss.xml",
    "{base}/atom.xml",
    "{base}/index.xml",
    "{base}/feeds/all.atom.xml",
)
_FEED_TEMPLATES_WITH_TOPIC = (
    "{base}/topic/{topic}/feed/",
    "{base}/topic/{topic}/feed",
    "{base}/topic/{topic}.rss",
    "{base}/topics/{topic}/feed/",
    "{base}/tag/{topic}/feed/",
    "{base}/category/{topic}/feed/",
    "{base}/feeds/topic/{topic}.rss",
    "{base}/rss/topic/{topic}",
)


def _http_get(url, timeout=None):
    """Fetch a URL with the project's User-Agent. Returns the body bytes."""
    timeout = config.HTTP_TIMEOUT if timeout is None else timeout
    req = urllib.request.Request(
        url, headers={"User-Agent": config.HTTP_USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def verify_feed(url):
    """Verify that `url` returns a parseable RSS/Atom feed.

    Returns {"ok", "entries", "title", "error"}: `ok` is True only when
    the response parses as a feed and contains at least one entry. The
    health check is liberal — a feed with one entry today still counts.
    """
    try:
        body = _http_get(url)
    except (urllib.error.URLError, ValueError, TimeoutError) as exc:
        return {"ok": False, "entries": 0, "title": "", "error": str(exc)}
    parsed = feedparser.parse(body)
    entries = len(parsed.entries)
    title = (parsed.feed.get("title", "") or "").strip()
    if entries == 0:
        return {"ok": False, "entries": 0, "title": title,
                "error": "no entries"}
    return {"ok": True, "entries": entries, "title": title, "error": ""}


def discover_candidates(seeds):
    """Probe standard RSS URL templates for each seed.

    `seeds` is a list of dicts: {"name", "homepage", "topics"}. For each
    seed we try the no-topic templates against the homepage, then the
    with-topic templates for every topic. Any URL that verifies as a feed
    with at least one entry is returned.

    Returns a list of dicts: {"name", "url", "entries", "title"}, one per
    verified candidate. A seed that yields zero verified candidates does
    NOT appear in the result — the caller can compare the input and output
    lengths to see what fell through.
    """
    verified = []
    for seed in seeds:
        base = seed["homepage"].rstrip("/")
        topics = seed.get("topics") or [""]
        seen_urls = set()
        for template in _FEED_TEMPLATES_NO_TOPIC:
            url = template.format(base=base)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            status = verify_feed(url)
            if status["ok"]:
                verified.append({
                    "name": seed["name"], "url": url,
                    "entries": status["entries"],
                    "title": status["title"],
                })
                break  # one good URL per seed is enough
        else:
            # No no-topic feed worked — try the topic-scoped templates.
            for topic in topics:
                if not topic:
                    continue
                topic_slug = urllib.parse.quote(topic)
                for template in _FEED_TEMPLATES_WITH_TOPIC:
                    url = template.format(base=base, topic=topic_slug)
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    status = verify_feed(url)
                    if status["ok"]:
                        verified.append({
                            "name": "%s — %s" % (seed["name"], topic),
                            "url": url,
                            "entries": status["entries"],
                            "title": status["title"],
                        })
                        break
                else:
                    continue
                break
    return verified


# Curated seed list of national-security + AI-relevant organizations that
# don't (yet) have a feed in `feeds.FEEDS`. These are the seeds the
# `discover-feeds` CLI command probes; add more as new organizations
# become relevant. Homepages, not feed URLs — the templates above will
# probe candidates against each homepage.
DEFAULT_SEEDS = [
    {"name": "RAND", "homepage": "https://www.rand.org",
     "topics": ["artificial-intelligence", "national-security"]},
    {"name": "Brookings", "homepage": "https://www.brookings.edu",
     "topics": ["artificial-intelligence", "defense-strategy"]},
    {"name": "CNAS", "homepage": "https://www.cnas.org",
     "topics": ["artificial-intelligence", "technology-and-national-security"]},
    {"name": "Atlantic Council", "homepage": "https://www.atlanticcouncil.org",
     "topics": ["artificial-intelligence", "defense"]},
    {"name": "Stimson Center", "homepage": "https://www.stimson.org",
     "topics": ["artificial-intelligence"]},
    {"name": "Lawfare", "homepage": "https://www.lawfaremedia.org",
     "topics": ["artificial-intelligence", "national-security"]},
    {"name": "Stanford HAI", "homepage": "https://hai.stanford.edu",
     "topics": ["news", "research"]},
    {"name": "DARPA", "homepage": "https://www.darpa.mil",
     "topics": ["news"]},
    {"name": "Anthropic", "homepage": "https://www.anthropic.com",
     "topics": ["news"]},
    {"name": "Microsoft AI Blog", "homepage": "https://blogs.microsoft.com/ai",
     "topics": []},
    {"name": "Meta AI", "homepage": "https://ai.meta.com",
     "topics": ["blog"]},
    {"name": "NVIDIA Blog", "homepage": "https://blogs.nvidia.com",
     "topics": []},
    {"name": "Palantir Blog", "homepage": "https://blog.palantir.com",
     "topics": []},
    {"name": "Anduril Blog", "homepage": "https://www.anduril.com",
     "topics": ["news"]},
]


def health_check_existing(feeds):
    """Verify every URL in `feeds` — caller-supplied (e.g. feeds.FEEDS).

    Returns two lists: [healthy] and [unhealthy], each a dict with the
    original feed record plus a `status` from `verify_feed`. Useful as a
    pre-flight before a scrape to surface feeds that have rotted.
    """
    healthy = []
    unhealthy = []
    for feed in feeds:
        status = verify_feed(feed["url"])
        record = dict(feed)
        record["status"] = status
        (healthy if status["ok"] else unhealthy).append(record)
    return healthy, unhealthy
