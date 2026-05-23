"""Apposition-based acronym ↔ expansion extraction.

Mines corpus text for patterns where an acronym is defined alongside its
expansion in the same phrase — the only reliable way to map an acronym to
its meaning without an external knowledge base. Initial-matching alone is
too false-positive prone (every two adjacent capitalized words look like a
potential acronym definition); requiring an actual definitional cue in the
article text is what makes this safe enough to surface as a suggestion.

Recognized patterns
-------------------
* ``<Expansion> (<ACRONYM>)`` — by far the most common, e.g.
  "Defense Advanced Research Projects Agency (DARPA)".
* ``<ACRONYM> (<Expansion>)`` — also common, e.g.
  "DARPA (Defense Advanced Research Projects Agency)".

The extractor returns raw ``(acronym, expansion)`` candidate pairs without
deciding whether to merge. Callers tally pairs across documents and apply
their own gate (the project standard: require corroboration in
``_MIN_DOC_FREQ_FOR_REAL = 2`` documents before promoting from suggestion
to applied merge). Strict initials verification rejects accidental
adjacencies — "the F-35 Joint Strike Fighter (JSF)" is matched only if
"JSF" really is the initial-letter contraction of the expansion words.
"""

import re


# An acronym candidate: 2 to 7 uppercase letters/digits/hyphens, starting
# with a letter. Bounded by word edges. Single-letter "acronyms" are
# rejected (too noisy); the upper bound keeps us from grabbing all-caps
# section headers ("BREAKING NEWS") that aren't true acronyms.
_ACRONYM = r"[A-Z][A-Z0-9\-]{1,6}"

# An expansion candidate: 2-8 words separated by spaces, commas, or
# "and"/"or" connectors. The first character of each word must be a letter
# (lowercase is OK — "intelligence, surveillance and reconnaissance"
# expands to ISR). Hyphens permitted within words.
_EXPANSION_WORD = r"[A-Za-z][A-Za-z\-]+"
_EXPANSION = (
    r"(?:" + _EXPANSION_WORD + r")"
    r"(?:[ ,]+(?:and\s+|or\s+)?" + _EXPANSION_WORD + r"){1,7}"
)

# Pattern 1: <Expansion> (<ACRONYM>)
_EXPANSION_PAREN = re.compile(
    r"(" + _EXPANSION + r")\s*\((" + _ACRONYM + r")\)"
)
# Pattern 2: <ACRONYM> (<Expansion>)
_ACRONYM_PAREN = re.compile(
    r"\b(" + _ACRONYM + r")\s*\((" + _EXPANSION + r")\)"
)

# Acronym words that are skipped when matching initials — these connectors
# are typically absent from the acronym ("Department of Defense" → DOD,
# not DOOD).
_SKIP = frozenset({
    "of", "the", "and", "or", "for", "in", "to", "a", "an", "at",
    "by", "from", "with", "&",
})


def _words(expansion):
    """Tokenize an expansion into substantive words.

    Splits on whitespace and commas, drops empties. Preserves connector
    words ("of", "the", "and") because their inclusion in the acronym is
    case-by-case ("MIT" skips "of"; "DOD" includes the "o" of "of").
    """
    return [w for w in re.split(r"[ ,]+", expansion.strip()) if w]


def _matches(acronym, expansion):
    """True when `acronym` is plausibly the initials of `expansion`.

    Acronyms vary in how they treat connector words. Some skip them
    ("MIT" = "Massachusetts Institute of Technology"); some include them
    ("DOD" = "Department of Defense" with the lowercase "o"). The check
    tries every subset of include/skip across the connectors so both
    conventions are recognized — without admitting acronyms whose letters
    don't appear among the expansion's word initials at all.

    The acronym must also use the LAST word's first letter, so we don't
    accidentally match a prefix of a longer noun phrase (e.g. "DARPA" must
    not match "Defense Advanced Research Projects Agency Inc").

    Leading words can be skipped freely (lets "The Defense Advanced
    Research Projects Agency" still match "DARPA").
    """
    target = re.sub(r"[^A-Z]", "", acronym.upper())
    if len(target) < 2:
        return False
    words = _words(expansion)
    if not words:
        return False
    if words[-1][0].upper() != target[-1]:
        return False
    # Try every possible starting position (trim leading articles / "The").
    for start in range(len(words)):
        sub = words[start:]
        if _try_match(sub, target):
            return True
    return False


