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
        kg.insert_edge(china, pentagon, "co_occurs_with", 4)
        kg.commit()
        kg.close()
        NEROutputLog(config.NER_OUTPUT_DB).close()

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
        self.assertEqual(len(body["edges"]), 1)

    def test_search_endpoint(self):
        resp = self.client.get("/api/search?q=chi")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("China", [n["label"] for n in resp.json()["results"]])

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
