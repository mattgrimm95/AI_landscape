import json
import pathlib
import tempfile
import unittest

from fastapi.testclient import TestClient

from ailandscape import config, server
from ailandscape.storage_kg import KnowledgeGraphStore
from ailandscape.storage_ner import NEROutputLog


class ServerApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = (
            config.KG_DB,
            config.NER_OUTPUT_DB,
            config.CORPUS_FILE,
            config.CORRECTIONS_FILE,
            config.RUN_HISTORY_FILE,
        )
        config.KG_DB = pathlib.Path(self.tmp) / "kg.db"
        config.NER_OUTPUT_DB = pathlib.Path(self.tmp) / "ner.db"
        config.CORPUS_FILE = pathlib.Path(self.tmp) / "documents.jsonl"
        config.CORRECTIONS_FILE = pathlib.Path(self.tmp) / "corrections.json"
        config.RUN_HISTORY_FILE = pathlib.Path(self.tmp) / "run_history.jsonl"

        kg = KnowledgeGraphStore(config.KG_DB)
        china = kg.insert_node("China", "place", mention_count=5, document_count=3)
        pentagon = kg.insert_node(
            "Pentagon", "organization", mention_count=8, document_count=4
        )
        kg.insert_alias(china, "china")
        kg.insert_alias(pentagon, "pentagon")
        kg.insert_alias(pentagon, "dod")
        kg.insert_edge(china, pentagon, "co_occurs_with", 4)
        kg.insert_edge(
            pentagon, china, "awards_contract", 2,
            metadata={"evidence": "the Pentagon awarded a major contract",
                      "source": "hashA"},
        )
        kg.insert_node_documents(china, ["hashA", "hashB"])
        kg.insert_node_documents(pentagon, ["hashA"])
        kg.commit()
        kg.close()
        NEROutputLog(config.NER_OUTPUT_DB).close()

        config.CORPUS_FILE.write_text(
            "\n".join(
                json.dumps(d)
                for d in [
                    {"content_hash": "hashA", "title": "Doc A",
                     "source": "Feed A", "url": "https://ex.test/a",
                     "published": "2026-05-01",
                     "fetched_at": "2026-05-01T00:00:00+00:00",
                     "raw_text": "Body A about China and the Pentagon."},
                    {"content_hash": "hashB", "title": "Doc B",
                     "source": "Feed B", "url": "https://ex.test/b",
                     "published": "2026-05-02",
                     "fetched_at": "2026-05-02T00:00:00+00:00",
                     "raw_text": "Body B about China."},
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        self.client = TestClient(server.app)

    def tearDown(self):
        (
            config.KG_DB,
            config.NER_OUTPUT_DB,
            config.CORPUS_FILE,
            config.CORRECTIONS_FILE,
            config.RUN_HISTORY_FILE,
        ) = self._orig

    def test_graph_endpoint(self):
        resp = self.client.get("/api/graph?min_weight=1")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body["nodes"]), 2)
        # One co-occurrence edge plus one typed edge between the two nodes.
        self.assertEqual(len(body["edges"]), 2)

    def test_typed_edge_carries_evidence(self):
        edges = self.client.get("/api/graph?min_weight=1").json()["edges"]
        typed = [e for e in edges if e["relation"] == "awards_contract"]
        self.assertEqual(len(typed), 1)
        self.assertIn("awarded", typed[0]["evidence"])
        # Co-occurrence edges carry no evidence.
        cooc = [e for e in edges if e["relation"] == "co_occurs_with"]
        self.assertEqual(cooc[0]["evidence"], "")

    def test_edges_carry_strength(self):
        edges = self.client.get("/api/graph?min_weight=1").json()["edges"]
        typed = [e for e in edges if e["relation"] == "awards_contract"][0]
        self.assertEqual(typed["strength"], 1.0)
        cooc = [e for e in edges if e["relation"] == "co_occurs_with"][0]
        self.assertIn("strength", cooc)

    def test_search_endpoint(self):
        resp = self.client.get("/api/search?q=chi")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("China", [n["label"] for n in resp.json()["entities"]])

    def test_search_matches_aliases(self):
        # "dod" is an alias of Pentagon but not a substring of its name.
        body = self.client.get("/api/search?q=dod").json()
        self.assertIn("Pentagon", [n["label"] for n in body["entities"]])

    def test_search_finds_documents_by_title(self):
        body = self.client.get("/api/search?q=Doc A").json()
        self.assertIn("Doc A", [d["title"] for d in body["documents"]])

    def test_node_and_neighbors(self):
        graph = self.client.get("/api/graph?min_weight=1").json()
        china_id = next(n["id"] for n in graph["nodes"] if n["label"] == "China")
        resp = self.client.get("/api/node/%d" % china_id)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["node"]["label"], "China")
        self.assertEqual(body["neighbors"][0]["label"], "Pentagon")

    def test_node_not_found(self):
        self.assertEqual(self.client.get("/api/node/999999").status_code, 404)

    def test_node_documents_endpoint(self):
        graph = self.client.get("/api/graph?min_weight=1").json()
        china_id = next(n["id"] for n in graph["nodes"] if n["label"] == "China")
        body = self.client.get("/api/node/%d/documents" % china_id).json()
        self.assertEqual(body["total"], 2)
        self.assertEqual({d["title"] for d in body["documents"]},
                         {"Doc A", "Doc B"})
        # Most-recent-first by fetched_at.
        self.assertEqual(body["documents"][0]["title"], "Doc B")
        # The endpoint also returns a per-month activity timeline.
        self.assertIn("timeline", body)

    def test_trends_endpoint(self):
        body = self.client.get("/api/trends").json()
        self.assertIn("document_volume", body)
        self.assertIn("new_entities", body)
        self.assertIn("recent_entities", body)

    def test_path_endpoint_finds_connection(self):
        res = self.client.get("/api/path?from=China&to=Pentagon").json()
        self.assertTrue(res["found"])
        self.assertEqual(res["from"]["label"], "China")
        self.assertEqual(res["to"]["label"], "Pentagon")
        self.assertEqual(len(res["nodes"]), len(res["edges"]) + 1)

    def test_path_endpoint_unknown_entity_404(self):
        resp = self.client.get("/api/path?from=Nonexistent&to=China")
        self.assertEqual(resp.status_code, 404)

    def test_types_endpoint(self):
        types = {t["type"]: t["count"] for t in self.client.get("/api/types").json()["types"]}
        self.assertEqual(types.get("place"), 1)
        self.assertEqual(types.get("organization"), 1)

    def test_correct_endpoint_writes_corrections(self):
        resp = self.client.post(
            "/api/correct", json={"action": "ignore", "terms": ["Pentagon"]}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["applied"])
        self.assertTrue(config.CORRECTIONS_FILE.exists())

    def test_correct_rejects_bad_action(self):
        resp = self.client.post(
            "/api/correct", json={"action": "delete", "terms": ["X"]}
        )
        self.assertEqual(resp.status_code, 400)

    def test_frontend_is_served(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("AI Landscape", resp.text)


if __name__ == "__main__":
    unittest.main()
