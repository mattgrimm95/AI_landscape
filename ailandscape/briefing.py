"""A generated briefing of the AI national-security landscape.

A short, readable digest for quickly catching up: the documents gathered in
a recent window, the most active entities, trending AI topics, recent
contract awards and deals, and the strongest typed relationships. It is
templated and deterministic — the same corpus and date always produce the
same briefing — and is the natural input for the optional LLM narrative
synthesis (see ailandscape/synthesis.py).

`build_briefing` computes the digest (a plain dict, easy to test);
`render_briefing` formats it as a human-readable report.
"""

import datetime
import json

# Typed relations that read as discrete "events" worth surfacing on their
# own — money and partnerships, the most decision-relevant signal.
_EVENT_RELATIONS = ("awards_contract", "acquires", "partners_with", "supplies")


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _edge_meta(edge):
    """Parse the JSON metadata stored on an edge into a dict."""
    raw = edge.get("metadata")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {}


def build_briefing(documents, kg_store, days=7, now=None, subfield_concepts=None):
    """Compute the briefing digest from the corpus documents and the graph.

    `days` sets the recency window for the "recent documents" section; the
    entity, topic, and relationship sections summarise the whole graph.
    `now` is injectable so tests are deterministic.

    When `subfield_concepts` is given (a collection of canonical concept
    names from `gazetteer.SUBFIELDS`), the briefing is scoped to that
    subfield: every node, edge, and document considered must touch one of
    those concepts. The result is the same shape as the full briefing —
    just narrower — so the "What's happening in <subfield>" view reuses
    the whole render path with no code duplication.
    """
    now = now or _now()
    cutoff = (now - datetime.timedelta(days=days)).isoformat()
    nodes = kg_store.nodes()
    edges = kg_store.edges()
    by_id = {n["id"]: n for n in nodes}

    if subfield_concepts:
        concept_set = {c.lower() for c in subfield_concepts}
        scope_ids = {
            n["id"] for n in nodes
            if n["canonical_name"].lower() in concept_set
        }
        # A node is in scope if it IS a subfield concept or shares an edge
        # with one. This pulls in the orgs/people/products active in the
        # subfield without forcing every match to be a concept itself.
        connected = set(scope_ids)
        for e in edges:
            if e["src_id"] in scope_ids:
                connected.add(e["dst_id"])
            if e["dst_id"] in scope_ids:
                connected.add(e["src_id"])
        nodes = [n for n in nodes if n["id"] in connected]
        edges = [
            e for e in edges
            if e["src_id"] in connected and e["dst_id"] in connected
        ]
        by_id = {n["id"]: n for n in nodes}
        # Filter documents to those mentioning any in-scope entity. Reuses
        # `node_documents` indirectly: a doc is in scope if it's a source
        # for any typed edge whose endpoints are in scope, OR if it's
        # already attached to a subfield concept's document list. Simpler
        # heuristic: a doc whose raw_text contains any subfield concept
        # name. Trafilatura-extracted bodies make this reliable enough.
        documents = [
            d for d in documents
            if any(c in (d.get("raw_text") or "").lower()
                   for c in concept_set)
        ]

    recent = sorted(
        (d for d in documents if (d.get("fetched_at") or "") >= cutoff),
        key=lambda d: d.get("fetched_at", ""),
        reverse=True,
    )

    typed = [e for e in edges if e["relation"] != "co_occurs_with"]

    def _edge_view(edge):
        src = by_id.get(edge["src_id"])
        dst = by_id.get(edge["dst_id"])
        if not src or not dst:
            return None
        meta = _edge_meta(edge)
        return {
            "subject": src["canonical_name"],
            "relation": edge["relation"],
            "object": dst["canonical_name"],
            "weight": edge["weight"],
            "evidence": meta.get("evidence", ""),
            "confidence": meta.get("confidence"),
        }

    events = [
        v
        for v in (
            _edge_view(e) for e in typed if e["relation"] in _EVENT_RELATIONS
        )
        if v
    ]
    events.sort(key=lambda v: v["weight"], reverse=True)

    key_rels = [
        v
        for v in (
            _edge_view(e)
            for e in typed
            if e["relation"] not in _EVENT_RELATIONS
        )
        if v
    ]
    key_rels.sort(key=lambda v: v["weight"], reverse=True)

    by_mentions = sorted(
        nodes, key=lambda n: n["mention_count"], reverse=True
    )
    concepts = [n for n in by_mentions if n["type"] == "concept"]

    # SBIR-sourced documents carry a structured award amount; total it so the
    # briefing shows where defense AI R&D money has gone.
    sbir_docs = [
        d for d in documents
        if (d.get("metadata") or {}).get("data_source") == "SBIR"
    ]
    sbir_funding = {
        "awards": len(sbir_docs),
        "total_amount": sum(
            (d["metadata"].get("award_amount") or 0) for d in sbir_docs
        ),
    }

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "window_days": days,
        "totals": {
            "documents": len(documents),
            "entities": len(nodes),
            "typed_relations": len(typed),
        },
        "sbir_funding": sbir_funding,
        "recent_count": len(recent),
        "recent_documents": [
            {
                "title": d.get("title", ""),
                "source": d.get("source", ""),
                "url": d.get("url", ""),
                "published": d.get("published", ""),
            }
            for d in recent[:12]
        ],
        "top_entities": [
            {
                "id": n["id"],
                "name": n["canonical_name"],
                "type": n["type"],
                "mentions": n["mention_count"],
            }
            for n in by_mentions[:10]
        ],
        "trending_topics": [
            {
                "id": n["id"],
                "name": n["canonical_name"],
                "mentions": n["mention_count"],
            }
            for n in concepts[:10]
        ],
        "contract_awards": events[:12],
        "key_relationships": key_rels[:12],
    }


