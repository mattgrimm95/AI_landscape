"""Corpus / knowledge-graph quality review.

A routine that goes over the corpus and the graph, finds entities that weaken
it, and records strengthenings. Its findings are saved to `review.json` — an
accumulating, version-controlled store: each run *merges* new findings into
the file rather than overwriting it, and because the file is a standalone
source document (not a derived database) the findings survive every database
reconstruction. The recorded merge suggestions are in the same shape the
`correct` command consumes, so they can be applied to the graph directly.

Merges are deliberately *suggested*, not auto-applied: collapsing two
entities is a judgement call, so the routine surfaces candidates for review
rather than silently rewriting the graph.

`build_review` computes the findings; `render_review` formats them;
`save_review` merges them into the accumulating store.
"""

import json
import pathlib


def build_review(documents, kg_store):
    """Audit the graph and return quality findings worth acting on."""
    nodes = kg_store.nodes()

    # Partial-name duplicate candidates: a single-word node whose word is the
    # last word of exactly one multi-word node of the same type — likely the
    # same entity under a shortened name.
    by_last_word = {}
    for node in nodes:
        parts = node["canonical_name"].split()
        if len(parts) >= 2:
            key = (node["type"], parts[-1].lower())
            by_last_word.setdefault(key, []).append(node["canonical_name"])

    merge_suggestions = []
    for node in nodes:
        parts = node["canonical_name"].split()
        if len(parts) != 1:
            continue
        candidates = by_last_word.get((node["type"], parts[0].lower()), [])
        if len(candidates) == 1 and (
            candidates[0].lower() != node["canonical_name"].lower()
        ):
            merge_suggestions.append(
                {"from": node["canonical_name"], "into": candidates[0],
                 "type": node["type"]}
            )
    merge_suggestions.sort(key=lambda s: s["from"].lower())

    singletons = sum(1 for n in nodes if n["mention_count"] <= 1)
    return {
        "documents": len(documents),
        "nodes": len(nodes),
        "singletons": singletons,
        "merge_suggestions": merge_suggestions,
    }


def render_review(data):
    """Format the review findings into a human-readable report string."""
    bar = "=" * 60
    out = [bar, "  AI LANDSCAPE - QUALITY REVIEW", bar, ""]
    out.append(
        "  %d documents - %d graph entities - %d single-mention"
        % (data["documents"], data["nodes"], data["singletons"])
    )
    out += ["", "PARTIAL-NAME MERGE SUGGESTIONS (%d)"
            % len(data["merge_suggestions"])]
    for s in data["merge_suggestions"][:40]:
        out.append(
            '  "%s"  ->  "%s"  (%s)' % (s["from"], s["into"], s["type"])
        )
    if len(data["merge_suggestions"]) > 40:
        out.append("  ... and %d more (see review.json)"
                    % (len(data["merge_suggestions"]) - 40))
    if not data["merge_suggestions"]:
        out.append("  (none)")
    out.append(bar)
    return "\n".join(out)


def save_review(data, path):
    """Merge this review's findings into the accumulating review store.

    The store is never overwritten: existing suggestions are kept and only
    previously unseen ones are appended, so the review accumulates across
    runs. Returns the number of newly added suggestions.
    """
    p = pathlib.Path(path)
    store = {"suggested_merges": [], "history": []}
    if p.exists():
        try:
            store = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            store = {"suggested_merges": [], "history": []}
    store.setdefault("suggested_merges", [])
    store.setdefault("history", [])

    seen = {(s["from"], s["into"]) for s in store["suggested_merges"]}
    added = 0
    for suggestion in data["merge_suggestions"]:
        pair = (suggestion["from"], suggestion["into"])
        if pair not in seen:
            store["suggested_merges"].append(suggestion)
            seen.add(pair)
            added += 1
    store["history"].append(
        {"nodes": data["nodes"], "singletons": data["singletons"],
         "new_suggestions": added}
    )
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")
    return added
