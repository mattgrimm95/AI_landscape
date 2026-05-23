"""Temporal signals over the knowledge graph.

What is new and what is active: document volume by month, the entities that
appeared most recently, and the entities mentioned most recently. Node dates
come from publication (see corpus.published_date and reconcile), so these
signals reflect when news happened, not when it was scraped.

`build_trends` computes the signals (a plain dict, easy to test);
`render_trends` formats them as a human-readable report.
"""

import collections
import datetime

from . import corpus


def _node_brief(node):
    return {
        "name": node["canonical_name"],
        "type": node["type"],
        "mentions": node["mention_count"],
        "first_seen": node["first_seen"],
        "last_seen": node["last_seen"],
    }


def build_trends(documents, kg_store):
    """Compute temporal signals from the corpus documents and the graph."""
    nodes = kg_store.nodes()

    month_counts = collections.Counter()
    for doc in documents:
        date = corpus.published_date(doc)
        if date:
            month_counts[date[:7]] += 1  # YYYY-MM

    newest = sorted(
        (n for n in nodes if n["first_seen"]),
        key=lambda n: n["first_seen"],
        reverse=True,
    )
    active = sorted(
        (n for n in nodes if n["last_seen"]),
        key=lambda n: (n["last_seen"], n["mention_count"]),
        reverse=True,
    )
    return {
        "document_volume": [
            {"month": month, "count": count}
            for month, count in sorted(month_counts.items())
        ],
        "new_entities": [_node_brief(n) for n in newest[:15]],
        "recent_entities": [_node_brief(n) for n in active[:15]],
    }


def build_recent(documents, kg_store, since=None, days=7, limit=15):
    """Return the slice of the landscape that's new since `since` (or `days`).

    `since` is a date string (YYYY-MM-DD); if absent, `days` ago is used.
    The returned dict has three lists:
      * documents — published or fetched on/after the cutoff, newest first
      * new_entities — nodes whose first_seen is on/after the cutoff
      * active_entities — nodes whose last_seen is on/after the cutoff
    The aim is to power a "what changed since you were last here?" surface.
    """
    if since:
        try:
            cutoff = datetime.date.fromisoformat(since[:10])
        except ValueError:
            cutoff = datetime.date.today() - datetime.timedelta(days=days)
    else:
        cutoff = datetime.date.today() - datetime.timedelta(days=days)
    cutoff_iso = cutoff.isoformat()

    nodes = kg_store.nodes()
    new_nodes = [
        n for n in nodes if (n["first_seen"] or "") >= cutoff_iso
    ]
    new_nodes.sort(key=lambda n: n["first_seen"], reverse=True)
    active_nodes = [
        n for n in nodes if (n["last_seen"] or "") >= cutoff_iso
    ]
    active_nodes.sort(
        key=lambda n: (n["last_seen"], n["mention_count"]), reverse=True
    )

    recent_docs = []
    for doc in documents:
        # Prefer the published date; fall back to fetched_at if absent so a
        # just-scraped article without a parseable published date still shows.
        date = corpus.published_date(doc) or (doc.get("fetched_at") or "")[:10]
        if date and date >= cutoff_iso:
            recent_docs.append((date, doc))
    recent_docs.sort(key=lambda pair: pair[0], reverse=True)

    return {
        "since": cutoff_iso,
        "documents": [
            {
                "title": d.get("title", ""),
                "source": d.get("source", ""),
                "url": d.get("url", ""),
                "published": d.get("published", ""),
                "date": date,
                "content_hash": d.get("content_hash", ""),
            }
            for date, d in recent_docs[:limit]
        ],
        "document_total": len(recent_docs),
        "new_entities": [_node_brief(n) for n in new_nodes[:limit]],
        "new_entity_total": len(new_nodes),
        "active_entities": [_node_brief(n) for n in active_nodes[:limit]],
        "active_entity_total": len(active_nodes),
    }


def render_trends(data):
    """Format the trends dict into a human-readable report string."""
    bar = "=" * 60
    out = [bar, "  AI LANDSCAPE - TRENDS", bar]

    out += ["", "DOCUMENT VOLUME BY MONTH"]
    volume = data["document_volume"]
    peak = max((v["count"] for v in volume), default=1)
    for v in volume:
        blocks = "#" * max(1, round(40 * v["count"] / peak))
        out.append("  %-9s %5d  %s" % (v["month"], v["count"], blocks))
    if not volume:
        out.append("  (no dated documents)")

    out += ["", "NEWLY APPEARED ENTITIES"]
    for n in data["new_entities"]:
        out.append(
            "  %-28s %-12s first seen %s"
            % (n["name"][:28], n["type"], n["first_seen"])
        )

    out += ["", "MOST RECENTLY ACTIVE ENTITIES"]
    for n in data["recent_entities"]:
        out.append(
            "  %-28s %-12s last seen %s"
            % (n["name"][:28], n["type"], n["last_seen"])
        )

    out.append(bar)
    return "\n".join(out)
