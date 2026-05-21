"""Step 2 of the flow: named entity recognition.

Two backends:
- "rule": deterministic gazetteer + proper-noun extractor (no dependencies).
- "spacy": used automatically if spaCy and en_core_web_sm are installed.

Each extracted entity is a dict: {text, label, start, end}.
`label` is one of the normalized entity types below.
"""

import re

from . import gazetteer

ENTITY_TYPES = {
    "place", "organization", "person", "group",
    "product", "concept", "event", "facility", "misc",
}

# spaCy's labels mapped onto our normalized types.
_SPACY_TYPE = {
    "PERSON": "person", "ORG": "organization", "GPE": "place",
    "LOC": "place", "NORP": "group", "FAC": "facility",
    "PRODUCT": "product", "EVENT": "event",
}

_PERSON_TITLES = {
    "gen", "gen.", "general", "adm", "adm.", "admiral", "col", "col.",
    "colonel", "lt", "lt.", "lieutenant", "capt", "capt.", "captain",
    "maj", "maj.", "major", "sgt", "sgt.", "sergeant", "secretary",
    "president", "sen", "sen.", "senator", "rep", "rep.", "representative",
    "mr", "mr.", "ms", "ms.", "mrs", "mrs.", "dr", "dr.", "gov", "gov.",
    "governor", "ambassador",
}
_ORG_SUFFIXES = {"inc", "corp", "ltd", "llc", "co", "company", "group"}

# Capitalized words that should not, alone, begin an entity.
_STOPWORDS = {
    "The", "A", "An", "This", "That", "These", "Those", "It", "He", "She",
    "They", "We", "I", "But", "And", "Or", "In", "On", "At", "For", "To",
    "With", "As", "By", "From", "Of", "His", "Her", "Their", "Our", "Its",
    "Some", "Many", "Most", "All", "Both", "Each", "While", "When", "Where",
    "What", "Who", "How", "Why", "Although", "Because", "If", "There", "Here",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
    "Sunday", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
}
_CONNECTORS = {"of", "the", "and", "for", "de"}

_WORD_RE = re.compile(r"\S+")


def _build_gazetteer_regex():
    keys = sorted(gazetteer.GAZETTEER.keys(), key=len, reverse=True)
    alt = "|".join(re.escape(k) for k in keys)
    return re.compile(r"(?<!\w)(?:%s)(?!\w)" % alt, re.IGNORECASE)


_GAZ_RE = _build_gazetteer_regex()


def default_backend():
    """Return 'spacy' if available and its model is installed, else 'rule'."""
    try:
        import importlib.util

        if importlib.util.find_spec("spacy") and importlib.util.find_spec(
            "en_core_web_sm"
        ):
            return "spacy"
    except Exception:
        pass
    return "rule"


def _clean(token):
    return token.strip(".,;:!?\"'()[]{}—–")


def _extract_gazetteer(text):
    out = []
    for m in _GAZ_RE.finditer(text):
        surface = m.group(0)
        # Single-word gazetteer hits must be capitalized in the source to
        # reject common-word false positives; multi-word phrases are
        # unambiguous enough to accept in any case.
        if " " not in surface and not surface[0].isupper():
            continue
        canonical, etype = gazetteer.GAZETTEER[surface.lower()]
        out.append(
            {"text": canonical, "label": etype, "start": m.start(), "end": m.end()}
        )
    return out


def _overlaps(start, end, spans):
    return any(start < s_end and s_start < end for s_start, s_end in spans)


def _extract_proper_nouns(text, exclude_spans):
    tokens = [(m.group(0), m.start(), m.end()) for m in _WORD_RE.finditer(text)]
    out = []
    n = len(tokens)
    i = 0
    while i < n:
        word, start, _end = tokens[i]
        core = _clean(word)
        if not core or not core[0].isupper() or core in _STOPWORDS:
            i += 1
            continue
        parts = [core]
        seq_end = tokens[i][2]
        j = i + 1
        while j < n:
            w2, _s2, e2 = tokens[j]
            c2 = _clean(w2)
            if c2 and c2[0].isupper() and c2 not in _STOPWORDS:
                parts.append(c2)
                seq_end = e2
                j += 1
            elif c2.lower() in _CONNECTORS and j + 1 < n:
                nxt = _clean(tokens[j + 1][0])
                if nxt and nxt[0].isupper() and nxt not in _STOPWORDS:
                    parts.append(c2.lower())
                    j += 1
                else:
                    break
            else:
                break
        label = "misc"
        prev_core = _clean(tokens[i - 1][0]).lower() if i > 0 else ""
        if prev_core in _PERSON_TITLES:
            label = "person"
        # A capitalized person title absorbed as the first token of the
        # sequence (e.g. "Secretary Lloyd Austin") is stripped off.
        if (
            len(parts) > 1
            and _clean(parts[0]).lower() in _PERSON_TITLES
            and _clean(parts[1]).lower() not in _CONNECTORS
        ):
            parts = parts[1:]
            label = "person"
            start = tokens[i + 1][1]
        if _clean(parts[-1]).lower() in _ORG_SUFFIXES:
            label = "organization"
        phrase = " ".join(parts)
        if len(phrase) >= 3 and not _overlaps(start, seq_end, exclude_spans):
            out.append(
                {"text": phrase, "label": label, "start": start, "end": seq_end}
            )
        i = j
    return out


def _extract_rule(text):
    gaz = _extract_gazetteer(text)
    spans = [(e["start"], e["end"]) for e in gaz]
    merged = gaz + _extract_proper_nouns(text, spans)
    merged.sort(key=lambda e: e["start"])
    return merged


_NLP = None


def _get_nlp():
    global _NLP
    if _NLP is None:
        import spacy

        _NLP = spacy.load("en_core_web_sm")
    return _NLP


def _extract_spacy(text):
    out = []
    for ent in _get_nlp()(text).ents:
        etype = _SPACY_TYPE.get(ent.label_)
        if etype:
            out.append(
                {
                    "text": ent.text.strip(),
                    "label": etype,
                    "start": ent.start_char,
                    "end": ent.end_char,
                }
            )
    return out


def extract(text, backend=None):
    """Extract entities from `text`. Returns a list of entity dicts."""
    if not text:
        return []
    backend = backend or default_backend()
    if backend == "spacy":
        try:
            return _extract_spacy(text)
        except Exception:
            pass  # fall back to the rule backend if spaCy fails at runtime
    return _extract_rule(text)
