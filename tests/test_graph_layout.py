import unittest

from highschoolphysics.graph_layout import layout_knowledge_graph


class GraphLayoutTests(unittest.TestCase):
    def test_layout_positions_non_demo_nodes_without_overlap(self):
        nodes = [
            {"id": "module-a", "parent_id": None, "level": 1, "name": "模块A"},
            {"id": "chapter-a", "parent_id": "module-a", "level": 2, "name": "章节A"},
            {"id": "section-a", "parent_id": "chapter-a", "level": 3, "name": "小节A"},
            {"id": "section-b", "parent_id": "chapter-a", "level": 3, "name": "小节B"},
            {"id": "chapter-b", "parent_id": "module-a", "level": 2, "name": "章节B"},
        ]
        edges = [
            {"source_node_id": "module-a", "target_node_id": "chapter-a"},
            {"source_node_id": "chapter-a", "target_node_id": "section-a"},
            {"source_node_id": "chapter-a", "target_node_id": "section-b"},
            {"source_node_id": "module-a", "target_node_id": "chapter-b"},
        ]

        layout = layout_knowledge_graph(nodes, edges)

        positions = {item["id"]: (item["x"], item["y"]) for item in layout["nodes"]}
        self.assertEqual(len(set(positions.values())), len(nodes))
        self.assertLess(positions["module-a"][0], positions["chapter-a"][0])
        self.assertLess(positions["chapter-a"][0], positions["section-a"][0])
        self.assertEqual(layout["layout"], "deterministic-layered-v1")
        self.assertGreaterEqual(layout["view_box"]["height"], 300)

    def test_layout_is_stable_regardless_of_input_order(self):
        nodes = [
            {"id": "n3", "parent_id": "n1", "level": 2, "name": "C"},
            {"id": "n1", "parent_id": None, "level": 1, "name": "A"},
            {"id": "n2", "parent_id": "n1", "level": 2, "name": "B"},
        ]
        edges = [
            {"source_node_id": "n1", "target_node_id": "n3"},
            {"source_node_id": "n1", "target_node_id": "n2"},
        ]

        first = layout_knowledge_graph(nodes, edges)
        second = layout_knowledge_graph(list(reversed(nodes)), list(reversed(edges)))

        first_positions = [(item["id"], item["x"], item["y"]) for item in first["nodes"]]
        second_positions = [(item["id"], item["x"], item["y"]) for item in second["nodes"]]
        self.assertEqual(first_positions, second_positions)


if __name__ == "__main__":
    unittest.main()
