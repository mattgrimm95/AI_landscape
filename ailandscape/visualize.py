"""Step 7 of the flow: an interactive visualization of the knowledge graph.

The full graph is far too large to render at once (thousands of nodes, tens
of thousands of edges), so a *comprehensible subgraph* is selected first —
either the most-connected entities, or a chosen entity and its neighborhood
— and rendered as a self-contained interactive HTML page with pyvis / vis.js
(zoom, pan, drag; hover a node for its details; click to highlight links).
"""

import collections

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


def _degree(edges):
    degree = collections.Counter()
    for edge in edges:
        degree[edge["src_id"]] += 1
        degree[edge["dst_id"]] += 1
    return degree


def select_subgraph(
    nodes,
    edges,
    focus=None,
    node_type=None,
    min_mentions=0,
    max_nodes=70,
    min_weight=3,
):
    """Pick a comprehensible subgraph to render.

    With `focus`, returns the matching entity plus its strongest neighbors;
    otherwise returns the most-connected entities. Type / mention / edge-weight
    filters narrow the result. Returns (nodes, edges) as filtered lists.
    """
    by_id = {n["id"]: n for n in nodes}
    candidates = [
        n
        for n in nodes
        if (node_type is None or n["type"] == node_type)
        and n["mention_count"] >= min_mentions
    ]
    candidate_ids = {n["id"] for n in candidates}

    if focus:
        needle = focus.lower()
        match = next(
            (n for n in nodes if needle in n["canonical_name"].lower()), None
        )
        if match is None:
            raise ValueError("no entity matching %r" % focus)
        neighbor_weight = {}
        for edge in edges:
            # Typed semantic edges always count; co-occurrence is filtered.
            if edge["relation"] == "co_occurs_with" and edge["weight"] < min_weight:
                continue
            other = None
            if edge["src_id"] == match["id"]:
                other = edge["dst_id"]
            elif edge["dst_id"] == match["id"]:
                other = edge["src_id"]
            if other is not None and other in candidate_ids:
                neighbor_weight[other] = max(
                    neighbor_weight.get(other, 0), edge["weight"]
                )
        keep = {match["id"]}
        for nid, _w in sorted(
            neighbor_weight.items(), key=lambda kv: kv[1], reverse=True
        ):
            if len(keep) >= max_nodes:
                break
            keep.add(nid)
    else:
        degree = _degree(edges)
        ranked = sorted(
            candidates,
            key=lambda n: (degree.get(n["id"], 0), n["mention_count"]),
            reverse=True,
        )
        keep = {n["id"] for n in ranked[:max_nodes]}

    sel_nodes = [by_id[i] for i in keep if i in by_id]
    sel_edges = [
        e
        for e in edges
        if e["src_id"] in keep
        and e["dst_id"] in keep
        # Typed semantic edges always shown; co-occurrence is weight-filtered.
        and (e["relation"] != "co_occurs_with" or e["weight"] >= min_weight)
    ]
    return sel_nodes, sel_edges


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
