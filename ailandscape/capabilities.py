"""Capability subfield index for the AI knowledge graph.

Groups the gazetteer's `concept` entities into the subfields a defense / AI
reader thinks in (see `gazetteer.SUBFIELDS`) and surfaces, for each
subfield: the live concept nodes that match, the top organizations active
in that subfield (read from the co-occurrence edges), and a few summary
counts. Powers the "Capabilities" modal — a map an AI expert can scan to
find what's big in their territory rather than searching one concept at a
time.

`build_capabilities` returns a list of subfield cards as plain dicts, ready
to render. No new schema; everything is read from the in-memory graph.
"""

import collections

from . import gazetteer


def build_capabilities(nodes, edges, top_orgs=5, top_concepts=8):
    """Build one card per AI subfield.

    `nodes` and `edges` are the in-memory graph (use `_cached_graph()` on
    the server). Each returned card has:

      * id, label, tagline — straight from `gazetteer.SUBFIELDS`
      * concepts        — the live concept nodes that match the subfield's
                          canonical list, ranked by mentions
      * top_organizations — orgs that co-occur with the subfield's
                          concepts, ranked by total co-occurrence weight
                          (the "players matrix" for #8 in the UX plan)
      * concept_count   — total concepts in scope
      * mentions        — sum of mentions across the subfield's concepts
      * org_player_count — orgs above a tiny weight threshold
    """
    by_id = {n["id"]: n for n in nodes}
    nodes_by_name = {
        n["canonical_name"].lower(): n for n in nodes if n["type"] == "concept"
    }

    # Index neighbors by node id once so we can compute org rankings per
    # subfield in a single pass.
    neighbors = collections.defaultdict(list)  # node_id -> [(other_id, weight)]
    for edge in edges:
        weight = edge.get("weight", 1) or 1
        neighbors[edge["src_id"]].append((edge["dst_id"], weight))
        neighbors[edge["dst_id"]].append((edge["src_id"], weight))

    out = []
    for subfield in gazetteer.SUBFIELDS:
        concepts = []
        for name in subfield["concepts"]:
            node = nodes_by_name.get(name.lower())
            if node is not None:
                concepts.append(node)
        concepts.sort(key=lambda n: n["mention_count"], reverse=True)

        org_weights = collections.Counter()
        for concept in concepts:
            for other_id, weight in neighbors.get(concept["id"], []):
                other = by_id.get(other_id)
                if other and other["type"] == "organization":
                    org_weights[other_id] += weight

        ranked_orgs = []
        for other_id, weight in org_weights.most_common(top_orgs):
            org = by_id[other_id]
            ranked_orgs.append({
                "id": other_id,
                "name": org["canonical_name"],
                "weight": int(weight),
                "mentions": org["mention_count"],
            })

        out.append({
            "id": subfield["id"],
            "label": subfield["label"],
            "tagline": subfield["tagline"],
            "concepts": [
                {
                    "id": c["id"],
                    "name": c["canonical_name"],
                    "mentions": c["mention_count"],
                    "documents": c["document_count"],
                    "last_seen": c.get("last_seen") or "",
                }
                for c in concepts[:top_concepts]
            ],
            "concept_count": len(concepts),
            "mentions": sum(c["mention_count"] for c in concepts),
            "top_organizations": ranked_orgs,
            "org_player_count": sum(1 for _id, w in org_weights.items() if w >= 2),
        })
    return out


def subfield_concept_names(subfield_id):
    """Return the canonical concept names belonging to a subfield, or [].

    Used by `briefing.build_briefing(subfield_concepts=...)` to scope the
    "What's happening in <subfield>" view.
    """
    for subfield in gazetteer.SUBFIELDS:
        if subfield["id"] == subfield_id:
            return list(subfield["concepts"])
    return []
