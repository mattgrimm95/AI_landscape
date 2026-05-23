"""Source feed definitions for the AI / national-security landscape.

Feeds are split into two groups kept at roughly a 2:1 ratio — national
defense / military feeds to public AI feeds (8 : 4). Every URL has been
verified to return a parseable RSS/Atom feed.
"""

FEEDS = [
    # --- National defense / military feeds (defense AI, autonomy, military
    #     technology) — 8 feeds ---
    {"name": "Breaking Defense", "category": "defense",
     "url": "https://breakingdefense.com/feed/"},
    {"name": "Defense One", "category": "defense",
     "url": "https://www.defenseone.com/rss/all/"},
    {"name": "DefenseScoop", "category": "defense",
     "url": "https://defensescoop.com/feed/"},
    {"name": "C4ISRNET", "category": "defense",
     "url": "https://www.c4isrnet.com/arc/outboundfeeds/rss/?outputType=xml"},
    {"name": "The War Zone", "category": "defense",
     "url": "https://www.twz.com/feed"},
    {"name": "Defense News", "category": "defense",
     "url": "https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml"},
    {"name": "Military Times", "category": "defense",
     "url": "https://www.militarytimes.com/arc/outboundfeeds/rss/?outputType=xml"},
    {"name": "War on the Rocks", "category": "defense",
     "url": "https://warontherocks.com/feed/"},

    # --- National-security policy / think-tank feeds — added 2026-05-22
    #     to broaden coverage with policy analysis adjacent to defense
    #     reporting. Each URL was probed against feedparser to confirm a
    #     parseable feed before being added. ---
    {"name": "CSET (Georgetown)", "category": "defense",
     "url": "https://cset.georgetown.edu/feed/"},
    {"name": "Just Security", "category": "defense",
     "url": "https://www.justsecurity.org/feed/"},
    {"name": "ASPI Strategist", "category": "defense",
     "url": "https://www.aspistrategist.org.au/feed/"},
    {"name": "FedScoop", "category": "defense",
     "url": "https://fedscoop.com/feed/"},
    {"name": "Nextgov / FCW", "category": "defense",
     "url": "https://www.nextgov.com/rss/all/"},
    {"name": "Atlantic Council", "category": "defense",
     "url": "https://www.atlanticcouncil.org/feed/"},
    {"name": "Stimson Center", "category": "defense",
     "url": "https://www.stimson.org/feed/"},
    {"name": "DARPA", "category": "defense",
     "url": "https://www.darpa.mil/rss.xml"},

    # --- AI/defense industry blogs (primary-source company news adjacent
    #     to the defense AI stack — explicitly added so Palantir, Anduril,
    #     NVIDIA, Microsoft, and Meta become navigable nodes rather than
    #     trace mentions). Discovered via `discover-feeds`. ---
    {"name": "Palantir Blog", "category": "defense",
     "url": "https://blog.palantir.com/feed/"},
    {"name": "Microsoft AI Blog", "category": "public_ai",
     "url": "https://blogs.microsoft.com/ai/feed/"},
    {"name": "NVIDIA Blog", "category": "public_ai",
     "url": "https://blogs.nvidia.com/feed/"},

    # --- Public AI feeds (broader AI landscape, including academic
    #     sources) — 4 feeds ---
    {"name": "MIT News - AI", "category": "public_ai",
     "url": "https://news.mit.edu/rss/topic/artificial-intelligence2"},
    {"name": "Stanford AI Lab", "category": "public_ai",
     "url": "https://ai.stanford.edu/blog/feed.xml"},
    {"name": "OpenAI", "category": "public_ai",
     "url": "https://openai.com/news/rss.xml"},
    {"name": "Google DeepMind", "category": "public_ai",
     "url": "https://deepmind.google/blog/rss.xml"},

    # --- AI-focused tech press, biased toward the deeper / longer-form
    #     end. IEEE Spectrum's robotics + AI verticals are deep coverage
    #     of the autonomy stack; MIT Tech Review's AI vertical is the
    #     bridge from research to mainstream. ---
    {"name": "IEEE Spectrum - AI", "category": "public_ai",
     "url": "https://spectrum.ieee.org/feeds/topic/artificial-intelligence.rss"},
    {"name": "IEEE Spectrum - Robotics", "category": "public_ai",
     "url": "https://spectrum.ieee.org/feeds/topic/robotics.rss"},
    {"name": "MIT Technology Review - AI", "category": "public_ai",
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
