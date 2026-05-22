import importlib.util
import os
import tempfile
import unittest

from ailandscape import visualize

_HAS_PYVIS = importlib.util.find_spec("pyvis") is not None


def _node(nid, name, ntype, mentions=1):
    return {
        "id": nid,
        "canonical_name": name,
        "type": ntype,
        "mention_count": mentions,
        "document_count": 1,
    }


def _edge(src, dst, weight=1):
    return {"src_id": src, "dst_id": dst, "relation": "co_occurs_with", "weight": weight}


class SelectSubgraphTest(unittest.TestCase):
    def setUp(self):
        # Node 1 is a hub connected to every other node.
        self.nodes = [
            _node(1, "Pentagon", "organization", 50),
            _node(2, "China", "place", 30),
            _node(3, "Ukraine", "place", 20),
            _node(4, "F-35", "product", 10),
            _node(5, "Obscure Co", "organization", 1),
        ]
        self.edges = [
            _edge(1, 2, 9),
            _edge(1, 3, 7),
            _edge(1, 4, 5),
            _edge(1, 5, 1),
            _edge(2, 3, 4),
        ]

    def test_top_connected_selection_keeps_the_hub(self):
        nodes, _edges = visualize.select_subgraph(
            self.nodes, self.edges, max_nodes=3, min_weight=1
        )
        self.assertEqual(len(nodes), 3)
        self.assertIn(1, {n["id"] for n in nodes})

    def test_min_weight_filters_edges(self):
        _nodes, edges = visualize.select_subgraph(
            self.nodes, self.edges, max_nodes=10, min_weight=5
        )
        self.assertTrue(all(e["weight"] >= 5 for e in edges))

    def test_focus_mode_includes_neighbors(self):
        nodes, _edges = visualize.select_subgraph(
            self.nodes, self.edges, focus="China", min_weight=1, max_nodes=10
        )
        names = {n["canonical_name"] for n in nodes}
        self.assertIn("China", names)
        self.assertIn("Pentagon", names)  # China's strongest neighbor

    def test_focus_no_match_raises(self):
        with self.assertRaises(ValueError):
            visualize.select_subgraph(self.nodes, self.edges, focus="Nonexistent")

    def test_type_filter(self):
        nodes, _edges = visualize.select_subgraph(
            self.nodes, self.edges, node_type="place", max_nodes=10, min_weight=1
        )
        self.assertTrue(nodes)
        self.assertTrue(all(n["type"] == "place" for n in nodes))

    @unittest.skipUnless(_HAS_PYVIS, "pyvis not installed")
    def test_render_writes_self_contained_html(self):
        out = os.path.join(tempfile.mkdtemp(), "graph.html")
        nodes, edges = visualize.select_subgraph(
            self.nodes, self.edges, max_nodes=10, min_weight=1
        )
        visualize.render(nodes, edges, out)
        self.assertTrue(os.path.exists(out))
        with open(out, encoding="utf-8") as handle:
            self.assertIn("Pentagon", handle.read())


if __name__ == "__main__":
    unittest.main()
