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

from . import corpus as corpus_mod


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


def _bucketize(values, bounds):
    """Count values into ordered buckets.

    `bounds` is a list of (upper_inclusive, label); an upper of None is the
    catch-all final bucket. Returns a list of (label, count).
    """
    counts = [0] * len(bounds)
    for value in values:
        for i, (upper, _label) in enumerate(bounds):
            if upper is None or value <= upper:
                counts[i] += 1
                break
    return [(label, counts[i]) for i, (_upper, label) in enumerate(bounds)]


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
    isolated = sum(1 for n in nodes if degree.get(n["id"], 0) == 0)
    dups = _possible_partial_duplicates(nodes)
    pct = lambda count: (100.0 * count / node_count) if node_count else 0.0

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
        "distributions": {
            "mentions": _bucketize(
                (n["mention_count"] for n in nodes),
                [(1, "1 (one-off)"), (5, "2-5"), (20, "6-20"), (None, "21+")],
            ),
            "edge_weight": _bucketize(
                (e["weight"] for e in edges),
                [(1, "1 (weak)"), (4, "2-4"), (9, "5-9"), (None, "10+")],
            ),
        },
        "quality": {
            "singletons": singletons,
            "singleton_pct": pct(singletons),
            "isolated": isolated,
            "isolated_pct": pct(isolated),
            "partial_name_dups": len(dups),
            "examples": dups[:5],
        },
        "dates": _date_quality(documents),
        "feeds": _feed_health(documents, run_history_path),
        "signals": _signal_coverage(documents, ner_log, kg_store),
        "reading": _reading_stats(documents),
    }


def _date_quality(documents):
    """Per-source published-date parse coverage.

    Surfaces feeds that ship dates in formats `corpus.published_date_status`
    can't read (a silent data-quality leak today — `reconcile` then falls back
    to `fetched_at`, conflating "we don't know" with "published today").
    Returns a dict of total/parsed/missing/unparseable counts, plus a list of
    per-source rows sorted by unparseable rate.
    """
    by_source = {}
    for doc in documents:
        source = doc.get("source") or "(unknown)"
        bucket = by_source.setdefault(
            source, {"total": 0, "parsed": 0, "missing": 0, "unparseable": 0}
        )
        _date, status = corpus_mod.published_date_status(doc)
        bucket["total"] += 1
        bucket[status] = bucket.get(status, 0) + 1
    rows = []
    for source, b in by_source.items():
        unparseable_pct = (100.0 * b["unparseable"] / b["total"]) if b["total"] else 0.0
        rows.append({
            "source": source,
            "total": b["total"],
            "parsed": b["parsed"],
            "missing": b["missing"],
            "unparseable": b["unparseable"],
            "unparseable_pct": unparseable_pct,
        })
    rows.sort(key=lambda r: (-r["unparseable_pct"], r["source"]))
    totals = {
        "total": sum(b["total"] for b in by_source.values()),
        "parsed": sum(b["parsed"] for b in by_source.values()),
        "missing": sum(b["missing"] for b in by_source.values()),
        "unparseable": sum(b["unparseable"] for b in by_source.values()),
    }
    totals["parsed_pct"] = (
        100.0 * totals["parsed"] / totals["total"] if totals["total"] else 0.0
    )
    # The "concerning" set is what a reader actually needs to act on.
    concerning = [r for r in rows if r["unparseable"] > 0]
    return {"totals": totals, "by_source": rows, "concerning": concerning}


def _feed_health(documents, run_history_path):
    """Per-feed scorecard derived from the corpus + run history.

    For each source name appearing in the corpus, compute:
      * `documents`              — total documents that source contributed
      * `last_fetched_at`        — most recent fetched_at for that source
      * `hours_since_fetched`    — hours since the most recent fetch
      * `recent_runs_with_adds`  — over the last 14 run-history entries, how
                                   many added new documents from this source

    A feed with `recent_runs_with_adds == 0` and `hours_since_fetched > 14*24`
    is likely silently broken — its URL stopped returning new entries weeks
    ago and nothing surfaced the problem. Today this is invisible in the
    overview; making it explicit is the point.
    """
    per_source = {}
    for doc in documents:
        source = doc.get("source") or "(unknown)"
        bucket = per_source.setdefault(
            source,
            {"documents": 0, "last_fetched_at": "", "added_recent": 0},
        )
        bucket["documents"] += 1
        fetched = doc.get("fetched_at") or ""
        if fetched > bucket["last_fetched_at"]:
            bucket["last_fetched_at"] = fetched

    recent_runs = _read_recent_runs(run_history_path, limit=14)
    runs_with_adds = collections.Counter()
    for run in recent_runs:
        for source, stats in (run.get("feeds") or {}).items():
            if stats and (stats.get("added") or 0) > 0:
                runs_with_adds[source] += 1

    rows = []
    for source, b in per_source.items():
        hours = _hours_since(b["last_fetched_at"]) if b["last_fetched_at"] else None
        rows.append({
            "source": source,
            "documents": b["documents"],
            "last_fetched_at": b["last_fetched_at"] or None,
            "hours_since_fetched": hours,
            "recent_runs_with_adds": runs_with_adds.get(source, 0),
            "recent_runs_total": len(recent_runs),
        })
    rows.sort(key=lambda r: (
        -(r["hours_since_fetched"] or 0),
        r["source"],
    ))
    # Stale = no new doc from this source in the last 14 days AND no run
    # in the recent history showed an add. Either signal alone could just be
    # "nothing newsworthy"; both at once means the feed is silently dead.
    stale = [
        r for r in rows
        if (r["hours_since_fetched"] is None
            or r["hours_since_fetched"] > 14 * 24)
        and r["recent_runs_with_adds"] == 0
    ]
    return {"by_source": rows, "stale": stale}


