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
import re

# Words that, when they precede or qualify a bare name in a multi-word
# entity, mark that entity as a *different* thing — not a longer form of the
# bare name. "Gulf of Oman" is not Oman the country; "South Lebanon" is not
# Lebanon; "Broad Institute of MIT" is not MIT. The bare → multi-word merge
# is suppressed in those cases.
_PLACE_QUALIFIERS = frozenset({
    "new", "old", "south", "north", "east", "west", "central",
    "upper", "lower", "greater", "lesser",
    "gulf", "sea", "bay", "strait", "ocean", "river", "lake",
    "mountain", "mountains", "valley", "island", "islands", "peninsula",
    "city", "republic", "state", "province", "county", "region",
    "department", "ministry", "institute", "university", "school",
    "supreme", "national", "federal", "international",
})


def _is_compound_with_bare(bare_name, full_name):
    """True if `full_name` looks like a compound whose ending word `bare_name`
    refers to a *distinct* entity, so the bare → full merge would be wrong.

    Two structural cues trigger this: a qualifier word (Gulf/South/New/...)
    leading the full name, or an `<X> of <bare_name>` pattern.
    """
    parts = full_name.split()
    if not parts:
        return False
    words = [p.lower() for p in parts]
    if words[0] in _PLACE_QUALIFIERS:
        return True
    if "of" in words:
        idx = words.index("of")
        if bare_name.lower() in words[idx + 1:]:
            return True
    return False

# Structural noise patterns.
_URL_OR_HANDLE = re.compile(r"@|//|https?:", re.IGNORECASE)
# A capitalized common-English-word followed by a 1-4 digit suffix —
# "Block 2", "Group 1", "Assumption 2", "Lot 2", "Frozen 2". Some real
# products match too ("Gemma 2", "Aster 30"); surfaced as a *suggestion*,
# not auto-dropped, so a human picks. Requires the leading word to be
# capital + at least three lowercase letters so short military designations
# like "Mk 1" / "F 35" do not match.
_LIKELY_VERSION_TAG = re.compile(r"^[A-Z][a-z]{2,}\s+\d{1,4}$")


def _noise_reason(node):
    """Return a short reason string if a node looks like noise, else None.

    Structural-only heuristics; the broader "is this a real proper noun?"
    judgement is left to the reconcile prune (which uses gazetteer trust
    and document frequency) and to human review via `correct ignore`.
    """
    name = (node.get("canonical_name") or "").strip()
    if len(name) < 3:
        return "too short"
    if _URL_OR_HANDLE.search(name):
        return "contains URL or handle characters"
    if not any(ch.isalpha() for ch in name):
        return "no letters"
    if _LIKELY_VERSION_TAG.fullmatch(name):
        return "looks like a generic version tag"
    return None


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
        bare = node["canonical_name"]
        candidates = by_last_word.get((node["type"], parts[0].lower()), [])
        if len(candidates) != 1:
            continue
        full = candidates[0]
        if full.lower() == bare.lower():
            continue
        # Skip suggestions where the compound clearly names a *different*
        # entity that just happens to contain the bare word — "Oman" is
        # not "Gulf of Oman", "London" is not "New London", "MIT" is not
        # "Broad Institute of MIT".
        if _is_compound_with_bare(bare, full):
            continue
        merge_suggestions.append(
            {"from": bare, "into": full, "type": node["type"]}
        )
    merge_suggestions.sort(key=lambda s: s["from"].lower())

    noise_suggestions = []
    for node in nodes:
        reason = _noise_reason(node)
        if reason:
            noise_suggestions.append({
                "name": node["canonical_name"],
                "type": node["type"],
                "reason": reason,
            })
    noise_suggestions.sort(key=lambda n: n["name"].lower())

    singletons = sum(1 for n in nodes if n["mention_count"] <= 1)
    return {
        "documents": len(documents),
        "nodes": len(nodes),
        "singletons": singletons,
        "merge_suggestions": merge_suggestions,
        "noise_suggestions": noise_suggestions,
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

    noise = data.get("noise_suggestions", [])
    out += ["", "STRUCTURAL NOISE (%d)" % len(noise)]
    for n in noise[:40]:
        out.append('  "%s"  (%s, %s)' % (n["name"], n["type"], n["reason"]))
    if len(noise) > 40:
        out.append("  ... and %d more (see review.json)" % (len(noise) - 40))
    if not noise:
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
    store = {"suggested_merges": [], "suggested_ignores": [], "history": []}
    if p.exists():
        try:
            store = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            store = {"suggested_merges": [], "suggested_ignores": [],
                     "history": []}
    store.setdefault("suggested_merges", [])
    store.setdefault("suggested_ignores", [])
    store.setdefault("history", [])

    seen_merges = {(s["from"], s["into"]) for s in store["suggested_merges"]}
    new_merges = 0
    for suggestion in data["merge_suggestions"]:
        pair = (suggestion["from"], suggestion["into"])
        if pair not in seen_merges:
            store["suggested_merges"].append(suggestion)
            seen_merges.add(pair)
            new_merges += 1

    seen_ignores = {s["name"] for s in store["suggested_ignores"]}
    new_ignores = 0
    for suggestion in data.get("noise_suggestions", []):
        if suggestion["name"] not in seen_ignores:
            store["suggested_ignores"].append(suggestion)
            seen_ignores.add(suggestion["name"])
            new_ignores += 1

    store["history"].append({
        "nodes": data["nodes"],
        "singletons": data["singletons"],
        "new_merges": new_merges,
        "new_ignores": new_ignores,
    })
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")
    return {"merges": new_merges, "ignores": new_ignores}
