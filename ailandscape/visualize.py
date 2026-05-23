"""Step 7 of the flow: an interactive visualization of the knowledge graph.

The full graph is far too large to render at once (thousands of nodes, tens
of thousands of edges), so a *comprehensible subgraph* is selected first —
either the most-connected entities, or a chosen entity and its neighborhood
— and rendered as a self-contained interactive HTML page with pyvis / vis.js
(zoom, pan, drag; hover a node for its details; click to highlight links).
"""

import collections
import json

# A consistent, legible colour per entity type.
_TYPE_COLORS = {
    "place": "#4f83cc",
    "organization": "#e08a3c",
    "person": "#5fa55a",
    "product": "#9b6dc7",
    "concept": "#cc4f5a",
    "group": "#3fb5a8",
    "facility": "#c9a23b",
    "event": "#8d8d8d",
    "misc": "#9aa0a6",
}
_DEFAULT_COLOR = "#9aa0a6"

# Entities so broad they function as categories rather than navigable nodes.
# When the default landing view is selected (no focus, no type filter, no
# relations-only mode), these are pushed to the back of the ranking so the
# screen leads with concrete entities — "U.S. Air Force", "Palantir",
# "Computer Vision", "MIT" — instead of "Artificial Intelligence" + "United
# States" dominating the canvas by sheer mention volume. They are NOT
# removed from the graph: a focused query, a type filter, or a search will
# still surface them.
_GENERIC_GIANTS = frozenset(
    name.lower()
    for name in (
        "Artificial Intelligence",
        "AI",
        "United States",
        "U.S.",
        "US",
        "America",
        "American",
        "Americans",
        "China",
        "Chinese",
        "Russia",
        "Russian",
        "Europe",
        "European",
        "Ukraine",
        "Ukrainian",
        "Iran",
        "Iranian",
        "Israel",
        "Israeli",
        "WASHINGTON",
        "Washington",
    )
)


def _is_generic_giant(node):
    return (node.get("canonical_name") or "").lower() in _GENERIC_GIANTS


def _degree(edges):
    degree = collections.Counter()
    for edge in edges:
        degree[edge["src_id"]] += 1
        degree[edge["dst_id"]] += 1
    return degree


def _edge_strength(edge):
    """Normalized co-occurrence strength (Jaccard, 0..1) stored on the edge.

    Typed semantic edges always score 1.0 so they rank above co-occurrence.
    """
    if edge["relation"] != "co_occurs_with":
        return 1.0
    meta = edge.get("metadata")
    if not meta:
        return 0.0
    try:
        return json.loads(meta).get("strength", 0.0) or 0.0
    except (ValueError, TypeError):
        return 0.0


def _edge_confidence(edge):
    """Stored confidence (0..1) for typed relations; None for co-occurrence."""
    if edge["relation"] == "co_occurs_with":
        return None
    meta = edge.get("metadata")
    if not meta:
        return None
    try:
        return json.loads(meta).get("confidence")
    except (ValueError, TypeError):
        return None


