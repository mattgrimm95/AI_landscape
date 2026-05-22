"""FastAPI backend for the knowledge-graph web app.

The full graph is far too large to ship to the browser, so the backend does
the heavy lifting: it queries and subsets the graph database and serves
focused views as JSON. The frontend renders only what it receives, which
keeps it smooth even on a single laptop.
"""

import json
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, corpus, reconcile, report, visualize
from .storage_kg import KnowledgeGraphStore
from .storage_ner import NEROutputLog

_WEB_DIR = config.ROOT / "ailandscape" / "web"

app = FastAPI(title="AI Landscape Knowledge Graph", docs_url="/api/docs")


def _node_json(node):
    return {
        "id": node["id"],
        "label": node["canonical_name"],
        "type": node["type"],
        "mentions": node["mention_count"],
        "documents": node["document_count"],
    }


def _edge_json(edge):
    return {
        "id": edge["id"],
        "source": edge["src_id"],
        "target": edge["dst_id"],
        "weight": edge["weight"],
    }


def _load_graph():
    kg = KnowledgeGraphStore(config.KG_DB)
    try:
        return kg.nodes(), kg.edges()
    finally:
        kg.close()


@app.get("/api/graph")
def api_graph(
    focus: Optional[str] = None,
    type: Optional[str] = None,
    min_mentions: int = 0,
    max_nodes: int = Query(70, ge=1, le=400),
    min_weight: int = Query(8, ge=1),
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
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {
        "nodes": [_node_json(n) for n in sel_nodes],
        "edges": [_edge_json(e) for e in sel_edges],
    }


@app.get("/api/search")
def api_search(q: str, limit: int = Query(20, ge=1, le=100)):
    """Search entities by name."""
    nodes, _edges = _load_graph()
    needle = q.lower().strip()
    matches = [n for n in nodes if needle and needle in n["canonical_name"].lower()]
    matches.sort(key=lambda n: n["mention_count"], reverse=True)
    return {"results": [_node_json(n) for n in matches[:limit]]}


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
        other = None
        if edge["src_id"] == node_id:
            other = edge["dst_id"]
        elif edge["dst_id"] == node_id:
            other = edge["src_id"]
        if other is not None and other in by_id:
            entry = _node_json(by_id[other])
            entry["weight"] = edge["weight"]
            neighbors.append(entry)
    neighbors.sort(key=lambda n: n["weight"], reverse=True)
    return {"node": _node_json(node), "neighbors": neighbors}


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
    documents = corpus.load(config.CORPUS_FILE)
    ner_log = NEROutputLog(config.NER_OUTPUT_DB)
    kg = KnowledgeGraphStore(config.KG_DB)
    try:
        return report.build_overview(
            documents, ner_log, kg, config.RUN_HISTORY_FILE
        )
    finally:
        ner_log.close()
        kg.close()


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

    documents = corpus.load(config.CORPUS_FILE)
    ner_log = NEROutputLog(config.NER_OUTPUT_DB)
    kg = KnowledgeGraphStore(config.KG_DB)
    try:
        summary = reconcile.reconcile(
            documents, ner_log, kg, corrections=reconcile.load_corrections(path)
        )
    finally:
        ner_log.close()
        kg.close()
    return {"applied": True, "graph": summary}


# The frontend is served from /. Defined last so the /api routes win.
app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="web")