def _signal_coverage(documents, ner_log, kg_store):
    """How many corpus documents produced *any* recognized signal.

    Off-topic articles and silent extraction failures both look the same in
    the corpus — a document with no entity hits and no edges contributed.
    Surfacing the count + a few examples turns those silent failures into a
    follow-up list. Trafilatura sometimes returns a page's nav-skeleton (no
    real body) and trafilatura's HTTP layer sometimes gets blocked entirely;
    both leave the same fingerprint.
    """
    no_entities = []
    short_body = []
    for doc in documents:
        chash = doc.get("content_hash")
        ents = ner_log.entities_for(chash) if chash else []
        body_len = len(doc.get("raw_text") or "")
        if not ents:
            no_entities.append({
                "source": doc.get("source") or "(unknown)",
                "title": (doc.get("title") or "")[:80],
                "url": doc.get("url") or "",
                "body_chars": body_len,
            })
        if body_len < 300 and body_len > 0:
            short_body.append({
                "source": doc.get("source") or "(unknown)",
                "title": (doc.get("title") or "")[:80],
                "url": doc.get("url") or "",
                "body_chars": body_len,
            })
    return {
        "documents": len(documents),
        "no_entities": len(no_entities),
        "short_body": len(short_body),
        "examples_no_entities": no_entities[:5],
        "examples_short_body": short_body[:5],
    }


def _read_recent_runs(run_history_path, limit=14):
    """Return up to `limit` most recent records from the run-history log."""
    if not run_history_path:
        return []
    path = pathlib.Path(run_history_path)
    if not path.exists():
        return []
    lines = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                lines.append(line)
    records = []
    for line in lines[-limit:]:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def diff_runs(run_history_path):
    """Compute KPI deltas between the two most recent runs.

    Returns ``None`` if there aren't at least two runs to compare. Otherwise
    a dict shaped ``{prev, curr, deltas}`` where ``deltas`` maps each tracked
    KPI to ``{"prev", "curr", "delta", "delta_pct"}``. KPIs absent in either
    record are skipped (so legacy run-history records gracefully omit, not
    error). The renderer in `render_diff` highlights deltas > 10%.
    """
    runs = _read_recent_runs(run_history_path, limit=2)
    if len(runs) < 2:
        return None
    prev, curr = runs[0], runs[1]
    tracked = (
        "documents", "entities", "nodes", "edges", "typed_relations",
        "singletons", "singleton_pct", "isolated", "isolated_pct",
        "partial_name_dups", "mentions_per_node",
        "scrape_seconds", "rebuild_seconds", "added",
    )
    deltas = {}
    for key in tracked:
        if key in prev and key in curr:
            p, c = prev[key], curr[key]
            delta = c - p
            base = abs(p) if isinstance(p, (int, float)) and p else 0
            delta_pct = (100.0 * delta / base) if base else None
            deltas[key] = {
                "prev": p, "curr": c, "delta": delta, "delta_pct": delta_pct,
            }
    return {"prev": prev, "curr": curr, "deltas": deltas}