def select_subgraph(
    nodes,
    edges,
    focus=None,
    node_type=None,
    min_mentions=0,
    max_nodes=70,
    min_weight=3,
    relations_only=False,
    min_confidence=0.0,
    min_strength=0.0,
    src_type=None,
    dst_type=None,
):
    """Pick a comprehensible subgraph to render.

    With `focus`, returns the matching entity plus its strongest neighbors;
    otherwise returns the most *informative* entities — those participating
    in typed semantic relationships first, then the most-connected — so the
    default view leads with signal rather than a co-occurrence hairball.
    Type / mention / edge-weight filters narrow the result. With
    `relations_only`, plain co-occurrence is dropped entirely, leaving just
    the typed-relationship graph.

    Two evidence filters layer on top:
      * `min_confidence` drops typed edges with confidence below the floor
        (co-occurrence edges, which have no confidence, are left untouched
        unless `relations_only` is set).
      * `min_strength` drops co-occurrence edges below the Jaccard floor.

    `src_type` / `dst_type` further narrow typed edges to those whose ends
    are of those entity types (e.g. organization → product). Co-occurrence
    is undirected so the test is applied to either endpoint pair.

    Returns (nodes, edges) as filtered lists.
    """
    by_id = {n["id"]: n for n in nodes}
    candidates = [
        n
        for n in nodes
        if (node_type is None or n["type"] == node_type)
        and n["mention_count"] >= min_mentions
    ]
    candidate_ids = {n["id"] for n in candidates}

    def _passes_evidence(edge):
        """Apply the confidence / strength / type-pair filters to one edge."""
        is_typed = edge["relation"] != "co_occurs_with"
        if is_typed:
            if min_confidence > 0.0:
                conf = _edge_confidence(edge)
                if conf is None or conf < min_confidence:
                    return False
            if src_type or dst_type:
                src = by_id.get(edge["src_id"])
                dst = by_id.get(edge["dst_id"])
                if not src or not dst:
                    return False
                if src_type and src["type"] != src_type:
                    return False
                if dst_type and dst["type"] != dst_type:
                    return False
        else:
            if min_strength > 0.0 and _edge_strength(edge) < min_strength:
                return False
            if src_type or dst_type:
                src = by_id.get(edge["src_id"])
                dst = by_id.get(edge["dst_id"])
                if not src or not dst:
                    return False
                pair = {src["type"], dst["type"]}
                if src_type and src_type not in pair:
                    return False
                if dst_type and dst_type not in pair:
                    return False
        return True

    # Typed-relationship participation among the candidates — the signal the
    # default view and the relations-only view are built around. The same
    # evidence filters used for the final edge cut apply here so a node only
    # earns "typed" status from edges that will survive rendering.
    typed_degree = collections.Counter()
    for edge in edges:
        if (
            edge["relation"] != "co_occurs_with"
            and edge["src_id"] in candidate_ids
            and edge["dst_id"] in candidate_ids
            and _passes_evidence(edge)
        ):
            typed_degree[edge["src_id"]] += 1
            typed_degree[edge["dst_id"]] += 1

    if focus:
        needle = focus.lower()
        match = next(
            (n for n in nodes if needle in n["canonical_name"].lower()), None
        )
        if match is None:
            raise ValueError("no entity matching %r" % focus)
        neighbor_score = {}
        for edge in edges:
            is_typed = edge["relation"] != "co_occurs_with"
            # Typed semantic edges always count; co-occurrence is filtered,
            # and dropped outright in relations-only mode.
            if not is_typed and (relations_only or edge["weight"] < min_weight):
                continue
            if not _passes_evidence(edge):
                continue
            other = None
            if edge["src_id"] == match["id"]:
                other = edge["dst_id"]
            elif edge["dst_id"] == match["id"]:
                other = edge["src_id"]
            if other is not None and other in candidate_ids:
                # Rank neighbors by normalized strength, so a focused entity's
                # genuine associations surface ahead of links to mega-hubs.
                neighbor_score[other] = max(
                    neighbor_score.get(other, 0.0), _edge_strength(edge)
                )
        keep = {match["id"]}
        for nid, _s in sorted(
            neighbor_score.items(), key=lambda kv: kv[1], reverse=True
        ):
            if len(keep) >= max_nodes:
                break
            keep.add(nid)
    else:
        degree = _degree(edges)
        pool = candidates
        if relations_only:
            pool = [n for n in candidates if typed_degree.get(n["id"], 0)]
        # In the default landing view (no type filter, no relations-only
        # mode), generic giants like "AI" and "United States" are demoted to
        # the bottom of the ranking so the screen leads with concrete,
        # navigable entities. Any user-narrowed view (type filter, relations
        # only, focus) skips this so the giants still surface when they're
        # actually what the user asked for.
        demote_generics = node_type is None and not relations_only

        def rank_key(n):
            demoted = demote_generics and _is_generic_giant(n)
            return (
                # not demoted (True > False), then typed degree, then total
                # degree, then sheer mention count. Demoted entities sort
                # last in every tier.
                not demoted,
                typed_degree.get(n["id"], 0),
                degree.get(n["id"], 0),
                n["mention_count"],
            )

        ranked = sorted(pool, key=rank_key, reverse=True)
        keep = {n["id"] for n in ranked[:max_nodes]}

    sel_nodes = [by_id[i] for i in keep if i in by_id]
    sel_edges = []
    for e in edges:
        if e["src_id"] not in keep or e["dst_id"] not in keep:
            continue
        if e["relation"] == "co_occurs_with":
            # Co-occurrence is weight-filtered, and dropped in relations-only
            # mode; typed semantic edges are always shown.
            if relations_only or e["weight"] < min_weight:
                continue
        if not _passes_evidence(e):
            continue
        sel_edges.append(e)
    return sel_nodes, sel_edges


