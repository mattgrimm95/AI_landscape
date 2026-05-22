"""DoD budget Justification Books (J-Books) — a non-RSS data source.

Each year DoD submits Justification Books to Congress detailing funding by
program element; the RDT&E ("R-1") books are the AI-relevant slice. This
module pulls AI-related R&D program elements from the FY26 / FY27 budget
materials pages (Air Force and Defense-Wide), filters them, and converts
each into a corpus document so they flow through the rest of the pipeline.

The user's constraints (see TODO):
  * target AI-related items
  * R&D items primarily
  * FY26 and FY27 only for now

`pypdf` is an optional dependency used only to extract text from a fetched
PDF. Without it the live-fetch path raises JBookError and the rest of the
pipeline continues — exactly as a single failed RSS feed or a throttled
SBIR run is tolerated.
"""

import io
import re
import urllib.error
import urllib.parse
import urllib.request

from . import config

# Reuses the SBIR / AI vocabulary — same defense-AI domain. Multi-word
# phrases match case-insensitively; the acronym set is case-sensitive.
_AI_TERMS = re.compile(
    r"\b("
    r"artificial intelligence|machine learning|deep learning|"
    r"neural net(work)?|reinforcement learning|generative ai|"
    r"large language model|foundation model|computer vision|"
    r"machine vision|natural language processing|"
    r"autonomy|autonomous systems|autonomous weapons|"
    r"robotics?|drone swarm|swarm intelligence|"
    r"sensor fusion|edge ai|edge computing|"
    r"data analytics|predictive analytics|anomaly detection|"
    r"electronic warfare|cyber warfare"
    r")\b",
    re.IGNORECASE,
)
_AI_ACRONYMS = re.compile(r"\b(AI|ML|LLM|NLP|ISR|SLAM)\b")

# Page-level filter: the document must read as RDT&E or research/development.
_RDTE_TERMS = re.compile(
    r"\b(RDT&E|RDTE|Research[, ]+Development|R&D|"
    r"Research and Development|Test and Evaluation)\b",
    re.IGNORECASE,
)

# Cap projects (corpus docs) added per run so a single source cannot dominate.
MAX_JBOOK_PROJECTS = 80

# Per-PDF cap when crawling an index page so a run terminates predictably.
DEFAULT_MAX_PDFS = 8


class JBookError(Exception):
    """Raised when J-books cannot be fetched or parsed."""


def is_ai_related(text):
    """True if J-book text mentions AI or ML."""
    return bool(_AI_TERMS.search(text) or _AI_ACRONYMS.search(text))


def is_rdte(text):
    """True if the document reads as R&D / RDT&E content."""
    return bool(_RDTE_TERMS.search(text))


# Program elements in an R-1 book are usually preceded by an "Exhibit R-2"
# header or a "Program Element <PE#> | <Title>" line. The split is best-
# effort: we keep whatever heading we can find.
_PE_HEADER = re.compile(
    r"(Exhibit\s+R[-‐–—]\d[A-Za-z]?[^\n]*"
    r"|Program\s+Element\s*(?:Number|#)?\s*[:\-]?\s*"
    r"\d{7}[A-Z][A-Z0-9]*\s*[^\n]*)",
    re.IGNORECASE,
)


def extract_program_elements(text):
    """Split a J-book's text into per-program-element chunks.

    Returns a list of {title, body}. Falls back to a single whole-document
    chunk if no program-element headers can be found, so a non-standard book
    still produces something useful.
    """
    headers = list(_PE_HEADER.finditer(text))
    if not headers:
        return [{"title": "Justification Book", "body": text}]
    chunks = []
    for i, match in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        title = " ".join(match.group(1).split())[:160]
        chunks.append({"title": title, "body": text[match.start():end].strip()})
    return chunks


def pe_to_article(chunk, source_url, fiscal_year, agency):
    """Convert one program-element chunk into a scraper-style article dict."""
    return {
        "source": "J-Book (%s %s)" % (agency, fiscal_year),
        "url": source_url,
        "title": chunk["title"],
        "published": fiscal_year,
        "raw_text": chunk["body"][:8000],
        "metadata": {
            "data_source": "J-Book",
            "fiscal_year": fiscal_year,
            "agency": agency,
        },
    }


def ai_articles(pdf_text, source_url, fiscal_year, agency):
    """Return AI-related R&D program-element articles from a J-book's text.

    Documents that do not look like R&D are skipped at the book level so
    procurement / O&M books do not pollute the corpus.
    """
    if not pdf_text or not is_rdte(pdf_text):
        return []
    return [
        pe_to_article(chunk, source_url, fiscal_year, agency)
        for chunk in extract_program_elements(pdf_text)
        if is_ai_related(chunk["body"])
    ]


def _fetch_url(url, timeout=None):
    timeout = config.HTTP_TIMEOUT * 3 if timeout is None else timeout
    request = urllib.request.Request(
        url, headers={"User-Agent": config.HTTP_USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


_PDF_LINK_RE = re.compile(r'href="([^"#]+\.pdf[^"]*)"', re.IGNORECASE)


def find_pdf_links(html, base_url):
    """Heuristically pull PDF links out of a budget-materials index page."""
    text = html.decode("utf-8", "replace") if isinstance(html, bytes) else html
    seen = set()
    out = []
    for match in _PDF_LINK_RE.finditer(text):
        url = urllib.parse.urljoin(base_url, match.group(1))
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _extract_pdf_text(pdf_bytes):
    """Extract plain text from a PDF using pypdf (optional dependency)."""
    try:
        import pypdf
    except ImportError as exc:
        raise JBookError(
            "pypdf is required for J-Books; install it with `pip install pypdf`"
        ) from exc
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    except Exception as exc:
        raise JBookError("could not parse PDF: %s" % exc) from exc
    parts = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n".join(parts)


def fetch_jbook_articles(
    index_url, fiscal_year, agency, max_pdfs=DEFAULT_MAX_PDFS, log=None
):
    """Crawl a budget-materials index page; return AI-related R&D articles.

    Best-effort: a PDF that fails to fetch or parse is skipped with a log
    line; the index-page fetch itself raises JBookError on failure so the
    caller can treat the whole source as down for the run.
    """
    log = log or (lambda *_a: None)
    try:
        html = _fetch_url(index_url)
    except (urllib.error.URLError, OSError) as exc:
        raise JBookError("could not fetch %s: %s" % (index_url, exc)) from exc
    links = find_pdf_links(html, index_url)
    if not links:
        return []
    articles = []
    for url in links[:max_pdfs]:
        try:
            data = _fetch_url(url)
            text = _extract_pdf_text(data)
        except (JBookError, urllib.error.URLError, OSError) as exc:
            log("WARN J-book PDF skipped (%s): %s" % (url, exc))
            continue
        articles.extend(ai_articles(text, url, fiscal_year, agency))
        if len(articles) >= MAX_JBOOK_PROJECTS:
            break
    return articles[:MAX_JBOOK_PROJECTS]
