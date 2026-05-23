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
import json

from . import corpus


def _node_brief(node):
    return {
        "id": node["id"],
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


def build_spikes(documents, kg_store, recent_days=30, min_recent=5,
                  spike_ratio=3.0, min_active_days=60, limit=20):
    """Entities whose recent mention rate is sharply above their baseline.

    For each node with corpus presence, compare mentions in the last
    `recent_days` days against the long-term average over the same window
    length across the node's full active span. A node is "spiking" when:

      * recent >= ``min_recent`` (small absolute floor — no flagging a
        node that went from 0/month to 1/month as "spiking"), AND
      * recent >= baseline × ``spike_ratio`` (large relative jump), AND
      * the entity has been around at least ``min_active_days`` days
        (otherwise it's a NEW entity, surfaced in "newly appeared," not
        a spike — and the ratio math is artificially inflated for any
        node whose first appearance is inside the recent window).

    The misc entity type is excluded entirely — those are mostly common
    capitalized words that NER picked up (First, However, Instead, ...)
    and would dominate any spike list. Typed entities (person /
    organization / place / product / concept) are what a learner wants
    surfaced.

    Returned sorted by relative jump so the top of the list is the
    most-newsworthy spike. The frontend uses the returned ids as a set
    to add a small "↑" badge to entity rows site-wide; no node-level
    schema change required.
    """
    today = datetime.date.today()
    recent_start = today - datetime.timedelta(days=recent_days)
    nodes = kg_store.nodes()
    # Misc-typed nodes are mostly noise capitalized words that NER picked
    # up at sentence start. They would dominate the spike list otherwise.
    nodes = [n for n in nodes if n["type"] != "misc"]
    by_id = {n["id"]: n for n in nodes}
    # Build a per-node document-date list once.
    docs_by_node = {n["id"]: [] for n in nodes}
    by_hash = {d.get("content_hash"): d for d in documents}
    for node in nodes:
        for h in kg_store.documents_for_node(node["id"]):
            doc = by_hash.get(h)
            if not doc:
                continue
            date_str = corpus.published_date(doc)
            if not date_str:
                continue
            try:
                date = datetime.date.fromisoformat(date_str)
            except ValueError:
                continue
            if date > today:
                continue
            docs_by_node[node["id"]].append(date)

    spikes = []
    for node_id, dates in docs_by_node.items():
        if not dates:
            continue
        recent = sum(1 for d in dates if d >= recent_start)
        if recent < min_recent:
            continue
        first = min(dates)
        active_days = (today - first).days
        if active_days < min_active_days:
            # Genuinely new entity — show up via "newly appeared," not here.
            continue
        baseline = len(dates) * recent_days / active_days
        if baseline <= 0:
            continue
        ratio = recent / baseline
        if ratio < spike_ratio:
            continue
        node = by_id[node_id]
        spikes.append({
            "id": node_id,
            "name": node["canonical_name"],
            "type": node["type"],
            "recent": recent,
            "baseline": round(baseline, 2),
            "ratio": round(ratio, 2),
            "first_seen": node.get("first_seen") or "",
        })
    spikes.sort(key=lambda s: (-s["ratio"], -s["recent"]))
    return spikes[:limit]


def build_trajectory(documents, kg_store, months=12):
    """Corpus-wide month-by-month trajectory.

    Returns ``months`` worth of recent activity buckets so the user can see
    the landscape evolving rather than a flat snapshot. Each bucket carries:

      * ``month`` — "YYYY-MM"
      * ``documents`` — articles published that month
      * ``new_entities`` — count + a handful of example names of nodes
        whose first_seen falls in that month
      * ``typed_relations`` — typed edges whose evidence sentence appears
        in a document published that month (approximated by edge source
        document, when present in metadata)
      * ``entity_type_counts`` — count of new entities by type (for the
        stacked-area renderer)

    Designed for the Trajectory modal — a "many months at a glance" view
    the current Trends modal doesn't offer (it shows total volume but no
    per-month entity-type breakdown).
    """
    today = datetime.date.today()
    # Months are sorted ascending; the most-recent `months` are returned.
    month_keys = []
    cursor = datetime.date(today.year, today.month, 1)
    for _ in range(months):
        month_keys.append(cursor.isoformat()[:7])
        # Walk back one month at a time.
        if cursor.month == 1:
            cursor = datetime.date(cursor.year - 1, 12, 1)
        else:
            cursor = datetime.date(cursor.year, cursor.month - 1, 1)
    month_keys = list(reversed(month_keys))
    keys_set = set(month_keys)

    buckets = {
        m: {
            "month": m, "documents": 0,
            "new_entities": 0, "typed_relations": 0,
            "new_entity_names": [],
            "entity_type_counts": collections.Counter(),
        }
        for m in month_keys
    }

    for doc in documents:
        date = corpus.published_date(doc)
        if not date:
            continue
        key = date[:7]
        if key in buckets:
            buckets[key]["documents"] += 1

    nodes = kg_store.nodes()
    for node in nodes:
        first = (node.get("first_seen") or "")[:7]
        if first in buckets:
            b = buckets[first]
            b["new_entities"] += 1
            b["entity_type_counts"][node["type"]] += 1
            if len(b["new_entity_names"]) < 3:
                b["new_entity_names"].append(node["canonical_name"])

    edges = kg_store.edges()
    by_hash_month = {}
    for doc in documents:
        h = doc.get("content_hash")
        date = corpus.published_date(doc)
        if h and date:
            by_hash_month[h] = date[:7]
    for edge in edges:
        if edge["relation"] == "co_occurs_with":
            continue
        meta = _edge_meta(edge)
        source_hash = meta.get("source")
        if not source_hash:
            continue
        month = by_hash_month.get(source_hash)
        if month and month in buckets:
            buckets[month]["typed_relations"] += 1

    return {
        "months": [
            {
                "month": m,
                "documents": buckets[m]["documents"],
                "new_entities": buckets[m]["new_entities"],
                "typed_relations": buckets[m]["typed_relations"],
                "new_entity_names": buckets[m]["new_entity_names"],
                "entity_type_counts": dict(buckets[m]["entity_type_counts"]),
            }
            for m in month_keys
        ],
    }


def _edge_meta(edge):
    raw = edge.get("metadata")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {}


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
