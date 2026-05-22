"""Typed relationship extraction (part of step 4).

Beyond plain co-occurrence, this finds *semantic* relationships: two entities
that sit close together in the same sentence with a recognised cue phrase
between them — e.g. "Lockheed Martin builds the F-35" -> develops.

The rules are deliberately conservative — a short gap between the entities,
no sentence boundary in the gap, and entity types that fit the relation — so
it extracts fewer, more trustworthy relationships rather than many wrong
ones. Each relationship is directed: (subject, relation, object).

Passive voice is detected and the direction flipped, so "Anduril was awarded
a contract by the Pentagon" yields (Pentagon, awards_contract, Anduril) — the
same triple as the active "the Pentagon awarded Anduril a contract".
"""

import re

# Maximum characters allowed between the two entities for a relation to count.
_MAX_GAP = 55

# A sentence boundary inside the gap means the entities are not really related.
_SENTENCE_BREAK = re.compile(r"[.!?]\s")

# A passive-voice gap ("was awarded ... by", "is built by") means the entity
# order is reversed relative to the semantic subject/object, so the direction
# is flipped before the type check.
_PASSIVE = re.compile(r"\b(was|were|been|is|are|being)\b.*?\bby\b")

# Ordered (cue regex, relation, subject types, object types). The gap text is
# matched lowercased; an empty type set means "any entity type". More specific
# structural cues are listed first.
_PATTERNS = [
    (re.compile(r"\b(director|secretary|chief|commander|head|chairman|ceo|"
                r"president)\s+of\b"
                r"|\b(lead|led|head|run|command|oversee)(s|ed|ing)?\b"),
     "leads", {"person"}, {"organization", "group"}),
    (re.compile(r"\b(part|unit|division|subsidiary|arm|branch|wing|office|"
                r"component|member)\s+of\b"),
     "part_of", {"organization", "group", "facility"}, {"organization", "group"}),
    (re.compile(r"\b(based|headquartered|located)\s+in\b"),
     "located_in", {"organization", "group", "facility"}, {"place"}),
    (re.compile(r"\bacquir|\bbought\b|\bbuys?\b|\bpurchas|\btakeover\b"),
     "acquires", {"organization"}, {"organization"}),
    (re.compile(r"\bpartner|\bteam(s|ed|ing)?\b|\bcollaborat|\bjoint\b|\balliance\b"),
     "partners_with", {"organization"}, {"organization"}),
    (re.compile(r"\baward|\bgrant(s|ed)?\b|\bcontract|\bselect|\bpick(s|ed)\b"
                r"|\btapped\b|\bchose\b"),
     "awards_contract", {"organization"}, {"organization"}),
    (re.compile(r"\bdevelop|\bbuil[dt]|\bmanufactur|\bproduc(e|es|ed|ing)\b"
                r"|\bdesign(s|ed|ing)?\b|\bmade\b|\bmakes?\b|\bmaking\b"),
     "develops", {"organization"}, {"product", "concept", "organization"}),
    (re.compile(r"\bsuppl(y|ies|ied)\b|\bdeliver|\bprovid"),
     "supplies", {"organization"}, set()),
]

# All relation names this module can emit (handy for callers/tests).
RELATION_TYPES = tuple(rel for _re, rel, _s, _o in _PATTERNS)


def extract_relations(text, entities):
    """Extract typed relationships from one document.

    `text` is the document text the entity offsets refer to; `entities` are
    NER records with `text`, `label`, `start`/`start_char`, `end`/`end_char`.
    Returns a list of (subject_text, relation, object_text) tuples.
    """
    located = []
    for ent in entities:
        start = ent.get("start", ent.get("start_char"))
        end = ent.get("end", ent.get("end_char"))
        if start is not None and end is not None:
            located.append((start, end, ent))
    located.sort(key=lambda item: item[0])

    relations = []
    for i, (_s1, end1, e1) in enumerate(located):
        for start2, _e2_end, e2 in located[i + 1:]:
            if start2 <= end1:
                continue  # overlapping entities
            if start2 - end1 > _MAX_GAP:
                break  # e2 (and everything after it) is too far away
            gap = text[end1:start2]
            if _SENTENCE_BREAK.search(gap):
                continue  # the entities are in different sentences
            gap_lower = gap.lower()
            passive = _PASSIVE.search(gap_lower) is not None
            for pattern, relation, subj_types, obj_types in _PATTERNS:
                if not pattern.search(gap_lower):
                    continue
                # In passive voice the agent follows the cue, so the later
                # entity is the semantic subject.
                subj, obj = (e2, e1) if passive else (e1, e2)
                if subj_types and subj["label"] not in subj_types:
                    continue
                if obj_types and obj["label"] not in obj_types:
                    continue
                relations.append((subj["text"], relation, obj["text"]))
                break
    return relations
