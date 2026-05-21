# App Plan

## Goal

The essential starting goal is to create an accurate and detailed knowledge
graph of the national security landscape by scraping websites.

## Flow

1. **Scrape** web pages from AI feeds (e.g. The War Zone, Breaking Defense,
   Military Times).
2. **Named entity recognition & data creation** — extract entities and
   structured data from the scraped pages.
3. **Database log** — record the extracted data.
4. **Filter / de-duplicate / reconcile / relationship links** — clean the data
   and establish links between records.
5. **Knowledge graph database** — store nodes, relationships, and metadata.
6. **Manual corrections** — apply human corrections as desired.
7. **Artifact creation & extended features** — build outputs and additional
   capabilities on top of the graph.
