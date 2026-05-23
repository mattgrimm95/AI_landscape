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

from . import corpus, gazetteer, relations

# Normalized aliases dropped as noise regardless of corrections.
_DEFAULT_IGNORE = {
    "officials", "official", "reuters", "associated press",
    "news", "report", "reports", "statement", "spokesperson",
    "analysts", "analyst", "leaders", "leader", "members", "member",
}

# Page-chrome tokens. If an entity surface form contains any of these as a
# whole word, it's an inline metadata-label leak (e.g. "Website Keywords",
# "Subscribe Newsletter", "Authors List") rather than a real named entity.
# Generic enough to catch the pattern across scraped sites without
# hard-coding "Website Keywords" specifically.
_BOILERPLATE_TOKENS = frozenset({
    "keywords", "keyword", "tags", "tag", "categories", "category",
    "subscribe", "newsletter", "topics", "topic", "subject",
    "authors", "affiliation", "department", "citation", "doi",
    # "Links" / "Website" / "Email" / "Phone" / "Contact" already get
    # stripped as attribute boilerplate (_ATTR_BOILERPLATE), so they only
    # land here when wedged into an entity span by NER itself.
    "links", "website", "phone", "contact",
})
_BOILERPLATE_TOKEN_RE = re.compile(r"\b\w+\b")


def _is_boilerplate_entity(text):
    """An entity that contains a page-chrome metadata word is not an entity.

    Used to drop pseudo-entities like "Website Keywords" that come from a
    scraper's metadata block leaking into the body text. A real entity (a
    real person, organization, etc.) very rarely contains "Keywords",
    "Subscribe", "Newsletter", "Tags", etc. as one of its constituent words.
    """
    if not text:
        return False
    tokens = [t.lower() for t in _BOILERPLATE_TOKEN_RE.findall(text)]
    return any(t in _BOILERPLATE_TOKENS for t in tokens)

# Documents with more distinct entities than this contribute no edges,
# keeping the co-occurrence graph from exploding on very long pages.
_MAX_EDGE_ENTITIES = 60

# A single-word entity is kept only if at least this many distinct documents
# mention it — most one-off capitalized words ("Designs", "Allies", "Vision")
# are sentence-initial / common-noun noise rather than real named entities.
# Gazetteer-canonical entities ("Pentagon", "Anduril", etc.) are always kept,
# regardless of frequency.
_MIN_SINGLE_WORD_DF = 2

# A leading article ("the"/"a"/"an") on an entity surface form is almost
# always an NER span artifact ("the Naval Surface Warfare Center"); it is
# stripped from both the dedup key and the display name.
_LEADING_ARTICLE = re.compile(r"^(?:the|an|a)\s+", re.IGNORECASE)

# Boilerplate that often gets glued onto an entity name when a scraped page
# concatenated a person's name with their contact block — academic
# researcher pages render "Name Contact : email Links: Paper" without
# clear separators and NER captures the whole thing as one entity.
_ATTR_BOILERPLATE = re.compile(
    r"\s*(?:Contact|Links?|Email|Phone)\s*[:：]", re.IGNORECASE
)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _split_attributes(text):
    """Strip attribute-block boilerplate off an entity name.

    Returns (clean_name, attrs) where `attrs` may carry an `email` key when
    one was found in the original text. The email is kept as a structured
    attribute on the node so it stays available when the entity is clicked.
    """
    if not text:
        return text, {}
    attrs = {}
    emails = _EMAIL_RE.findall(text)
    if emails:
        attrs["email"] = emails[0]
    name = _ATTR_BOILERPLATE.split(text, maxsplit=1)[0]
    name = _EMAIL_RE.sub("", name)
    name = name.strip(" ,;:.")
    return name, attrs


def _singularize(token):
    """Drop a simple trailing plural 's' from a single word."""
    if len(token) >= 5 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _strip_article(name):
    """Drop a leading article from an entity's display name."""
    return _LEADING_ARTICLE.sub("", name)


def _gazetteer_canonicals():
    """Normalized canonical names of every gazetteer entry — these are
    trusted and never pruned, regardless of how rarely they appear."""
    return frozenset(
        normalize(canonical) for canonical, _type in gazetteer.GAZETTEER.values()
    )


