"""A statistical overview of the pipeline's data.

A quick, readable read on the pipeline funnel, scrape recency, entity and
relationship breakdowns, the most prominent and most-connected entities, and
data-quality signals — meant to guide improvements toward a navigable visual
knowledge graph.

`build_overview` computes the metrics (a plain dict, easy to test);
`render_overview` formats them into a human-readable report.
"""

import collections
import datetime
import json
import pathlib


def _read_last_run(run_history_path):
    """Return the most recent run-history record, or None."""
    if not run_history_path:
        return None
    path = pathlib.Path(run_history_path)
    if not path.exists():
        return None
    last = None
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                last = line.strip()
    try:
        return json.loads(last) if last else None
    except json.JSONDecodeError:
        return None


def _hours_since(iso_timestamp):
    """Hours between an ISO-8601 timestamp and now (UTC), or None."""
    if not iso_timestamp:
        return None
    try:
        when = datetime.datetime.fromisoformat(iso_timestamp)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    return (now - when).total_seconds() / 3600.0


def _counter_rows(counter, total):
    return [
        (name, count, (100.0 * count / total) if total else 0.0)
        for name, count in counter.most_common()
    ]


def _possible_partial_duplicates(nodes):
    """Single-word nodes whose word is the last word of a multi-word node of
    the same type — likely the same entity referred to by a partial name."""
    last_word = {}  # (type, last word) -> full canonical name
    for node in nodes:
        parts = node["canonical_name"].split()
        if len(parts) >= 2:
            last_word.setdefault((node["type"], parts[-1].lower()), node["canonical_name"])
    dups = []
    for node in nodes:
        parts = node["canonical_name"].split()
        if len(parts) == 1:
            full = last_word.get((node["type"], parts[0].lower()))
            if full and full.lower() != node["canonical_name"].lower():
                dups.append((node["canonical_name"], full))
    return dups


def build_overview(documents, ner_log, kg_store, run_history_path=None):
    """Compute overview metrics from the corpus, NER log, and graph."""
    nodes = kg_store.nodes()
    edges = kg_store.edges()
    doc_count = len(documents)
    raw_mentions = ner_log.count_entities()
    node_count = len(nodes)
    edge_count = len(edges)

    funnel = {
        "documents": doc_count,
        "raw_mentions": raw_mentions,
        "nodes": node_count,
        "edges": edge_count,
        "mentions_per_doc": (raw_mentions / doc_count) if doc_count else 0.0,
        "mentions_per_node": (raw_mentions / node_count) if node_count else 0.0,
    }

    last_fetch = max((d.get("fetched_at", "") for d in documents), default="")
    hours = _hours_since(last_fetch)
    scrape = {
        "last_fetch": last_fetch or None,
        "hours_since": hours,
        "within_24h": hours is not None and hours <= 24,
        "last_run": _read_last_run(run_history_path),
    }

    degree = collections.Counter()
    for edge in edges:
        degree[edge["src_id"]] += 1
        degree[edge["dst_id"]] += 1
    node_by_id = {n["id"]: n for n in nodes}
    most_connected = [
        (node_by_id[nid]["canonical_name"], node_by_id[nid]["type"], deg)
        for nid, deg in degree.most_common(10)
        if nid in node_by_id
    ]

    singletons = sum(1 for n in nodes if n["mention_count"] <= 1)
    dups = _possible_partial_duplicates(nodes)

    return {
        "funnel": funnel,
        "scrape": scrape,
        "entity_types": _counter_rows(
            collections.Counter(n["type"] for n in nodes), node_count
        ),
        "relation_types": _counter_rows(
            collections.Counter(e["relation"] for e in edges), edge_count
        ),
        "top_by_mentions": sorted(
            nodes, key=lambda n: n["mention_count"], reverse=True
        )[:10],
        "most_connected": most_connected,
        "quality": {
            "singletons": singletons,
            "singleton_pct": (100.0 * singletons / node_count) if node_count else 0.0,
            "partial_name_dups": len(dups),
            "examples": dups[:5],
        },
    }


