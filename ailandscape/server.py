"""FastAPI backend for the knowledge-graph web app.

The full graph is far too large to ship to the browser, so the backend does
the heavy lifting: it queries and subsets the graph database and serves
focused views as JSON. The frontend renders only what it receives, which
keeps it smooth even on a single laptop.
"""

import collections
import datetime
import json
import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (
    briefing, config, corpus, reconcile, report, synthesis, tours, trends,
    visualize,
)
from .storage_kg import KnowledgeGraphStore
from .storage_ner import NEROutputLog

_WEB_DIR = config.ROOT / "ailandscape" / "web"

app = FastAPI(title="AI Landscape Knowledge Graph", docs_url="/api/docs")


# ---- mtime-keyed caches ----------------------------------------------------
# Every request used to re-parse 418 JSONL lines and re-load 4909 nodes + 76K
# edges from SQLite. Both inputs are file-backed and only change when the
# pipeline rewrites them, so we memoize on file mtime: cheap to check, exact
# invalidation. No TTL — the cache is correct until the file changes.

_corpus_cache = {"mtime": None, "docs": None}
_graph_cache = {"mtime": None, "nodes": None, "edges": None}


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _cached_corpus():
    mtime = _mtime(config.CORPUS_FILE)
    if _corpus_cache["mtime"] != mtime:
        _corpus_cache["mtime"] = mtime
        _corpus_cache["docs"] = corpus.load(config.CORPUS_FILE)
    return _corpus_cache["docs"]


def _cached_graph():
    mtime = _mtime(config.KG_DB)
    if _graph_cache["mtime"] != mtime:
        kg = KnowledgeGraphStore(config.KG_DB)
        try:
            _graph_cache["nodes"] = kg.nodes()
            _graph_cache["edges"] = kg.edges()
        finally:
            kg.close()
        _graph_cache["mtime"] = mtime
    return _graph_cache["nodes"], _graph_cache["edges"]


def _invalidate_caches():
    """Force the next request to reload (used after `correct` rebuilds)."""
    _corpus_cache["mtime"] = None
    _graph_cache["mtime"] = None


def _node_json(node):
    attributes = {}
    raw = node.get("metadata")
    if raw:
        try:
            attributes = json.loads(raw).get("attributes", {}) or {}
        except (ValueError, TypeError):
            attributes = {}
    return {
        "id": node["id"],
        "label": node["canonical_name"],
        "type": node["type"],
        "mentions": node["mention_count"],
        "documents": node["document_count"],
        "first_seen": node["first_seen"],
        "last_seen": node["last_seen"],
        "attributes": attributes,
    }


def _edge_meta(edge):
    """Parse an edge's stored metadata (evidence, strength) into a dict."""
    raw = edge.get("metadata")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {}


def _edge_json(edge):
    meta = _edge_meta(edge)
    typed = edge["relation"] != "co_occurs_with"
    return {
        "id": edge["id"],
        "source": edge["src_id"],
        "target": edge["dst_id"],
        "weight": edge["weight"],
        "relation": edge["relation"],
        "evidence": meta.get("evidence", ""),
        # Hub-corrected strength; typed edges always score 1.0.
        "strength": 1.0 if typed else meta.get("strength", 0.0),
        # Confidence in a typed relationship; null for co-occurrence.
        "confidence": meta.get("confidence") if typed else None,
    }


def _load_graph():
    return _cached_graph()


def _match_node(nodes, query):
    """Resolve a name query to a node: an exact canonical match if there is
    one, else the most-mentioned substring match, else None."""
    needle = (query or "").lower().strip()
    if not needle:
        return None
    exact = [n for n in nodes if n["canonical_name"].lower() == needle]
    if exact:
        return exact[0]
    matches = [n for n in nodes if needle in n["canonical_name"].lower()]
    matches.sort(key=lambda n: n["mention_count"], reverse=True)
    return matches[0] if matches else None