def render_briefing(data):
    """Format the briefing dict into a human-readable report string."""
    bar = "=" * 62
    out = [
        bar,
        "  AI LANDSCAPE - BRIEFING",
        bar,
        "  generated %s   %d-day window"
        % (data["generated_at"][:16].replace("T", " "), data["window_days"]),
    ]

    t = data["totals"]
    out += [
        "",
        "SNAPSHOT",
        "  %d documents - %d entities - %d typed relationships"
        % (t["documents"], t["entities"], t["typed_relations"]),
    ]

    sf = data["sbir_funding"]
    if sf["awards"]:
        out += [
            "",
            "SBIR / STTR FUNDING",
            "  %d AI-related awards - $%s total"
            % (sf["awards"], format(int(sf["total_amount"]), ",d")),
        ]

    out += [
        "",
        "DOCUMENTS GATHERED IN THE LAST %d DAYS  (%d)"
        % (data["window_days"], data["recent_count"]),
    ]
    for d in data["recent_documents"]:
        out.append("  - %-62s (%s)" % (d["title"][:62], d["source"]))
    if not data["recent_documents"]:
        out.append("  (none)")

    out += ["", "TRENDING AI TOPICS"]
    for c in data["trending_topics"]:
        out.append("  %-28s %5d mentions" % (c["name"][:28], c["mentions"]))
    if not data["trending_topics"]:
        out.append("  (none)")

    out += ["", "MOST ACTIVE ENTITIES"]
    for n in data["top_entities"]:
        out.append(
            "  %-28s %-13s %5d mentions"
            % (n["name"][:28], n["type"], n["mentions"])
        )

    out += ["", "CONTRACT AWARDS & DEALS"]
    for e in data["contract_awards"]:
        out.append(
            "  %s  -- %s -->  %s"
            % (e["subject"], e["relation"].replace("_", " "), e["object"])
        )
        if e["evidence"]:
            out.append('      "%s"' % e["evidence"][:88])
    if not data["contract_awards"]:
        out.append("  (none)")

    out += ["", "KEY RELATIONSHIPS"]
    for e in data["key_relationships"]:
        out.append(
            "  %s  -- %s -->  %s"
            % (e["subject"], e["relation"].replace("_", " "), e["object"])
        )
    if not data["key_relationships"]:
        out.append("  (none)")

    out.append(bar)
    return "\n".join(out)
