"""Source feed definitions for the AI / national-security landscape.

Feeds are split into two groups kept at roughly a 2:1 ratio — national
defense / military feeds to public AI feeds. Every URL has been
verified to return a parseable RSS/Atom feed.

## Per-feed AI-relevance filtering

Many of the general defense / policy feeds (Atlantic Council, ASPI
Strategist, Stimson, Just Security, …) cover Indo-Pacific posture, UN
sanctions, peacekeeping numbers, Cold War aviation history — all
valid national-security reporting but not AI. Letting those flow
unfiltered dilutes the corpus and pollutes the knowledge graph with
off-topic entities competing for attention.

Each feed entry carries an ``ai_only`` flag:
  * ``ai_only=True`` (default) — apply the shared AI lexicon
    (``ai_terms.is_ai_relevant``) to every scraped article; skip
    articles with no AI/ML/autonomy term.
  * ``ai_only=False`` — feed is already curated to AI by the source
    (MIT News - AI, IEEE Spectrum - AI, Microsoft AI Blog, …). Skip
    the filter so legitimate AI articles that happen to omit the
    keyword in the title still land.

The filtering happens inside ``pipeline.scrape_into_corpus`` after
trafilatura extracts the article body, so the regex sees the full
text not just the title.
"""

FEEDS = [
    # --- National defense / military feeds (defense AI, autonomy, military
    #     technology) — broad coverage, AI filter applied per article. ---
    {"name": "Breaking Defense", "category": "defense", "ai_only": True,
     "url": "https://breakingdefense.com/feed/"},
    {"name": "Defense One", "category": "defense", "ai_only": True,
     "url": "https://www.defenseone.com/rss/all/"},
    {"name": "DefenseScoop", "category": "defense", "ai_only": True,
     "url": "https://defensescoop.com/feed/"},
    {"name": "C4ISRNET", "category": "defense", "ai_only": True,
     "url": "https://www.c4isrnet.com/arc/outboundfeeds/rss/?outputType=xml"},
    {"name": "The War Zone", "category": "defense", "ai_only": True,
     "url": "https://www.twz.com/feed"},
    {"name": "Defense News", "category": "defense", "ai_only": True,
     "url": "https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml"},
    {"name": "Military Times", "category": "defense", "ai_only": True,
     "url": "https://www.militarytimes.com/arc/outboundfeeds/rss/?outputType=xml"},
    {"name": "War on the Rocks", "category": "defense", "ai_only": True,
     "url": "https://warontherocks.com/feed/"},

    # --- National-security policy / think-tank feeds. Broad subject mix
    #     (Indo-Pacific posture, alliance dynamics, sanctions, etc.);
    #     AI filter strongly recommended -- past sample showed ~60% of
    #     ASPI Strategist / Atlantic Council items were non-AI. ---
    {"name": "CSET (Georgetown)", "category": "defense", "ai_only": True,
     "url": "https://cset.georgetown.edu/feed/"},
    {"name": "Just Security", "category": "defense", "ai_only": True,
     "url": "https://www.justsecurity.org/feed/"},
    {"name": "ASPI Strategist", "category": "defense", "ai_only": True,
     "url": "https://www.aspistrategist.org.au/feed/"},
    {"name": "FedScoop", "category": "defense", "ai_only": True,
     "url": "https://fedscoop.com/feed/"},
    {"name": "Nextgov / FCW", "category": "defense", "ai_only": True,
     "url": "https://www.nextgov.com/rss/all/"},
    {"name": "Atlantic Council", "category": "defense", "ai_only": True,
     "url": "https://www.atlanticcouncil.org/feed/"},
    {"name": "Stimson Center", "category": "defense", "ai_only": True,
     "url": "https://www.stimson.org/feed/"},
    {"name": "DARPA", "category": "defense", "ai_only": True,
     "url": "https://www.darpa.mil/rss.xml"},

    # --- AI/defense industry blogs (primary-source company news adjacent
    #     to the defense AI stack). Palantir / NVIDIA / Microsoft AI
    #     occasionally post non-AI content (general corporate news,
    #     enterprise IT) so still gated by the AI filter. ---
    {"name": "Palantir Blog", "category": "defense", "ai_only": True,
     "url": "https://blog.palantir.com/feed/"},
    {"name": "Microsoft AI Blog", "category": "public_ai", "ai_only": False,
     "url": "https://blogs.microsoft.com/ai/feed/"},
    {"name": "NVIDIA Blog", "category": "public_ai", "ai_only": True,
     "url": "https://blogs.nvidia.com/feed/"},

    # --- Pure AI feeds. The publisher already curates by topic, so the
    #     filter is OFF (ai_only=False) -- a legitimate "interpretability
    #     research" or "diffusion model" article that doesn't repeat the
    #     word "AI" verbatim in the body still lands. ---
    {"name": "MIT News - AI", "category": "public_ai", "ai_only": False,
     "url": "https://news.mit.edu/rss/topic/artificial-intelligence2"},
    {"name": "Stanford AI Lab", "category": "public_ai", "ai_only": False,
     "url": "https://ai.stanford.edu/blog/feed.xml"},
    {"name": "OpenAI", "category": "public_ai", "ai_only": False,
     "url": "https://openai.com/news/rss.xml"},
    {"name": "Google DeepMind", "category": "public_ai", "ai_only": False,
     "url": "https://deepmind.google/blog/rss.xml"},
    {"name": "IEEE Spectrum - AI", "category": "public_ai", "ai_only": False,
     "url": "https://spectrum.ieee.org/feeds/topic/artificial-intelligence.rss"},
    {"name": "IEEE Spectrum - Robotics", "category": "public_ai", "ai_only": False,
     "url": "https://spectrum.ieee.org/feeds/topic/robotics.rss"},
    {"name": "MIT Technology Review - AI", "category": "public_ai", "ai_only": False,
     "url": "https://www.technologyreview.com/topic/artificial-intelligence/feed"},
]

# SBIR/STTR award queries — a non-RSS data source (see ailandscape/sbir.py).
# The public SBIR.gov API has no keyword search, so awards are pulled per
# agency and year and then filtered to AI-related ones. DOD-focused, to
# match the project's national-security emphasis; awarded contracts are a
# concrete, primary-source signal of where defense AI money is going.
SBIR_QUERIES = [
    {"agency": "DOD", "year": 2025, "max_records": 400},
    {"agency": "DOD", "year": 2024, "max_records": 400},
]

# DoD budget Justification Books (see ailandscape/jbooks.py). AI-related,
# R&D-focused, FY26 + FY27 only for now per the project's scope.
JBOOK_SOURCES = [
    {"url": "https://comptroller.war.gov/Budget-Materials/"
            "FY2027BudgetJustification/",
     "fiscal_year": "FY2027", "agency": "Defense-Wide"},
    {"url": "https://comptroller.war.gov/Budget-Materials/"
            "FY2026BudgetJustification/",
     "fiscal_year": "FY2026", "agency": "Defense-Wide"},
    {"url": "https://www.af.mil/Secretariat-of-the-Air-Force/"
            "Financial-Management-SAF-FM/",
     "fiscal_year": "FY2026-FY2027", "agency": "Air Force"},
]