def normalize(text):
    """Normalize an entity surface form into a dedup/alias key.

    Collapses common wording differences so slight variants resolve to the
    same node (and their relationship edges merge): case, curly vs straight
    apostrophes, possessive "'s", acronym dots ("U.S." == "US"), a leading
    article ("the"/"a"/"an"), and a trailing plural on the final word
    ("drone swarms" == "drone swarm").
    """
    s = (text or "").lower().strip().replace("’", "'")
    s = re.sub(r"'s\b", "", s)        # drop possessive 's
    s = s.replace(".", "")            # drop acronym dots: "u.s." -> "us"
    s = re.sub(r"[^\w\s&-]", " ", s)  # other punctuation -> space
    s = re.sub(r"\s+", " ", s).strip()
    s = _LEADING_ARTICLE.sub("", s)   # drop a leading article
    if not s:
        return s
    head, _, last = s.rpartition(" ")
    last = _singularize(last)
    return (head + " " + last) if head else last


# Characters that almost always indicate a malformed entity surface — math
# operators or code-bracket characters that NER can scoop up off a CamelCase
# identifier or a math-laden snippet ("A=2 B=3 C=4", "Use ArrayBuffer").
# Curly braces and square brackets normally get stripped by `_clean`, but
# they survive when attached to a token without a delimiting space.
_BAD_SURFACE_CHARS = re.compile(r"[=<>{}\[\]/]")


def _is_noise(alias):
    if len(alias) < 3 or alias.isdigit():
        return True
    if _BAD_SURFACE_CHARS.search(alias):
        return True
    return False


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


def _coreference_by_email(nodes):
    """Detect shared-email person merges (source key -> target key).

    Some scraped author/byline pages give us a person's email address as a
    structured attribute (see `_split_attributes`). When two distinct person
    nodes share the same email — typically the same individual referred to
    with slightly different name forms across documents ("J. Smith" /
    "Jane Smith") — they're the same person, and the surname-based
    `_coreference` rule won't always catch them (e.g. when both names have
    multiple words, or when the surname collides with another person's).

    The rules:
      * the email must be non-empty AND must look like a personal address
        (not a shared inbox: "info@", "contact@", "press@", "support@"
        are skipped),
      * exactly the multi-mention winner survives; every other person node
        with that email folds into it,
      * the source must be a person node — emails on org nodes (rare, but
        possible) are not merge signals.

    Returns ``{source_key: target_key, ...}`` for the merge engine to
    apply alongside `_coreference`'s output.
    """
    by_email = {}
    for key, node in nodes.items():
        if node.get("type") != "person":
            continue
        email = (node.get("attributes") or {}).get("email") or ""
        email = email.strip().lower()
        if not email:
            continue
        local = email.split("@", 1)[0]
        if local in _SHARED_MAILBOX_LOCALS:
            continue
        by_email.setdefault(email, []).append(key)

    merge_into = {}
    for email, keys in by_email.items():
        if len(keys) < 2:
            continue
        winner = max(
            keys,
            key=lambda k: (
                nodes[k]["mentions"],
                len(nodes[k]["canonical"]),
                k,  # final tiebreaker: deterministic
            ),
        )
        for k in keys:
            if k != winner:
                merge_into[k] = winner
    return merge_into


# Shared / role inboxes that should never act as a person-identity signal —
# multiple distinct people legitimately appear under "press@…" or "info@…".
_SHARED_MAILBOX_LOCALS = frozenset({
    "info", "contact", "press", "media", "support", "help", "sales",
    "admin", "office", "team", "noreply", "no-reply", "donotreply",
    "hello", "hi", "general", "communications", "comms", "pr",
})


