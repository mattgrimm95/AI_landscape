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
]
