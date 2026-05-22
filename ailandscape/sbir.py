"""SBIR / STTR award ingestion — a non-RSS data source (part of step 1).

The U.S. Small Business Innovation Research (SBIR) and Small Business
Technology Transfer (STTR) programs fund early-stage R&D at small firms.
SBIR.gov publishes awarded contracts through a public JSON API that needs
no API key. This module pulls awards, keeps only the AI-related ones, and
converts each into a corpus document so it flows through the same NER /
reconcile / graph pipeline as a scraped news article.

The API offers no keyword search (only agency / firm / year / institution),
so AI relevance is decided here by scanning each award's title, abstract,
and research-area keywords.

The public API sits behind an AWS API gateway that throttles aggressively
and can return HTTP 429 (`TooManyRequestsError`) — at times for every
request, when the public endpoint is throttled off. Callers treat
`SBIRError` as a soft failure — SBIR is skipped for that run and the rest
of the pipeline continues, exactly as a single failed RSS feed is tolerated.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config

# Public awards endpoint. No API key required.
API_URL = "https://api.www.sbir.gov/public/api/awards"

# Awards requested per API page, and the cap on AI awards kept per run so a
# single source cannot dominate the corpus (mirrors the RSS per-feed cap).
_PAGE_SIZE = 100
MAX_AI_AWARDS = 150

# Award text that marks a project as AI-related. Matched case-insensitively
# with word boundaries. The list is intentionally broad — it spans core
# AI/ML, model families, perception, data science, and embodied-AI
# robotics — because the API offers no keyword search, so this filter is
# the only AI gate.
_AI_TERMS = re.compile(
    r"\b("
    # core AI / machine learning
    r"artificial intelligence|machine intelligence|machine learning|"
    r"deep learning|deep neural|neural net(work)?|"
    r"reinforcement learning|supervised learning|unsupervised learning|"
    r"transfer learning|federated learning|"
    # model families / generative
    r"generative (ai|model|adversarial)|language model|foundation model|"
    r"transformer model|diffusion model|"
    r"ml model|ai/ml|ai-(enabled|powered|driven)|ml-based|"
    # perception / language
    r"computer vision|machine vision|image (recognition|classification)|"
    r"object (detection|recognition)|scene understanding|"
    r"speech recognition|natural language|pattern recognition|"
    r"anomaly detection|"
    # data science / analytics
    r"data science|data analytics|data mining|predictive analytics|"
    r"predictive model|"
    # embodied AI / robotics
    r"robots?|robotics?|embodied (ai|intelligence|agent)|"
    r"imitation learning|motion planning|path planning|"
    r"dexterous manipulation|visual servoing|sensorimotor|"
    r"sim-to-real|sim2real|simultaneous localization|quadruped|"
    # other AI
    r"autonom(y|ous)|cognitive computing|expert system"
    r")\b",
    re.IGNORECASE,
)

# Strong AI acronyms, matched case-sensitively as bare uppercase tokens so
# they never fire on lowercase fragments inside ordinary words ("ai" in
# "maintain"/"available", "ml" in "HTML"). SLAM = simultaneous localization
# and mapping, a core embodied-AI / robotics technique.
_AI_ACRONYMS = re.compile(r"\b(AI|ML|LLM|NLP|SLAM)\b")


class SBIRError(Exception):
    """Raised when the SBIR API cannot be reached or returns an error."""


def is_ai_related(award):
    """True if an award's title / abstract / keywords mention AI or ML."""
    text = " ".join(
        str(award.get(field, "") or "")
        for field in ("award_title", "abstract", "research_area_keywords")
    )
    return bool(_AI_TERMS.search(text) or _AI_ACRONYMS.search(text))


def _get_json(url, max_retries=3):
    """Fetch and parse JSON from the SBIR API, backing off on rate limits."""
    for attempt in range(max_retries + 1):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": config.HTTP_USER_AGENT,
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(
                req, timeout=config.HTTP_TIMEOUT * 2
            ) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            # 429 (TooManyRequestsError) is the gateway throttle — back off
            # and retry. Any other status (incl. 503) is a hard failure.
            if exc.code == 429 and attempt < max_retries:
                time.sleep(3 * (2 ** attempt))
                continue
            raise SBIRError("SBIR API returned HTTP %s" % exc.code) from exc
        except (urllib.error.URLError, ValueError, OSError) as exc:
            raise SBIRError("SBIR API request failed: %s" % exc) from exc
    raise SBIRError(
        "SBIR API still rate-limited after %d retries" % max_retries
    )


def fetch_awards(agency="DOD", year=None, max_records=200):
    """Fetch awards for one agency (optionally one year) from the API.

    Pages through results until `max_records` is reached or the data runs
    out. Raises SBIRError if the API cannot be reached.
    """
    awards = []
    start = 0
    while len(awards) < max_records:
        params = {
            "agency": agency,
            "rows": _PAGE_SIZE,
            "start": start,
            "format": "json",
        }
        if year:
            params["year"] = year
        batch = _get_json(API_URL + "?" + urllib.parse.urlencode(params))
        if not isinstance(batch, list) or not batch:
            break
        awards.extend(batch)
        if len(batch) < _PAGE_SIZE:
            break
        start += _PAGE_SIZE
    return awards[:max_records]


def _clean(value):
    """Collapse whitespace; treat None as empty."""
    return " ".join(str(value or "").split())


def _award_url(award, firm, title):
    """A stable, unique URL for an award (used for de-duplication)."""
    link = _clean(award.get("award_link"))
    if link:
        return link
    ref = (
        _clean(award.get("agency_tracking_number"))
        or _clean(award.get("contract"))
        or ("%s %s" % (firm, title))
    )
    return "https://www.sbir.gov/award/" + urllib.parse.quote(ref)


def award_to_article(award):
    """Convert one SBIR award record into a scraper-style article dict.

    The body opens with short, factual, active-voice sentences — phrased so
    NER and the typed-relation extractor recognise the funding relationship
    (agency -> firm) — followed by the project abstract.
    """
    firm = _clean(award.get("firm"))
    agency = _clean(award.get("agency"))
    branch = _clean(award.get("branch"))
    funder = ("%s %s" % (agency, branch)).strip() or "A federal agency"
    program = _clean(award.get("program")) or "SBIR"
    phase = _clean(award.get("phase"))
    title = _clean(award.get("award_title")) or "SBIR/STTR award"
    abstract = _clean(award.get("abstract"))
    ri_name = _clean(award.get("ri_name"))
    pi_name = _clean(award.get("pi_name"))

    contract_desc = " ".join(p for p in (program, phase, "contract") if p)
    sentences = []
    if firm:
        sentences.append("%s awarded %s a %s." % (funder, firm, contract_desc))
    if firm and ri_name:
        sentences.append(
            "%s partnered with %s on the project." % (firm, ri_name)
        )
    if firm and pi_name:
        sentences.append(
            "The principal investigator at %s was %s." % (firm, pi_name)
        )
    lead = " ".join(sentences)
    raw_text = (lead + "\n\n" + abstract).strip() if abstract else lead

    return {
        "source": "SBIR.gov (%s)" % (agency or "award"),
        "url": _award_url(award, firm, title),
        "title": title,
        "published": (
            _clean(award.get("proposal_award_date"))
            or _clean(award.get("award_year"))
        ),
        "raw_text": raw_text,
    }


def ai_articles(awards):
    """Filter `awards` to AI-related ones and convert them to article dicts."""
    return [award_to_article(a) for a in awards if is_ai_related(a)]


def load_fixture(path):
    """Load awards from a local JSON file (used for deterministic tests)."""
    import pathlib

    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
