import importlib.util
import json
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


def _edge(src, dst, weight=1, strength=None):
    edge = {"src_id": src, "dst_id": dst, "relation": "co_occurs_with",
            "weight": weight}
    if strength is not None:
        edge["metadata"] = json.dumps({"strength": strength})
    return edge


def _typed_edge(src, dst, relation="develops", weight=1, confidence=None):
    edge = {"src_id": src, "dst_id": dst, "relation": relation, "weight": weight}
    if confidence is not None:
        edge["metadata"] = json.dumps({"confidence": confidence})
    return edge


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

    def test_default_view_leads_with_typed_relationship_nodes(self):
        # F-35 (id 4) and Obscure Co (id 5) gain a typed edge; both should be
        # pulled into a tight default view ahead of plain co-occurrence hubs.
        edges = self.edges + [_typed_edge(5, 4, "develops")]
        nodes, _e = visualize.select_subgraph(
            self.nodes, edges, max_nodes=3, min_weight=1
        )
        ids = {n["id"] for n in nodes}
        self.assertIn(4, ids)
        self.assertIn(5, ids)

    def test_focus_ranks_neighbors_by_strength(self):
        nodes = [
            _node(1, "Hub", "organization", 50),
            _node(2, "Weak Link", "place", 5),
            _node(3, "Strong Link", "place", 5),
        ]
        # Same raw weight; node 3's edge has the higher normalized strength.
        edges = [_edge(1, 2, 10, strength=0.1), _edge(1, 3, 10, strength=0.9)]
        sel, _e = visualize.select_subgraph(
            nodes, edges, focus="Hub", max_nodes=2, min_weight=1
        )
        names = {n["canonical_name"] for n in sel}
        self.assertIn("Strong Link", names)
        self.assertNotIn("Weak Link", names)

    def test_relations_only_drops_co_occurrence(self):
        edges = self.edges + [_typed_edge(2, 4, "develops")]
        nodes, sel_edges = visualize.select_subgraph(
            self.nodes, edges, max_nodes=10, min_weight=1, relations_only=True
        )
        self.assertTrue(sel_edges)
        self.assertTrue(
            all(e["relation"] != "co_occurs_with" for e in sel_edges)
        )
        # Only the two typed-edge endpoints survive.
        self.assertEqual({n["id"] for n in nodes}, {2, 4})

    def test_min_confidence_drops_low_confidence_typed_edges(self):
        edges = [
            _typed_edge(1, 4, "develops", confidence=0.9),
            _typed_edge(1, 5, "develops", confidence=0.2),
        ]
        _nodes, sel = visualize.select_subgraph(
            self.nodes, edges, max_nodes=10, min_weight=1, min_confidence=0.5
        )
        # The 0.9-confidence edge is kept; the 0.2 one is dropped.
        self.assertEqual(len(sel), 1)
        self.assertEqual(sel[0]["dst_id"], 4)

    def test_min_strength_drops_weak_cooccurrence(self):
        edges = [
            _edge(2, 3, 5, strength=0.7),
            _edge(2, 4, 5, strength=0.1),
        ]
        _nodes, sel = visualize.select_subgraph(
            self.nodes, edges, max_nodes=10, min_weight=1, min_strength=0.5
        )
        kept = {(e["src_id"], e["dst_id"]) for e in sel}
        self.assertIn((2, 3), kept)
        self.assertNotIn((2, 4), kept)

    def test_src_dst_type_filters_typed_edges(self):
        # An org->product develops edge and an org->place located_in edge.
        edges = [
            _typed_edge(1, 4, "develops"),       # organization -> product
            _typed_edge(1, 2, "located_in"),     # organization -> place
        ]
        _nodes, sel = visualize.select_subgraph(
            self.nodes, edges,
            max_nodes=10, min_weight=1,
            src_type="organization", dst_type="product",
        )
        relations = {(e["src_id"], e["dst_id"], e["relation"]) for e in sel}
        self.assertIn((1, 4, "develops"), relations)
        self.assertNotIn((1, 2, "located_in"), relations)

    def test_type_filter(self):
        nodes, _edges = visualize.select_subgraph(
            self.nodes, self.edges, node_type="place", max_nodes=10, min_weight=1
        )
        self.assertTrue(nodes)
        self.assertTrue(all(n["type"] == "place" for n in nodes))

    def test_find_path_returns_shortest_route(self):
        # Nodes 4 and 5 each connect only to the hub (node 1), so the path
        # between them is 4 -> 1 -> 5.
        steps = visualize.find_path(self.nodes, self.edges, 4, 5)
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0][0], 4)
        self.assertEqual(steps[-1][1], 5)

    def test_find_path_disconnected_returns_empty(self):
        nodes = [_node(1, "A", "place"), _node(2, "B", "place")]
        self.assertEqual(visualize.find_path(nodes, [], 1, 2), [])

    def test_find_path_same_node_is_empty(self):
        self.assertEqual(visualize.find_path(self.nodes, self.edges, 1, 1), [])

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