def _int(value):
    return format(int(value), ",d")


def _ago(hours):
    if hours < 1:
        return "%d minutes ago" % max(1, int(hours * 60))
    if hours < 48:
        return "%.1f hours ago" % hours
    return "%.1f days ago" % (hours / 24.0)


def _duration(seconds):
    seconds = int(round(seconds))
    if seconds < 60:
        return "%ds" % seconds
    return "%dm %02ds" % (seconds // 60, seconds % 60)


def render_overview(data):
    """Format the overview dict into a human-readable report string."""
    bar = "=" * 60
    out = [bar, "  AI LANDSCAPE - DATA OVERVIEW", bar]

    f = data["funnel"]
    out += [
        "",
        "PIPELINE FUNNEL",
        "  %-24s %12s" % ("Articles scraped", _int(f["documents"])),
        "  %-24s %12s   (%.1f per article)"
        % ("Raw NER mentions", _int(f["raw_mentions"]), f["mentions_per_doc"]),
        "  %-24s %12s   (%.1f mentions per node)"
        % ("Knowledge-graph nodes", _int(f["nodes"]), f["mentions_per_node"]),
        "  %-24s %12s" % ("Relationships (edges)", _int(f["edges"])),
    ]

    s = data["scrape"]
    out += ["", "SCRAPE STATUS"]
    if s["hours_since"] is None:
        out.append("  %-24s %s" % ("Last article fetched", "unknown (empty corpus)"))
    else:
        out.append("  %-24s %s" % ("Last article fetched", _ago(s["hours_since"])))
    out.append("  %-24s %s" % ("Scraped in past 24h?", "YES" if s["within_24h"] else "NO"))
    run = s["last_run"]
    if run:
        out.append(
            "  %-24s %s" % ("Last full run", run["finished_at"][:19].replace("T", " "))
        )
        out.append(
            "  %-24s scrape %s, rebuild %s"
            % ("  duration", _duration(run["scrape_seconds"]), _duration(run["rebuild_seconds"]))
        )
        out.append(
            "  %-24s %s new of %s fetched"
            % ("  articles added", _int(run["added"]), _int(run["fetched"]))
        )
    else:
        out.append("  %-24s %s" % ("Last full run", "not recorded yet"))

    out += ["", "ENTITY TYPES (%s nodes)" % _int(f["nodes"])]
    for name, count, pct in data["entity_types"]:
        out.append("  %-16s %10s   %5.1f%%" % (name, _int(count), pct))

    out += ["", "RELATIONSHIP TYPES (%s edges)" % _int(f["edges"])]
    for name, count, pct in data["relation_types"]:
        out.append("  %-16s %10s   %5.1f%%" % (name, _int(count), pct))

    out += ["", "MOST PROMINENT ENTITIES (by mentions)"]
    for i, n in enumerate(data["top_by_mentions"], 1):
        out.append(
            "  %2d. %-26s %-13s %6s mentions / %s docs"
            % (i, n["canonical_name"][:26], n["type"],
               _int(n["mention_count"]), _int(n["document_count"]))
        )

    out += ["", "MOST CONNECTED ENTITIES (by relationships)"]
    for i, (name, etype, deg) in enumerate(data["most_connected"], 1):
        out.append(
            "  %2d. %-26s %-13s %6s links" % (i, name[:26], etype, _int(deg))
        )

    q = data["quality"]
    out += ["", "DATA QUALITY (targets for improvement)"]
    out.append(
        "  %-24s %10s   (%.1f%% of nodes) - likely noise or fragments"
        % ("Single-mention nodes", _int(q["singletons"]), q["singleton_pct"])
    )
    out.append(
        "  %-24s %10s   single-word names that match a fuller name"
        % ("Partial-name duplicates", _int(q["partial_name_dups"]))
    )
    for short, full in q["examples"]:
        out.append('        e.g. "%s" may be the same as "%s"' % (short, full))

    out.append(bar)
    return "\n".join(out)