@app.get("/api/graph")
def api_graph(
    focus: Optional[str] = None,
    type: Optional[str] = None,
    min_mentions: int = 0,
    # Defaults chosen so the landing graph reads as "specific entities" —
    # 90 nodes (was 70) with a min_weight of 2 (was 8) keeps most real
    # links instead of dropping all but the megahub-to-megahub ones.
    max_nodes: int = Query(90, ge=1, le=400),
    min_weight: int = Query(2, ge=1),
    relations_only: bool = False,
    min_confidence: float = Query(0.0, ge=0.0, le=1.0),
    min_strength: float = Query(0.0, ge=0.0, le=1.0),
    src_type: Optional[str] = None,
    dst_type: Optional[str] = None,
):
    """Return a comprehensible subgraph as {nodes, edges}."""
    nodes, edges = _load_graph()
    try:
        sel_nodes, sel_edges = visualize.select_subgraph(
            nodes,
            edges,
            focus=focus,
            node_type=type,
            min_mentions=min_mentions,
            max_nodes=max_nodes,
            min_weight=min_weight,
            relations_only=relations_only,
            min_confidence=min_confidence,
            min_strength=min_strength,
            src_type=src_type,
            dst_type=dst_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {
        "nodes": [_node_json(n) for n in sel_nodes],
        "edges": [_edge_json(e) for e in sel_edges],
    }


@app.get("/api/search")
def api_search(
    q: str,
    limit: int = Query(20, ge=1, le=100),
    since: Optional[str] = None,
    relation: Optional[str] = None,
):
    """Search entities (by canonical name or any alias) and documents.

    Matching aliases means an entity is findable by any name form it was
    ever mentioned under ("DoD" finds Department of Defense); matching
    documents means a topic is findable even before it is its own node.

    Two optional filters narrow the cut:
      * `since` (YYYY-MM-DD): only documents published on/after the date,
        and only entities whose last_seen is on/after the date.
      * `relation`: only entities that participate in at least one typed
        relationship of the given kind (e.g. develops, awards_contract),
        which lets a learner search for "who develops X" or similar.
    """
    needle = q.lower().strip()
    if not needle:
        return {"entities": [], "documents": []}
    nodes, edges = _cached_graph()
    kg = KnowledgeGraphStore(config.KG_DB)
    try:
        aliases = kg.aliases()
    finally:
        kg.close()

    alias_hits = {a["node_id"] for a in aliases if needle in a["alias"]}
    entities = [
        n for n in nodes
        if needle in n["canonical_name"].lower() or n["id"] in alias_hits
    ]
    if since:
        entities = [n for n in entities if (n.get("last_seen") or "") >= since]
    if relation:
        relation_ids = set()
        for e in edges:
            if e["relation"] == relation:
                relation_ids.add(e["src_id"])
                relation_ids.add(e["dst_id"])
        entities = [n for n in entities if n["id"] in relation_ids]
    entities.sort(key=lambda n: n["mention_count"], reverse=True)

    documents = []
    for doc in _cached_corpus():
        title = doc.get("title", "")
        in_title = needle in title.lower()
        if in_title or needle in (doc.get("raw_text", "") or "").lower():
            if since:
                date = corpus.published_date(doc)
                if date and date < since:
                    continue
            documents.append((0 if in_title else 1, doc))
    # Title matches rank above body-only matches.
    documents.sort(key=lambda pair: pair[0])

    return {
        "entities": [_node_json(n) for n in entities[:limit]],
        "documents": [
            {
                "title": d.get("title", ""),
                "source": d.get("source", ""),
                "url": d.get("url", ""),
                "published": d.get("published", ""),
                "content_hash": d.get("content_hash", ""),
            }
            for _rank, d in documents[:limit]
        ],
    }


@app.get("/api/path")
def api_path(
    from_: str = Query(..., alias="from"), to: str = Query(...)
):
    """Find how two entities are connected: the shortest path between them."""
    nodes, edges = _load_graph()
    by_id = {n["id"]: n for n in nodes}
    src = _match_node(nodes, from_)
    dst = _match_node(nodes, to)
    if src is None:
        raise HTTPException(status_code=404,
                            detail="no entity matching %r" % from_)
    if dst is None:
        raise HTTPException(status_code=404,
                            detail="no entity matching %r" % to)
    steps = visualize.find_path(nodes, edges, src["id"], dst["id"])
    found = src["id"] == dst["id"] or bool(steps)
    path_node_ids = [src["id"]] + [step[1] for step in steps]
    return {
        "found": found,
        "from": _node_json(src),
        "to": _node_json(dst),
        "length": len(steps),
        "nodes": [_node_json(by_id[i]) for i in path_node_ids],
        "edges": [_edge_json(step[2]) for step in steps],
    }


@app.get("/api/node/{node_id}")
def api_node(node_id: int):
    """Return a node and its neighbors, ranked by edge weight."""
    nodes, edges = _load_graph()
    by_id = {n["id"]: n for n in nodes}
    node = by_id.get(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="entity not found")
    neighbors = []
    for edge in edges:
        if edge["src_id"] == node_id:
            other, direction = edge["dst_id"], "out"
        elif edge["dst_id"] == node_id:
            other, direction = edge["src_id"], "in"
        else:
            continue
        if other in by_id:
            entry = _node_json(by_id[other])
            entry["weight"] = edge["weight"]
            entry["relation"] = edge["relation"]
            entry["direction"] = direction
            meta = _edge_meta(edge)
            entry["evidence"] = meta.get("evidence", "")
            entry["confidence"] = meta.get("confidence")
            neighbors.append(entry)
    # Typed semantic relationships first, then strongest co-occurrence.
    neighbors.sort(
        key=lambda n: (n["relation"] == "co_occurs_with", -n["weight"])
    )
    return {"node": _node_json(node), "neighbors": neighbors}


@app.get("/api/node/{node_id}/documents")
def api_node_documents(node_id: int, limit: int = Query(50, ge=1, le=200)):
    """Return the source articles a node appears in, most recent first.

    This is what turns navigation into reading: from any entity the user can
    reach the underlying corpus documents that mention it.
    """
    kg = KnowledgeGraphStore(config.KG_DB)
    try:
        hashes = set(kg.documents_for_node(node_id))
    finally:
        kg.close()
    by_hash = {d["content_hash"]: d for d in _cached_corpus()}
    docs = [by_hash[h] for h in hashes if h in by_hash]
    docs.sort(key=lambda d: d.get("fetched_at", ""), reverse=True)
    timeline = collections.Counter()
    for d in docs:
        date = corpus.published_date(d)
        if date:
            timeline[date[:7]] += 1
    return {
        "total": len(docs),
        "timeline": [
            {"month": m, "count": c} for m, c in sorted(timeline.items())
        ],
        "momentum": _momentum(docs),
        "documents": [
            {
                "title": d.get("title", ""),
                "source": d.get("source", ""),
                "url": d.get("url", ""),
                "published": d.get("published", ""),
                "snippet": (d.get("raw_text", "") or "")[:240].strip(),
                "claude_read_count": int(d.get("claude_read_count", 0) or 0),
                "claude_read_fresh": bool(d.get("claude_read_fresh")),
                "content_hash": d.get("content_hash", ""),
            }
            for d in docs[:limit]
        ],
    }


def _momentum(docs):
    """Compare mentions in the last 30 days vs the prior 30 days.

    Returns a small dict with a verbal label (rising / steady / cooling) and
    the raw counts so the UI can show "X over last 30 days". A node needs at
    least three mentions in the recent window for the label to mean anything;
    below that, callers fall back to "too few mentions".
    """
    today = datetime.date.today()
    recent_start = today - datetime.timedelta(days=30)
    prior_start = today - datetime.timedelta(days=60)
    recent = prior = 0
    for d in docs:
        date_str = corpus.published_date(d)
        if not date_str:
            continue
        try:
            date = datetime.date.fromisoformat(date_str)
        except ValueError:
            continue
        if date > today:
            continue
        if date >= recent_start:
            recent += 1
        elif date >= prior_start:
            prior += 1
    label = "too few mentions"
    if recent >= 3:
        if recent >= prior * 1.5:
            label = "rising"
        elif recent * 1.5 <= prior:
            label = "cooling"
        else:
            label = "steady"
    return {
        "label": label,
        "recent_30d": recent,
        "prior_30d": prior,
    }


@app.get("/api/tours")
def api_tours():
    """Return the curated story tours for the sidebar / guide."""
    return {"tours": tours.build_tour_index()}


@app.get("/api/types")
def api_types():
    """Entity types and how many nodes each has (for the filter UI)."""
    nodes, _edges = _load_graph()
    counts = {}
    for node in nodes:
        counts[node["type"]] = counts.get(node["type"], 0) + 1
    types = [{"type": t, "count": c} for t, c in counts.items()]
    types.sort(key=lambda x: x["count"], reverse=True)
    return {"types": types}


@app.get("/api/overview")
def api_overview():
    """The statistical overview as structured JSON."""
    documents = _cached_corpus()
    ner_log = NEROutputLog(config.NER_OUTPUT_DB)
    kg = KnowledgeGraphStore(config.KG_DB)
    try:
        return report.build_overview(
            documents, ner_log, kg, config.RUN_HISTORY_FILE
        )
    finally:
        ner_log.close()
        kg.close()


@app.get("/api/trends")
def api_trends():
    """Temporal signals — document volume by month, new and active entities."""
    documents = _cached_corpus()
    kg = KnowledgeGraphStore(config.KG_DB)
    try:
        return trends.build_trends(documents, kg)
    finally:
        kg.close()


@app.get("/api/document/{content_hash}")
def api_document(content_hash: str):
    """Return one corpus document's full text + metadata for in-app reading.

    The dossier surfaces document titles + snippets; this endpoint is what
    backs the side drawer so a reader stays in-app instead of bouncing to
    the original URL on every click. The hash identity is the corpus's
    immutable key, so it survives renames in the upstream feed.
    """
    by_hash = {d["content_hash"]: d for d in _cached_corpus()}
    doc = by_hash.get(content_hash)
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")
    return {
        "content_hash": doc.get("content_hash"),
        "title": doc.get("title", ""),
        "source": doc.get("source", ""),
        "url": doc.get("url", ""),
        "published": doc.get("published", ""),
        "fetched_at": doc.get("fetched_at", ""),
        "raw_text": doc.get("raw_text", ""),
        "metadata": doc.get("metadata", {}),
        "claude_read_count": int(doc.get("claude_read_count", 0) or 0),
        "claude_read_fresh": bool(doc.get("claude_read_fresh")),
        "claude_last_read": doc.get("claude_last_read", ""),
    }


class MarkRead(BaseModel):
    content_hash: str


@app.post("/api/document/mark-read")
def api_document_mark_read(payload: MarkRead):
    """Stamp one document as Claude-read (counter += 1, fresh = True)."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds"
    )
    n = corpus.mark_read(config.CORPUS_FILE, [payload.content_hash], now)
    if not n:
        raise HTTPException(status_code=404, detail="document not found")
    # Corpus file mtime changed; next request will reload through the cache.
    _invalidate_caches()
    return {"updated": n, "when": now}


@app.get("/api/recent")
def api_recent(
    since: Optional[str] = None, days: int = Query(7, ge=1, le=90),
):
    """What's new since `since` (YYYY-MM-DD) — articles + entities."""
    documents = _cached_corpus()
    kg = KnowledgeGraphStore(config.KG_DB)
    try:
        return trends.build_recent(documents, kg, since=since, days=days)
    finally:
        kg.close()


@app.get("/api/briefing")
def api_briefing(days: int = Query(7, ge=1, le=90)):
    """A generated briefing of the landscape as structured JSON."""
    documents = _cached_corpus()
    kg = KnowledgeGraphStore(config.KG_DB)
    try:
        return briefing.build_briefing(documents, kg, days=days)
    finally:
        kg.close()


@app.get("/api/briefing/narrative")
def api_briefing_narrative(days: int = Query(7, ge=1, le=90)):
    """An LLM-written analyst narrative of the briefing — strictly opt-in.

    Returns available=False (not an error) when no ANTHROPIC_API_KEY is set,
    so the UI can show the feature without the call ever being attempted.
    """
    if not synthesis.is_configured():
        return {
            "available": False,
            "message": "Set ANTHROPIC_API_KEY to enable narrative synthesis.",
        }
    documents = _cached_corpus()
    kg = KnowledgeGraphStore(config.KG_DB)
    try:
        data = briefing.build_briefing(documents, kg, days=days)
    finally:
        kg.close()
    try:
        return {"available": True, "narrative": synthesis.summarize_briefing(data)}
    except synthesis.SynthesisError as exc:
        return {"available": True, "error": str(exc)}


class Correction(BaseModel):
    action: str
    terms: List[str]


@app.post("/api/correct")
def api_correct(correction: Correction):
    """Record a manual correction, then re-reconcile so it takes effect.

    Only `reconcile` is re-run (not NER), so the corrected graph is ready in
    seconds. The correction is written to the version-controlled
    corrections.json, keeping reconstruction deterministic.
    """
    if correction.action not in ("merge", "ignore"):
        raise HTTPException(status_code=400, detail="action must be merge or ignore")
    path = config.CORRECTIONS_FILE
    data = {"merge": {}, "ignore": []}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("merge", {})
    data.setdefault("ignore", [])
    if correction.action == "merge":
        if len(correction.terms) != 2:
            raise HTTPException(status_code=400, detail="merge needs two terms")
        data["merge"][correction.terms[0]] = correction.terms[1]
    else:
        if len(correction.terms) != 1:
            raise HTTPException(status_code=400, detail="ignore needs one term")
        if correction.terms[0] not in data["ignore"]:
            data["ignore"].append(correction.terms[0])
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    documents = _cached_corpus()
    ner_log = NEROutputLog(config.NER_OUTPUT_DB)
    kg = KnowledgeGraphStore(config.KG_DB)
    try:
        summary = reconcile.reconcile(
            documents, ner_log, kg, corrections=reconcile.load_corrections(path)
        )
    finally:
        ner_log.close()
        kg.close()
    # The graph file was just rewritten; force re-load on the next request.
    _invalidate_caches()
    return {"applied": True, "graph": summary}


# The frontend is served from /. Defined last so the /api routes win.
app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="web")