def _coreference(nodes):
    """Detect partial-name coreference merges (source key -> target key).

    Two conservative rules, each firing only when there is a *single*
    candidate, so distinct entities are never conflated:
      * person       — a one-word name folds into the unique multi-word
                       person whose surname (last word) it matches:
                       "Hegseth" -> "Pete Hegseth".
      * organization — a one-word name folds into the unique multi-word
                       organization whose first word it matches:
                       "Lockheed" -> "Lockheed Martin". An organization's
                       distinctive token is its first word, not (as for a
                       person) its last. Tokens shorter than four characters
                       are skipped to avoid fuzzy acronym merges.

    Sources are always single-word and targets always multi-word, so the
    merges never chain.
    """
    person_by_last = {}    # surname (lower) -> multi-word person keys
    org_by_first = {}      # first word (lower) -> multi-word organization keys
    single_persons = []    # (key, lowercased single token)
    single_orgs = []
    for key, node in nodes.items():
        words = node["canonical"].split()
        if node["type"] == "person":
            if len(words) >= 2:
                person_by_last.setdefault(words[-1].lower(), []).append(key)
            elif len(words) == 1:
                single_persons.append((key, words[0].lower()))
        elif node["type"] == "organization":
            if len(words) >= 2:
                org_by_first.setdefault(words[0].lower(), []).append(key)
            elif len(words) == 1 and len(words[0]) >= 4:
                single_orgs.append((key, words[0].lower()))

    merge_into = {}
    for single, index in ((single_persons, person_by_last),
                          (single_orgs, org_by_first)):
        for key, token in single:
            candidates = index.get(token, [])
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
    # Gazetteer canonicals are always trusted in the final precision pass.
    gazetteer_canonicals = _gazetteer_canonicals()

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
        return doc_freq.get(alias, 0) >= _MIN_SINGLE_WORD_DF

    def resolve(entity):
        raw_text = (entity.get("text") or "").strip()
        clean_text, attrs = _split_attributes(raw_text)
        if not clean_text:
            clean_text = raw_text
        if _is_boilerplate_entity(clean_text):
            # Page-chrome metadata leak ("Website Keywords", "Subscribe
            # Newsletter", etc.) — never a real entity.
            return None
        if _BAD_SURFACE_CHARS.search(clean_text):
            # Math operators and code-bracket characters in an entity
            # surface mean it came from a CamelCase identifier or a
            # formula ("A=2 B=3 C=4"), not from real prose. The
            # normalize() pass strips them, so this is the only place
            # the signal is still visible — reject before the alias
            # collapses to "a 2 b 3 c 4".
            return None
        alias = normalize(clean_text)
        if not alias or alias in ignore or _is_noise(alias):
            return None
        if not keep(entity, alias):
            return None
        if alias in alias_index:
            return alias_index[alias], None, entity["label"], alias, attrs
        # A human-curated merge value is the display name verbatim; a raw
        # surface form has its leading-article NER artifact stripped.
        if alias in merge:
            canonical = merge[alias]
        else:
            canonical = _strip_article(clean_text)
        key = normalize(canonical)
        if not key:
            return None
        alias_index[alias] = key
        alias_index.setdefault(key, key)
        return key, canonical, entity["label"], alias, attrs

    for doc in documents:
        # Date a node by when the news was published, not when it was
        # scraped; fall back to the fetch date if no published date parses.
        doc_date = (
            corpus.published_date(doc) or (doc.get("fetched_at") or "")[:10]
        )
        doc_keys = set()
        for entity in doc_entities[doc["content_hash"]]:
            resolved = resolve(entity)
            if resolved is None:
                continue
            key, canonical, etype, alias, attrs = resolved
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
                    "attributes": {},
                }
                nodes[key] = node
            node["mentions"] += 1
            node["docs"].add(doc["content_hash"])
            node["aliases"].add(alias)
            node["aliases"].add(key)
            if attrs:
                # Last-write-wins per attribute key. A later mention of the
                # same person can carry a more complete attribute set.
                node["attributes"].update(attrs)
            if node["type"] == "misc" and etype != "misc":
                node["type"] = etype
            if doc_date:
                node["first"] = min(node["first"] or doc_date, doc_date)
                node["last"] = max(node["last"] or doc_date, doc_date)
            doc_keys.add(key)
        if 2 <= len(doc_keys) <= _MAX_EDGE_ENTITIES:
            for pair in itertools.combinations(sorted(doc_keys), 2):
                edges[pair] = edges.get(pair, 0) + 1
        # Typed relationships from cue phrases between nearby entities, each
        # carrying the evidence snippet and the document it was read from.
        for subj, relation, obj, evidence in relations.extract_relations(
            corpus.document_text(doc), doc_entities[doc["content_hash"]]
        ):
            raw_relations.append(
                (normalize(subj), relation, normalize(obj),
                 evidence, doc["content_hash"])
            )

    # Step 4b: coreference — fold partial names into their fuller node, then
    # re-point that node's edges (dropping the resulting self-loops). Two
    # passes: surname / first-word matching (the conservative `_coreference`
    # rule), then shared-email merging for person nodes whose name forms the
    # surname rule did not catch (e.g. two multi-word variants of the same
    # author byline). The email pass runs after `_coreference` so it operates
    # on the already-merged person nodes; both contribute to a single
    # combined `merge_into` map applied below.
    merge_into = _coreference(nodes)
    merge_into.update(_coreference_by_email(nodes))
    for src_key, tgt_key in merge_into.items():
        src = nodes.get(src_key)
        tgt = nodes.get(tgt_key)
        if src is None or tgt is None:
            continue
        tgt["mentions"] += src["mentions"]
        tgt["docs"] |= src["docs"]
        tgt["aliases"] |= src["aliases"]
        for attr_key, attr_value in src.get("attributes", {}).items():
            tgt.setdefault("attributes", {}).setdefault(attr_key, attr_value)
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

    # Final precision pass: a single-word entity that is not gazetteer-
    # trusted, was not folded into a multi-word coreference target, and
    # either has a non-proper-noun shape (lowercase / non-letter initial)
    # or appears in fewer than _MIN_SINGLE_WORD_DF documents is dropped
    # along with its edges. This sweeps out sentence-initial / common-
    # noun noise ("Designs", "Allies", "Vision", "kin", "drogue") that
    # NER mistakenly typed as a real entity.
    weak = set()
    for key, node in list(nodes.items()):
        canonical = node["canonical"] or ""
        if " " in canonical:
            continue
        if normalize(canonical) in gazetteer_canonicals:
            continue
        if not canonical or not canonical[0].isalpha() or not canonical[0].isupper():
            weak.add(key)
        elif len(node["docs"]) < _MIN_SINGLE_WORD_DF:
            weak.add(key)
    for key in weak:
        del nodes[key]
    if weak:
        edges = {
            pair: w
            for pair, w in edges.items()
            if pair[0] not in weak and pair[1] not in weak
        }
        log("pruned %d weak single-word nodes" % len(weak))

    # Resolve extracted relations to node keys (after coreference merges) and
    # tally repeated relationships into directed, weighted typed edges. The
    # first evidence snippet seen for a triple is kept as its provenance.
    typed_edges = {}
    for norm_subj, relation, norm_obj, evidence, chash in raw_relations:
        src = alias_index.get(norm_subj)
        dst = alias_index.get(norm_obj)
        if src is None or dst is None:
            continue
        src = merge_into.get(src, src)
        dst = merge_into.get(dst, dst)
        if src == dst or src not in nodes or dst not in nodes:
            continue
        triple = (src, relation, dst)
        edge = typed_edges.get(triple)
        if edge is None:
            typed_edges[triple] = {
                "weight": 1, "evidence": evidence, "source": chash
            }
        else:
            edge["weight"] += 1

    kg_store.clear()
    key_to_id = {}
    for key, node in sorted(nodes.items()):
        attrs = node.get("attributes") or {}
        key_to_id[key] = kg_store.insert_node(
            canonical_name=node["canonical"],
            node_type=node["type"],
            first_seen=node["first"],
            last_seen=node["last"],
            mention_count=node["mentions"],
            document_count=len(node["docs"]),
            metadata={"attributes": attrs} if attrs else None,
        )
        for alias in sorted(node["aliases"]):
            kg_store.insert_alias(key_to_id[key], alias)
        kg_store.insert_node_documents(key_to_id[key], sorted(node["docs"]))
    for (key_a, key_b), weight in sorted(edges.items()):
        # A normalized, hub-corrected strength: the Jaccard overlap of the two
        # entities' document sets. Raw weight makes every link to a mega-hub
        # look strong; strength stays low unless the two entities genuinely
        # travel together.
        docs_a = len(nodes[key_a]["docs"])
        docs_b = len(nodes[key_b]["docs"])
        union = docs_a + docs_b - weight
        strength = round(weight / union, 4) if union > 0 else 0.0
        kg_store.insert_edge(
            key_to_id[key_a],
            key_to_id[key_b],
            "co_occurs_with",
            weight,
            metadata={"strength": strength},
        )
    for (src_key, relation, dst_key), info in sorted(typed_edges.items()):
        # Confidence rises with the number of independent occurrences:
        # weight 1 -> 0.5, 2 -> 0.667, 5 -> 0.833, 10 -> 0.909.
        confidence = round(1.0 - 1.0 / (1 + info["weight"]), 3)
        kg_store.insert_edge(
            key_to_id[src_key],
            key_to_id[dst_key],
            relation,
            info["weight"],
            metadata={
                "evidence": info["evidence"],
                "source": info["source"],
                "confidence": confidence,
            },
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
