"""Step 4 of the flow: filter / de-duplicate / reconcile / relationship links.

Builds the knowledge graph (step 5) from the corpus documents and the NER
output log: raw entity mentions are normalized, de-duplicated into canonical
nodes via an alias index, coreferenced (partial person names folded into
their full-name node), and linked by co-occurrence edges (entities sharing a
document).
"""

import itertools
import json
import pathlib
import re

from . import corpus, relations

# Normalized aliases dropped as noise regardless of corrections.
_DEFAULT_IGNORE = {
    "officials", "official", "reuters", "associated press",
    "news", "report", "reports", "statement", "spokesperson",
    "analysts", "analyst", "leaders", "leader", "members", "member",
}

# Documents with more distinct entities than this contribute no edges,
# keeping the co-occurrence graph from exploding on very long pages.
_MAX_EDGE_ENTITIES = 60

# A lone capitalized word (untyped "misc" entity) is kept only if at least
# this many distinct documents mention it — most one-off capitalized words
# are sentence-initial noise rather than real named entities.
_MIN_SINGLE_MISC_DF = 2


def _singularize(token):
    """Drop a simple trailing plural 's' from a single word."""
    if len(token) >= 5 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def normalize(text):
    """Normalize an entity surface form into a dedup/alias key.

    Collapses common wording differences so slight variants resolve to the
    same node (and their relationship edges merge): case, curly vs straight
    apostrophes, possessive "'s", acronym dots ("U.S." == "US"), a leading
    "the", and a trailing plural on the final word ("drone swarms" ==
    "drone swarm").
    """
    s = (text or "").lower().strip().replace("’", "'")
    s = re.sub(r"'s\b", "", s)        # drop possessive 's
    s = s.replace(".", "")            # drop acronym dots: "u.s." -> "us"
    s = re.sub(r"[^\w\s&-]", " ", s)  # other punctuation -> space
    s = re.sub(r"\s+", " ", s).strip()
    if s.startswith("the "):
        s = s[4:]
    if not s:
        return s
    head, _, last = s.rpartition(" ")
    last = _singularize(last)
    return (head + " " + last) if head else last


def _is_noise(alias):
    return len(alias) < 3 or alias.isdigit()


def load_corrections(path):
    """Load a manual-corrections file. Returns (merge_map, ignore_set).

    File format (JSON):
        {"merge": {"surface form": "Canonical Name"}, "ignore": ["surface"]}
    """
    p = pathlib.Path(path)
    if not p.exists():
        return {}, set()
    data = json.loads(p.read_text(encoding="utf-8"))
    merge = {normalize(k): v for k, v in data.get("merge", {}).items()}
    ignore = {normalize(x) for x in data.get("ignore", [])}
    return merge, ignore


def _coreference(nodes):
    """Detect person coreference merges (source key -> target key).

    A one-word person name that is the last word of exactly *one* multi-word
    person node merges into it — e.g. "Hegseth" -> "Pete Hegseth". The merge
    is made only when there is a single candidate, so two different people
    who share a surname are never conflated.
    """
    by_last_name = {}      # surname (lower) -> list of multi-word person keys
    single_persons = []    # (key, lowercased single token)
    for key, node in nodes.items():
        if node["type"] != "person":
            continue
        words = node["canonical"].split()
        if len(words) >= 2:
            by_last_name.setdefault(words[-1].lower(), []).append(key)
        elif len(words) == 1:
            single_persons.append((key, words[0].lower()))

    merge_into = {}
    for key, token in single_persons:
        candidates = by_last_name.get(token, [])
        if len(candidates) == 1 and candidates[0] != key:
            merge_into[key] = candidates[0]
    return merge_into