def _reading_stats(documents):
    """Claude-read coverage of the corpus — fresh / stale / never-read."""
    total = len(documents)
    ever_read = sum(
        1 for d in documents if int(d.get("claude_read_count", 0) or 0) > 0
    )
    fresh = sum(1 for d in documents if d.get("claude_read_fresh"))
    return {
        "documents": total,
        "ever_read": ever_read,
        "fresh": fresh,
        "stale": ever_read - fresh,
        "never_read": total - ever_read,
        "fresh_pct": (100.0 * fresh / total) if total else 0.0,
        "ever_read_pct": (100.0 * ever_read / total) if total else 0.0,
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

    dist = data["distributions"]
    out += ["", "DISTRIBUTIONS  (the shape of the data)"]
    out.append("  Nodes by mention count:")
    for label, count in dist["mentions"]:
        out.append("    %-14s %10s" % (label, _int(count)))
    out.append("  Edges by co-occurrence weight:")
    for label, count in dist["edge_weight"]:
        out.append("    %-14s %10s" % (label, _int(count)))

    q = data["quality"]
    out += ["", "DATA QUALITY  (problem spots to improve)"]
    out.append(
        "  %-24s %10s   (%.1f%% of nodes) - likely noise or fragments"
        % ("Single-mention nodes", _int(q["singletons"]), q["singleton_pct"])
    )
    out.append(
        "  %-24s %10s   (%.1f%% of nodes) - no relationships, hard to place"
        % ("Isolated nodes", _int(q["isolated"]), q["isolated_pct"])
    )
    out.append(
        "  %-24s %10s   single-word names that match a fuller name"
        % ("Partial-name duplicates", _int(q["partial_name_dups"]))
    )
    for short, full in q["examples"]:
        out.append('        e.g. "%s" may be the same as "%s"' % (short, full))

    dates = data.get("dates")
    if dates and dates["totals"]["total"]:
        t = dates["totals"]
        out += ["", "PUBLISHED-DATE COVERAGE  (silent if dates fail to parse)"]
        out.append(
            "  %-24s %10s   (%.1f%% of docs)"
            % ("Dates parsed", _int(t["parsed"]), t["parsed_pct"])
        )
        out.append(
            "  %-24s %10s   no `published` field on the document"
            % ("Missing", _int(t["missing"]))
        )
        out.append(
            "  %-24s %10s   present but unrecognised format"
            % ("Unparseable", _int(t["unparseable"]))
        )
        for row in dates["concerning"][:5]:
            out.append(
                '        %s: %d unparseable / %d total (%.1f%%)'
                % (row["source"][:40], row["unparseable"], row["total"],
                   row["unparseable_pct"])
            )

    feeds = data.get("feeds")
    if feeds and feeds["by_source"]:
        out += ["", "FEED HEALTH  (silently-broken feeds surface here)"]
        stale = feeds["stale"]
        if stale:
            out.append(
                "  %d feed(s) with no new docs in 14d and no recent adds:"
                % len(stale)
            )
            for row in stale[:10]:
                if row["hours_since_fetched"] is None:
                    age = "never"
                else:
                    age = _ago(row["hours_since_fetched"])
                out.append(
                    "        %-32s last %s, %d total docs"
                    % (row["source"][:32], age, row["documents"])
                )
        else:
            out.append("  All feeds with corpus presence look active.")

    signals = data.get("signals")
    if signals and signals["documents"]:
        out += ["", "EXTRACTION SIGNALS  (off-topic / extraction failures)"]
        out.append(
            "  %-24s %10s   docs whose body produced ZERO entities"
            % ("No-entity docs", _int(signals["no_entities"]))
        )
        out.append(
            "  %-24s %10s   docs with body shorter than 300 chars"
            % ("Short-body docs", _int(signals["short_body"]))
        )
        for ex in signals["examples_no_entities"][:3]:
            out.append(
                '        %s | %s'
                % (ex["source"][:24], ex["title"][:60])
            )

    out.append(bar)
    return "\n".join(out)


def render_diff(diff):
    """Format the run-to-run KPI deltas into a readable diff report.

    `diff` is the dict returned by `diff_runs`. Each KPI is shown with both
    runs' values and a delta; deltas exceeding +/-10% are marked with `**`
    so visual scanning surfaces meaningful regressions or improvements.
    Pure quality KPIs (singletons, isolated, partial dups) are flagged when
    they *rise*; throughput KPIs (entities, nodes) are flagged either way.
    """
    if diff is None:
        return "No previous run to compare against — only one run in history."
    bar = "=" * 60
    out = [bar, "  AI LANDSCAPE - RUN-OVER-RUN DIFF", bar]
    p_when = (diff["prev"].get("finished_at") or "")[:19].replace("T", " ")
    c_when = (diff["curr"].get("finished_at") or "")[:19].replace("T", " ")
    out += ["", "Previous: %s" % (p_when or "?"), "Current:  %s" % (c_when or "?")]
    out.append("")
    header = "%-24s %10s %10s %10s %8s" % (
        "KPI", "previous", "current", "delta", "%"
    )
    out.append(header)
    out.append("-" * len(header))
    # Order matters for readability — funnel first, then quality, then timing.
    ordered = [
        "documents", "entities", "nodes", "edges", "typed_relations",
        "added",
        "singletons", "singleton_pct", "isolated", "isolated_pct",
        "partial_name_dups", "mentions_per_node",
        "scrape_seconds", "rebuild_seconds",
    ]
    for key in ordered:
        if key not in diff["deltas"]:
            continue
        d = diff["deltas"][key]
        marker = ""
        if d["delta_pct"] is not None and abs(d["delta_pct"]) >= 10.0:
            marker = " **"
        prev_s = _fmt_kpi(d["prev"])
        curr_s = _fmt_kpi(d["curr"])
        delta_s = _fmt_kpi(d["delta"])
        pct_s = (
            "—" if d["delta_pct"] is None else "%+.1f%%" % d["delta_pct"]
        )
        out.append(
            "%-24s %10s %10s %10s %8s%s"
            % (key, prev_s, curr_s, delta_s, pct_s, marker)
        )
    out.append("")
    out.append("** = absolute delta >= 10%; investigate.")
    out.append(bar)
    return "\n".join(out)


def _fmt_kpi(value):
    if isinstance(value, float):
        return "%.2f" % value
    if isinstance(value, int):
        return _int(value)
    return str(value)
