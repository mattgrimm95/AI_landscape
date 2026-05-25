"""Shared AI-relevance lexicon — the single bar for "is this an AI story?".

Three places in the codebase need to decide whether a chunk of text is
about AI / ML / autonomy enough to keep:

  * ``sbir.is_ai_related`` — filters award abstracts before they enter
    the corpus.
  * ``enrich.plan_ai_signal`` — gates whole enrichment plans before they
    land in the corpus.
  * ``pipeline.scrape_into_corpus`` (per-feed) — skips off-topic articles
    from general defense / policy feeds so the corpus stays scoped.

They all import from this module so one term-list edit lifts every gate
at once. The legacy names ``sbir._AI_TERMS`` and ``sbir._AI_ACRONYMS``
are kept (re-exported) so existing call sites and tests don't break.

## Two regexes, not one

``AI_TERMS`` is case-INsensitive and matched against ``.lower()``-folded
text — it carries multi-word phrases and standalone words where false
positives from random uppercase variants are not a concern.

``AI_ACRONYMS`` is case-SENSITIVE and matched against the *original*
text — for short uppercase tokens (``AI``, ``ML``, ``LLM``, ``NLP``,
``SLAM``, ``CCA``, ``JADC2``, …) where the lowercase variant would fire
on common English words (``ai`` in *maintain* / *available*, ``ml`` in
*HTML*, ``cca`` in ``occasion``…). Forcing case-sensitivity for bare
acronyms is the entire reason these are split into two regexes.

## What's in the lexicon

The base set is the original SBIR list (core AI/ML, model families,
perception, data science, embodied-AI / robotics). On top of that we
add the **defense-AI overlap vocabulary** that surfaces consistently in
the corpus but wasn't in the original list:

  * Architecture / framing: JADC2 / CJADC2 (Joint All-Domain Command
    and Control), MUM-T (Manned-Unmanned Teaming), sensor fusion,
    multi-sensor fusion, edge inference, edge AI, decision support.
  * Adversarial / weapons systems with AI cores: collaborative combat
    aircraft (CCA), loitering munitions, counter-UAS / C-UAS, drone
    swarm, autonomous targeting, kill chain.
  * AI policy + research vocabulary: frontier model, foundation model,
    agentic AI, RAG, sovereign AI.
  * Named AI platforms that come up constantly in defense reporting:
    Maven Smart System / MSS, Lattice (Anduril), Project Replicator,
    Project Maven, AIP (Palantir's Artificial Intelligence Platform).

Why include named products? Because an article like "Marine Corps signs
Maven deal" is unambiguously about defense AI even if it doesn't use
the word "AI" anywhere — Maven IS the AI. Same for Lattice.

## What's intentionally NOT in the lexicon

  * Plain "electronic warfare" — too broad; spans far beyond AI.
  * Plain "ISR" — most ISR isn't AI-enabled. (Articles about
    AI-enabled ISR will hit other terms.)
  * Plain "drone" — most drone coverage is about platform, not AI.
    (Drone swarms / autonomous drones / drone wall hit other terms.)

The principle: terms must be diagnostic of AI involvement on their own.
A term that catches too many non-AI articles is worse than missing one
AI article, because corpus dilution shows up in the graph as off-topic
entities competing for attention.
"""

import re


# Case-INsensitive phrase + word patterns. Matched against text.lower().
AI_TERMS = re.compile(
    r"\b("
    # --- core AI / machine learning ---
    r"artificial intelligence|machine intelligence|machine learning|"
    r"deep learning|deep neural|neural net(work)?|"
    r"reinforcement learning|supervised learning|unsupervised learning|"
    r"transfer learning|federated learning|"
    # --- model families / generative ---
    r"generative (ai|model|adversarial)|language model|foundation model|"
    r"transformer model|diffusion model|frontier model|"
    r"ml model|ai/ml|ai-(enabled|powered|driven|first|native|ready)|"
    r"ml-(based|powered|driven)|"
    r"agentic ai|agentic system|retrieval augmented|sovereign ai|"
    # --- perception / language / signal processing ---
    r"computer vision|machine vision|image (recognition|classification)|"
    r"object (detection|recognition)|scene understanding|"
    r"speech recognition|natural language|pattern recognition|"
    r"anomaly detection|"
    # --- sensor + data fusion (heavily AI-enabled in defense use) ---
    r"sensor fusion|multi[- ]?sensor fusion|data fusion|"
    r"intelligence fusion|battlefield fusion|"
    # --- data science / analytics ---
    r"data science|data analytics|data mining|predictive analytics|"
    r"predictive model|"
    # --- embodied AI / robotics ---
    r"robots?|robotics?|embodied (ai|intelligence|agent)|"
    r"imitation learning|motion planning|path planning|"
    r"dexterous manipulation|visual servoing|sensorimotor|"
    r"sim-to-real|sim2real|simultaneous localization|quadruped|"
    # --- defense-AI overlap: autonomy + named platforms + airframe ---
    r"autonom(y|ous)|cognitive computing|expert system|"
    r"collaborative combat aircraft|loitering munition|"
    r"counter[- ]?uas|drone swarm|autonomous targeting|"
    r"manned[- ]unmanned teaming|kill chain|"
    r"maven smart system|project maven|lattice (os|platform)|"
    r"project replicator|replicator initiative|replicator program|"
    r"palantir aip|artificial intelligence platform|"
    # --- edge / inference vocabulary ---
    r"edge (ai|inference|computing)|on[- ]device inference"
    r")\b",
    re.IGNORECASE,
)


# Case-SENSITIVE acronyms. Matched against the *original* text so they
# never fire on lowercase fragments inside ordinary words ("ai" in
# "maintain", "ml" in "HTML", "cca" in "occasion"). SLAM is Simultaneous
# Localization And Mapping (core embodied-AI / robotics technique).
# JADC2 / CJADC2 are Joint All-Domain Command and Control (the
# heavily-AI-enabled DoD networked-targeting architecture).
# MUM-T is Manned-Unmanned Teaming (drone wingmen).
# C-UAS is Counter-Unmanned Aerial Systems (often AI-based detection).
AI_ACRONYMS = re.compile(
    r"\b(AI|ML|LLM|NLP|SLAM|JADC2|CJADC2|MUM-T|C-UAS|CCA|RAG|AGI|"
    r"AIP|MSS)\b"
)


def is_ai_relevant(text):
    """True if ``text`` contains an AI / ML / autonomy term.

    Combines the case-insensitive ``AI_TERMS`` regex (run against the
    folded text) with the case-sensitive ``AI_ACRONYMS`` regex (run
    against the original). Empty / None text returns False.
    """
    if not text:
        return False
    s = str(text)
    return bool(AI_TERMS.search(s.lower()) or AI_ACRONYMS.search(s))


def ai_terms_in(text):
    """Distinct AI term hits in ``text``, for diagnostic logging."""
    if not text:
        return []
    s = str(text)
    found = set()
    for m in AI_TERMS.finditer(s.lower()):
        found.add(m.group(0))
    for m in AI_ACRONYMS.finditer(s):
        found.add(m.group(0))
    return sorted(found)
