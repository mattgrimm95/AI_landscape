"""Step 2 of the flow: named entity recognition.

Three backends, selected explicitly via config.DEFAULT_NER_BACKEND or the
`--ner` CLI flag:
- "hybrid": gazetteer (precise, canonical) + spaCy (typed long tail).
- "rule": deterministic gazetteer + proper-noun extractor (no dependencies).
- "spacy": statistical model only (requires spaCy + en_core_web_sm).

"hybrid" and "spacy" fall back to "rule" if spaCy is not installed.
Each extracted entity is a dict: {text, label, start, end}.
`label` is one of the normalized entity types below.
"""

import re

from . import config, gazetteer

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
    """Return the configured default NER backend ('rule' or 'spacy').

    The default is an explicit configuration choice, not inferred from
    whichever package happens to be installed.
    """
    return config.DEFAULT_NER_BACKEND


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
        # Track the original-source span for each kept part, so we can rebuild
        # `start` correctly if a leading run of titles ("Lt. Col. ...") gets
        # stripped below.
        part_starts = [tokens[i][1]]
        j = i + 1
        while j < n:
            w2, _s2, e2 = tokens[j]
            c2 = _clean(w2)
            if c2 and c2[0].isupper() and c2 not in _STOPWORDS:
                parts.append(c2)
                part_starts.append(_s2)
                seq_end = e2
                j += 1
                continue
            # Connector handling: a phrase may bridge one or two lowercase
            # connectors ("of", "the", "and", "for", "de") if a capitalized
            # non-stopword follows. This lets us capture "Department of the
            # Treasury", "Office of the Director of National Intelligence",
            # "Joint Chiefs of Staff" as single entities — without runaway
            # consumption of ordinary prose.
            if c2.lower() in _CONNECTORS:
                k = j
                connector_run = []
                while (
                    k < n
                    and k - j < 2  # at most 2 connectors in a row
                    and _clean(tokens[k][0]).lower() in _CONNECTORS
                ):
                    connector_run.append(k)
                    k += 1
                if (
                    k < n
                    and connector_run
                    and _clean(tokens[k][0])
                    and _clean(tokens[k][0])[0].isupper()
                    and _clean(tokens[k][0]) not in _STOPWORDS
                ):
                    for ci in connector_run:
                        parts.append(_clean(tokens[ci][0]).lower())
                        part_starts.append(tokens[ci][1])
                    j = k
                    continue
            break
        label = "misc"
        prev_core = _clean(tokens[i - 1][0]).lower() if i > 0 else ""
        if prev_core in _PERSON_TITLES:
            label = "person"
        # Strip any *leading* run of person titles that got absorbed into the
        # phrase ("Secretary Lloyd Austin", "Lt. Col. Jason Kruck"). The first
        # non-title, non-connector token becomes the start of the entity.
        strip_to = 0
        while (
            strip_to < len(parts) - 1
            and _clean(parts[strip_to]).lower() in _PERSON_TITLES
            and _clean(parts[strip_to + 1]).lower() not in _CONNECTORS
        ):
            strip_to += 1
        if strip_to:
            parts = parts[strip_to:]
            start = part_starts[strip_to]
            part_starts = part_starts[strip_to:]
            label = "person"
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
    """Load the first available spaCy model from `config.SPACY_MODELS`.

    The preference list (larger first) means a user who installs a bigger
    model gets higher recall automatically, while a fresh install with only
    the `en_core_web_sm` that the docs ship still works without configuration.
    """
    global _NLP
    if _NLP is None:
        import spacy

        last_error = None
        for model_name in config.SPACY_MODELS:
            try:
                _NLP = spacy.load(model_name)
                break
            except (OSError, ImportError) as exc:
                last_error = exc
                continue
        if _NLP is None:
            raise last_error or RuntimeError("no spaCy model available")
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


def _extract_hybrid(text):
    """Gazetteer entities plus spaCy entities for the rest of the text.

    The gazetteer runs first — it is precise and yields canonical names — and
    spaCy supplies typed entities for everything the gazetteer does not
    already cover. Where a spaCy span overlaps a gazetteer span the gazetteer
    wins, so curated defense entities keep their canonical form and type.
    """
    gaz = _extract_gazetteer(text)
    gaz_spans = [(e["start"], e["end"]) for e in gaz]
    extra = [
        e
        for e in _extract_spacy(text)
        if not _overlaps(e["start"], e["end"], gaz_spans)
    ]
    merged = gaz + extra
    merged.sort(key=lambda e: e["start"])
    return merged


def extract(text, backend=None):
    """Extract entities from `text`. Returns a list of entity dicts.

    The "hybrid" and "spacy" backends fall back to the rule backend if spaCy
    is unavailable, so the pipeline always runs.
    """
    if not text:
        return []
    backend = backend or default_backend()
    if backend == "hybrid":
        try:
            return _extract_hybrid(text)
        except Exception:
            return _extract_rule(text)
    if backend == "spacy":
        try:
            return _extract_spacy(text)
        except Exception:
            return _extract_rule(text)
    return _extract_rule(text)