def find_path(nodes, edges, src_id, dst_id):
    """Shortest path between two node ids, as a list of (from, to, edge) steps.

    A breadth-first search by hop count. Typed semantic edges are expanded
    before co-occurrence ones, so among equally short routes a meaningful
    typed-relationship path is preferred. Returns [] if the two nodes are the
    same or not connected.
    """
    if src_id == dst_id:
        return []
    adjacency = {}
    for edge in edges:
        adjacency.setdefault(edge["src_id"], []).append((edge["dst_id"], edge))
        adjacency.setdefault(edge["dst_id"], []).append((edge["src_id"], edge))
    # Expand typed edges first so BFS records them as predecessors.
    for neighbors in adjacency.values():
        neighbors.sort(
            key=lambda pair: pair[1]["relation"] == "co_occurs_with"
        )

    came_from = {src_id: None}
    queue = collections.deque([src_id])
    while queue:
        current = queue.popleft()
        if current == dst_id:
            break
        for neighbor, edge in adjacency.get(current, []):
            if neighbor not in came_from:
                came_from[neighbor] = (current, edge)
                queue.append(neighbor)

    if dst_id not in came_from:
        return []
    steps = []
    node = dst_id
    while came_from[node] is not None:
        previous, edge = came_from[node]
        steps.append((previous, node, edge))
        node = previous
    steps.reverse()
    return steps


def render(nodes, edges, output_path, title="AI Landscape Knowledge Graph"):
    """Render a subgraph to a self-contained interactive HTML file."""
    from pyvis.network import Network

    net = Network(
        height="820px",
        width="100%",
        bgcolor="#11151c",
        font_color="#e8e8e8",
        notebook=False,
        directed=False,
        cdn_resources="in_line",
        neighborhood_highlight=True,  # click a node -> dim the rest
        select_menu=True,             # dropdown to find any entity
        heading=title,
    )
    net.barnes_hut()

    mention_max = max((n["mention_count"] for n in nodes), default=1) or 1
    for node in nodes:
        ratio = node["mention_count"] / mention_max
        size = 12 + 40 * (ratio ** 0.5)
        tooltip = "%s  |  %s  |  %d mentions  |  %d documents" % (
            node["canonical_name"],
            node["type"],
            node["mention_count"],
            node["document_count"],
        )
        net.add_node(
            node["id"],
            label=node["canonical_name"],
            title=tooltip,
            color=_TYPE_COLORS.get(node["type"], _DEFAULT_COLOR),
            size=size,
        )

    for edge in edges:
        if edge["relation"] == "co_occurs_with":
            net.add_edge(
                edge["src_id"],
                edge["dst_id"],
                value=edge["weight"],
                title="co-occurs in %d documents" % edge["weight"],
                color="#3a4555",
            )
        else:
            # Typed semantic relationships: directed, labelled, and brighter.
            label = edge["relation"].replace("_", " ")
            net.add_edge(
                edge["src_id"],
                edge["dst_id"],
                value=edge["weight"],
                title="%s (seen %dx)" % (label, edge["weight"]),
                label=label,
                color="#5e9bff",
                arrows="to",
            )

    # pyvis's write_html() opens the file with the platform default encoding
    # (cp1252 on Windows) and fails on the inlined vis.js; generate the HTML
    # and write it as UTF-8 ourselves.
    html = net.generate_html(notebook=False)
    with open(str(output_path), "w", encoding="utf-8") as handle:
        handle.write(html)
    return output_path