def reconcile(documents, ner_log, kg_store, corrections=None, log=None):
    """Build the knowledge graph from the corpus documents and NER log.

    `documents` is the list of corpus document records; `ner_log` supplies the
    raw entities for each, keyed by `content_hash`. Returns a summary dict.
    """
    log = log or (lambda *_a: None)
    merge, ignore = corrections if corrections else ({}, set())
    # Normalize the default ignore terms so they match normalized aliases.
    ignore = set(ignore) | {normalize(x) for x in _DEFAULT_IGNORE}

    # Pass 1: cache each document's entities and count document frequency —
    # how many distinct documents mention each normalized alias.
    doc_entities = {}
    doc_freq = {}
    for doc in documents:
        ents = ner_log.entities_for(doc["content_hash"])
        doc_entities[doc["content_hash"]] = ents
        for alias in {normalize(e["text"]) for e in ents}:
            if alias:
                doc_freq[alias] = doc_freq.get(alias, 0) + 1

    nodes = {}          # key -> node accumulator
    alias_index = {}    # normalized alias -> node key
    edges = {}          # (key_a, key_b) -> co-occurrence weight
    raw_relations = []  # (normalized subject, relation, normalized object)

    def keep(entity, alias):
        # Raw NER is deliberately greedy; this is the step-4 precision filter.
        # Human-curated merges, typed (gazetteer) hits, and multi-word phrases
        # are kept; a lone capitalized word is kept only if several documents
        # use it.
        if alias in merge:
            return True
        if entity["label"] != "misc":
            return True
        if " " in alias:
            return True
        return doc_freq.get(alias, 0) >= _MIN_SINGLE_MISC_DF

    def resolve(entity):
        alias = normalize(entity["text"])
        if not alias or alias in ignore or _is_noise(alias):
            return None
        if not keep(entity, alias):
            return None
        if alias in alias_index:
            return alias_index[alias], None, entity["label"], alias
        canonical = merge.get(alias, entity["text"].strip())
        key = normalize(canonical)
        if not key:
            return None
        alias_index[alias] = key
        alias_index.setdefault(key, key)
        return key, canonical, entity["label"], alias

    for doc in documents:
        doc_date = doc.get("fetched_at") or ""
        doc_keys = set()
        for entity in doc_entities[doc["content_hash"]]:
            resolved = resolve(entity)
            if resolved is None:
                continue
            key, canonical, etype, alias = resolved
            node = nodes.get(key)
            if node is None:
                node = {
                    "canonical": canonical or entity["text"].strip(),
                    "type": etype,
                    "mentions": 0,
                    "docs": set(),
                    "aliases": set(),
                    "first": doc_date,
                    "last": doc_date,
                }
                nodes[key] = node
            node["mentions"] += 1
            node["docs"].add(doc["content_hash"])
            node["aliases"].add(alias)
            node["aliases"].add(key)
            if node["type"] == "misc" and etype != "misc":
                node["type"] = etype
            if doc_date:
                node["first"] = min(node["first"] or doc_date, doc_date)
                node["last"] = max(node["last"] or doc_date, doc_date)
            doc_keys.add(key)
        if 2 <= len(doc_keys) <= _MAX_EDGE_ENTITIES:
            for pair in itertools.combinations(sorted(doc_keys), 2):
                edges[pair] = edges.get(pair, 0) + 1
        # Typed relationships from cue phrases between nearby entities.
        for subj, relation, obj in relations.extract_relations(
            corpus.document_text(doc), doc_entities[doc["content_hash"]]
        ):
            raw_relations.append((normalize(subj), relation, normalize(obj)))

    # Step 4b: coreference — fold partial names into their fuller node, then
    # re-point that node's edges (dropping the resulting self-loops).
    merge_into = _coreference(nodes)
    for src_key, tgt_key in merge_into.items():
        src = nodes.get(src_key)
        tgt = nodes.get(tgt_key)
        if src is None or tgt is None:
            continue
        tgt["mentions"] += src["mentions"]
        tgt["docs"] |= src["docs"]
        tgt["aliases"] |= src["aliases"]
        if src["first"]:
            tgt["first"] = min(tgt["first"] or src["first"], src["first"])
        if src["last"]:
            tgt["last"] = max(tgt["last"] or src["last"], src["last"])
        del nodes[src_key]
    if merge_into:
        remapped = {}
        for (key_a, key_b), weight in edges.items():
            key_a = merge_into.get(key_a, key_a)
            key_b = merge_into.get(key_b, key_b)
            if key_a == key_b:
                continue
            pair = (key_a, key_b) if key_a < key_b else (key_b, key_a)
            remapped[pair] = remapped.get(pair, 0) + weight
        edges = remapped
        log("coreference: merged %d partial-name nodes" % len(merge_into))

    # Resolve extracted relations to node keys (after coreference merges) and
    # tally repeated relationships into directed, weighted typed edges.
    typed_edges = {}
    for norm_subj, relation, norm_obj in raw_relations:
        src = alias_index.get(norm_subj)
        dst = alias_index.get(norm_obj)
        if src is None or dst is None:
            continue
        src = merge_into.get(src, src)
        dst = merge_into.get(dst, dst)
        if src == dst or src not in nodes or dst not in nodes:
            continue
        triple = (src, relation, dst)
        typed_edges[triple] = typed_edges.get(triple, 0) + 1

    kg_store.clear()
    key_to_id = {}
    for key, node in sorted(nodes.items()):
        key_to_id[key] = kg_store.insert_node(
            canonical_name=node["canonical"],
            node_type=node["type"],
            first_seen=node["first"],
            last_seen=node["last"],
            mention_count=node["mentions"],
            document_count=len(node["docs"]),
        )
        for alias in sorted(node["aliases"]):
            kg_store.insert_alias(key_to_id[key], alias)
    for (key_a, key_b), weight in sorted(edges.items()):
        kg_store.insert_edge(
            key_to_id[key_a], key_to_id[key_b], "co_occurs_with", weight
        )
    for (src_key, relation, dst_key), weight in sorted(typed_edges.items()):
        kg_store.insert_edge(
            key_to_id[src_key], key_to_id[dst_key], relation, weight
        )
    kg_store.commit()

    summary = {
        "documents": len(documents),
        "nodes": len(nodes),
        "edges": len(edges) + len(typed_edges),
        "typed_relations": len(typed_edges),
    }
    log(
        "reconcile: %d documents -> %d nodes, %d edges (%d typed relations)"
        % (
            summary["documents"],
            summary["nodes"],
            summary["edges"],
            summary["typed_relations"],
        )
    )
    return summary
