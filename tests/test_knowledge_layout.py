"""The bridge from hypergraph facts to a deterministic, labelled picture.

`facts_to_graph` turns (head, relation, tail) facts into the (nodes, edges)
shape `kosika.layout` positions; `render_knowledge` reuses those positions and
emits an SVG whose edges carry their relation as a label, so the knowledge can
be read straight off the picture. No model, no randomness: same facts, same
digest, same SVG.
"""

from __future__ import annotations

import unittest

from kosika.knowledge_graph import Context, Fact
from kosika.knowledge_layout import facts_to_graph, render_knowledge


def _facts() -> list[Fact]:
    ctx = Context(source="test", timestamp=0)
    return [
        Fact("alice", "parent", "bob", ctx),
        Fact("bob", "parent", "carol", ctx),
        Fact("alice", "grandparent", "carol", ctx),
    ]


class FactsToGraphTests(unittest.TestCase):
    def test_facts_become_distinct_nodes_and_relation_edges(self) -> None:
        nodes, edges = facts_to_graph(_facts())
        self.assertEqual([n[0] for n in nodes], ["alice", "bob", "carol"])
        # layout.py's edge is (source, target, kind): head -> tail, labelled by
        # the relation, so compute_layout can position it directly.
        self.assertEqual(
            sorted(edges),
            sorted(
                [
                    ("alice", "bob", "parent"),
                    ("bob", "carol", "parent"),
                    ("alice", "carol", "grandparent"),
                ]
            ),
        )

    def test_duplicate_triples_collapse_to_one_edge(self) -> None:
        ctx = Context(source="test", timestamp=0)
        facts = [Fact("alice", "parent", "bob", ctx), Fact("alice", "parent", "bob", ctx)]
        nodes, edges = facts_to_graph(facts)
        self.assertEqual(len(edges), 1)
        self.assertEqual([n[0] for n in nodes], ["alice", "bob"])

    def test_insertion_order_does_not_change_the_graph(self) -> None:
        forwards = facts_to_graph(_facts())
        backwards = facts_to_graph(list(reversed(_facts())))
        self.assertEqual(forwards, backwards)


class RenderKnowledgeTests(unittest.TestCase):
    def test_digest_and_svg_are_stable_across_runs(self) -> None:
        first = render_knowledge(_facts())
        second = render_knowledge(_facts())
        self.assertTrue(first["digest"])
        self.assertEqual(first["digest"], second["digest"])
        self.assertEqual(first["svg"], second["svg"])

    def test_svg_shows_entities_and_labels_each_edge_with_its_relation(self) -> None:
        svg = render_knowledge(_facts())["svg"]
        self.assertIn("<svg", svg)
        for entity in ("alice", "bob", "carol"):
            self.assertIn(f">{entity}</text>", svg)
        # The relation is written on the edge, not merely a substring of another.
        self.assertIn(">parent</text>", svg)
        self.assertIn(">grandparent</text>", svg)

    def test_empty_facts_render_an_empty_picture_without_crashing(self) -> None:
        result = render_knowledge([])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["nodes"], [])
        self.assertEqual(result["edges"], [])
        self.assertIn("<svg", result["svg"])

    def test_html_page_wraps_the_svg_and_the_title(self) -> None:
        result = render_knowledge(_facts(), title="Family maths")
        self.assertIn("<!doctype html>", result["html"].lower())
        self.assertIn("Family maths", result["html"])
        self.assertIn(result["svg"], result["html"])

    def test_count_and_strategy_are_reported(self) -> None:
        result = render_knowledge(_facts())
        self.assertEqual(result["count"], 3)
        self.assertIn(result["strategy"], ("layered", "circular", "grid", "force"))


if __name__ == "__main__":
    unittest.main()