def _try_match(words, target):
    """Try every include/skip combination on the connectors of `words`.

    Returns True if some combination produces the acronym letters exactly,
    in order. Connectors are the only optional tokens — substantive words
    always contribute their first letter.
    """
    connector_indices = [i for i, w in enumerate(words) if w.lower() in _SKIP]
    if not connector_indices:
        # No connectors — single fixed initials sequence.
        return _build_initials(words, set(range(len(words)))) == target
    for mask in range(1 << len(connector_indices)):
        kept = set(range(len(words)))
        for k, idx in enumerate(connector_indices):
            if not (mask & (1 << k)):
                kept.discard(idx)
        if _build_initials(words, kept) == target:
            return True
    return False


def _build_initials(words, kept_indices):
    """Build an initials string from the words at the given indices."""
    return "".join(
        words[i][0].upper() for i in sorted(kept_indices) if words[i]
    )


def extract_pairs(text):
    """Extract ``(acronym, expansion)`` candidate pairs from one document.

    Each candidate pair has passed the strict initials check, so what
    survives is a definitional apposition rather than a chance adjacency.
    The caller is responsible for aggregating across documents and gating
    on document frequency before promoting a pair to an applied merge.
    """
    if not text:
        return []
    pairs = []
    for match in _EXPANSION_PAREN.finditer(text):
        expansion, acronym = match.group(1).strip(), match.group(2).strip()
        if _matches(acronym, expansion):
            refined = _refine_expansion(acronym, expansion)
            pairs.append((acronym, _normalize_expansion(refined, acronym)))
    for match in _ACRONYM_PAREN.finditer(text):
        acronym, expansion = match.group(1).strip(), match.group(2).strip()
        if _matches(acronym, expansion):
            refined = _refine_expansion(acronym, expansion)
            pairs.append((acronym, _normalize_expansion(refined, acronym)))
    return pairs


def _refine_expansion(acronym, expansion):
    """Pick the shortest leading-trimmed expansion that still matches.

    The expansion regex is greedy — for "Officials at the Department of
    Defense (DOD)" it captures the whole prefix even though only
    "Department of Defense" is the acronym's actual expansion. After a
    candidate has passed `_matches`, we walk leading words off as long
    as removing one more still leaves a valid match; the result is the
    canonical, minimal form for storage and aggregation.
    """
    words = _words(expansion)
    # Trim leading words one at a time; stop when removing one more
    # breaks the match. Always keep at least two words so we don't end
    # up with single-word "expansions" that lose the noun phrase.
    while len(words) > 2:
        if _matches(acronym, " ".join(words[1:])):
            words = words[1:]
        else:
            break
    return " ".join(words)


_LEADING_FILLER = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)


def _normalize_expansion(expansion, acronym=None):
    """Clean up an extracted expansion for storage.

    * Collapse runs of whitespace and commas.
    * Strip a leading article ("the"/"a"/"an") if doing so still leaves
      the initials matching the acronym. This handles the common case
      where the apposition is "The Defense Advanced Research Projects
      Agency (DARPA)" — we want to store "Defense Advanced Research
      Projects Agency" as the canonical expansion.
    """
    s = re.sub(r"\s+", " ", expansion).strip()
    s = re.sub(r"\s*,\s*", ", ", s)
    if acronym:
        trimmed = _LEADING_FILLER.sub("", s)
        if trimmed and trimmed != s and _matches(acronym, trimmed):
            s = trimmed
    return s


# Threshold for promoting an extracted (acronym, expansion) pair from a
# raw candidate to a corroborated suggestion. Mirrors the version-tag
# gate in `review.py` — two independent documents using the same
# acronym definition is very unlikely to be coincidence.
MIN_DOC_FREQ = 2


def aggregate(per_doc_pairs):
    """Aggregate per-document pairs into corroborated suggestions.

    Input: an iterable of ``[(acronym, expansion), ...]`` lists, one per
    document.

    Output: a sorted list of suggestion dicts
    ``{"acronym": ..., "expansion": ..., "documents": <count>}``
    for pairs that appeared in at least ``MIN_DOC_FREQ`` distinct
    documents. Conflicting expansions for the same acronym are kept
    as separate suggestions so the curator can disambiguate.
    """
    counts = {}
    for doc_index, pairs in enumerate(per_doc_pairs):
        for acronym, expansion in set(pairs):
            key = (acronym.upper(), expansion.lower())
            entry = counts.setdefault(
                key,
                {
                    "acronym": acronym.upper(),
                    "expansion": expansion,
                    "documents": set(),
                },
            )
            entry["documents"].add(doc_index)
    out = []
    for entry in counts.values():
        if len(entry["documents"]) >= MIN_DOC_FREQ:
            out.append({
                "acronym": entry["acronym"],
                "expansion": entry["expansion"],
                "documents": len(entry["documents"]),
            })
    out.sort(key=lambda e: (-e["documents"], e["acronym"]))
    return out
