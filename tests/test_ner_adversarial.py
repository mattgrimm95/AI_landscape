"""Adversarial inputs for the NER + reconcile pipeline.

The positive-case NER tests (`tests/test_ner.py`) cover what we *want* to
find. This file covers what we want to *avoid* — strings shaped like
entities but coming from CSS class names, URL slugs, code blocks, math
notation, emoji, mixed-language prose, repeated punctuation, all-caps
headers, and the like. Any time NER drifts (e.g. a spaCy upgrade or a new
feed introduces an unfamiliar format) these guards should keep noise out of
the graph.

The tests run the *full* reconcile pipeline (NER → reconcile → KG store) so
they assert on the same set of nodes a real run would produce — single-word
"See" from a URL slug is emitted by NER but is dropped by the doc-frequency
filter in reconcile, and what matters for graph quality is the latter.

All tests use ``backend="rule"`` so they don't depend on spaCy and stay
deterministic on every developer machine.
"""

import os
import tempfile
import unittest

from ailandscape import ner, pipeline, reconcile, scraper
from ailandscape.storage_kg import KnowledgeGraphStore
from ailandscape.storage_ner import NEROutputLog


def _surfaces(entities):
    return {e["text"] for e in entities}


def _build_graph_for(text, title=""):
    """Run the full pipeline on a one-document corpus shaped from `text`.

    Returns ``(kg_nodes, ner_entities)`` where `kg_nodes` is the list of
    surviving knowledge-graph nodes after reconcile and `ner_entities` is
    the raw NER output before reconcile filtered it. Tests compare these
    to assert "NER may emit X, but reconcile must not let X land in the
    graph."

    The default title intentionally ends in a lowercase word ("page") so
    the rule NER's capitalized-chain bridge breaks between title and body
    — otherwise a one-word title would chain with the first capitalized
    body token into a synthetic two-word "entity" produced by the test
    harness, not by the input under test.
    """
    tmp = tempfile.mkdtemp(prefix="ail-adv-")
    article = {
        "source": "Adversarial",
        "url": "https://example.test/" + os.path.basename(tmp),
        "title": title or "Adversarial input test page",
        "published": "2026-05-23T00:00:00+00:00",
        "raw_text": text,
    }
    article["content_hash"] = scraper.content_hash(article)
    corpus_path = os.path.join(tmp, "documents.jsonl")
    from ailandscape import corpus
    corpus.append(corpus_path, pipeline.make_record(article))
    ner_log = NEROutputLog(os.path.join(tmp, "ner.db"))
    kg = KnowledgeGraphStore(os.path.join(tmp, "kg.db"))
    try:
        pipeline.rebuild(corpus_path, ner_log, kg, ner_backend="rule")
        nodes = kg.nodes()
    finally:
        ner_log.close()
        kg.close()
    ner_entities = ner.extract(text, backend="rule")
    return nodes, ner_entities


class NerAdversarialTest(unittest.TestCase):
    def assertNoSuspicious(self, text, title="", allow=()):
        """The final graph must contain no node from this adversarial input.

        `allow` lists canonical names that are permitted to land (e.g. a
        real gazetteer-trusted entity embedded inside the noise).
        """
        nodes, _ents = _build_graph_for(text, title=title)
        survivors = [
            n["canonical_name"]
            for n in nodes
            if n["canonical_name"] not in allow
        ]
        self.assertEqual(
            survivors, [],
            "Unexpected graph nodes from adversarial input: %r" % survivors,
        )

    def test_code_block_identifiers_do_not_become_entities(self):
        # CamelCase identifiers and bracketed function calls must not leak.
        text = (
            "Use ArrayBuffer or DataView to read bytes. "
            "Call processRequest(user_id) and getAuthToken()."
        )
        self.assertNoSuspicious(text)

    def test_url_slugs_do_not_become_entities(self):
        text = "See /api/v1/foo-bar/users-list for the schema."
        self.assertNoSuspicious(text)

    def test_css_class_names_do_not_become_entities(self):
        text = ".navMain__inner > .btn-primary:hover { color: #fff; }"
        self.assertNoSuspicious(text)

    def test_math_notation_does_not_become_entities(self):
        text = "Let X = A * B + C. Solve for X when A=2, B=3, C=4."
        self.assertNoSuspicious(text)

    def test_emoji_runs_do_not_become_entities(self):
        text = "Update: 🚀🚀 Big news 🔥🔥🔥 incoming! 🎉"
        self.assertNoSuspicious(text)

    def test_all_caps_headers_yield_no_pseudo_entity(self):
        text = "BREAKING NEWS: stay tuned for updates today."
        self.assertNoSuspicious(text)

    def test_mixed_language_prose_does_not_produce_boilerplate(self):
        # A snippet of Cyrillic + Latin should not produce a boilerplate-
        # shaped entity. Whatever NER emits, reconcile must reject any
        # surface containing a page-chrome token.
        text = "Россия и США сделали statement. Latin and Кириллица mix."
        nodes, _ents = _build_graph_for(text)
        for n in nodes:
            self.assertFalse(
                reconcile._is_boilerplate_entity(n["canonical_name"]),
                "Boilerplate slipped through: %r" % n["canonical_name"],
            )

    def test_extreme_phrase_length_is_bounded(self):
        # A long stretch of capitalized tokens (a malformed scrape) should
        # not yield a single huge entity that lands in the graph as a
        # multi-word "real-looking" node.
        text = " ".join(["Foo"] * 30)
        nodes, _ents = _build_graph_for(text)
        for n in nodes:
            self.assertLess(
                len(n["canonical_name"]), 200,
                "Entity name ridiculously long: %r" % n["canonical_name"],
            )

    def test_url_inside_text_does_not_leak_as_entity(self):
        text = "Visit https://example.com/path-to-thing today and tomorrow."
        self.assertNoSuspicious(text)

    def test_email_inside_text_does_not_leak_as_entity(self):
        # Bare email addresses in prose should not be promoted to entities
        # (and if they are, reconcile should strip them via _split_attributes).
        text = "Contact someone@example.org for details."
        nodes, _ents = _build_graph_for(text)
        for n in nodes:
            self.assertNotIn("@", n["canonical_name"])

    def test_boilerplate_tokens_dropped_by_reconcile(self):
        # The boilerplate filter is reconcile's first defence; verify the
        # canonical examples are still caught (regression guard if the
        # token list changes).
        for word in (
            "Website Keywords",
            "Subscribe Newsletter",
            "Authors List",
            "Contact Phone",
        ):
            self.assertTrue(
                reconcile._is_boilerplate_entity(word),
                "%r should be flagged as boilerplate" % word,
            )

    def test_short_aliases_dropped_by_reconcile(self):
        # `_is_noise` drops aliases shorter than 3 chars after normalization.
        for short in ("AB", "Z", "x"):
            alias = reconcile.normalize(short)
            if alias:  # if normalize returns "", that's also a drop signal
                self.assertTrue(
                    reconcile._is_noise(alias),
                    "%r (normalized %r) should be noise" % (short, alias),
                )

    def test_pure_digit_strings_dropped_by_reconcile(self):
        self.assertTrue(reconcile._is_noise("1234"))
        self.assertTrue(reconcile._is_noise("9999"))

    def test_real_entity_inside_quotes_survives(self):
        # Negative-case regression: the noise guards must not reject real
        # entities embedded in quoted prose.
        nodes, _ents = _build_graph_for(
            '"The Pentagon" issued a statement.'
        )
        names = {n["canonical_name"] for n in nodes}
        self.assertIn("Pentagon", names)


if __name__ == "__main__":
    unittest.main()
