"""Composable brain skills: kosika.maths (KG + KL) and kosika.memory (tiers).

These wrap the existing engines as skills on the orchestration bus. The
point is composition: maths derives a fact and hands it to memory by
routing a `remember` message, memory consolidates it on a tick, and a
`recall` brings it back. Nothing here is scripted — the answers come from
the real knowledge graph, KL interpreter, rule engine and memory stack.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import kosika.kl as kl_module
from kosika.brain_skills import load_brain_skills
from kosika.knowledge_graph import Context, Fact, Hyperedge, KnowledgeGraph
from kosika.memory_tiers import MemoryStack
from kosika.skills_framework import SkillMessage, SkillRegistry


def family_graph() -> KnowledgeGraph:
    graph = KnowledgeGraph()
    ctx = Context(source="test", timestamp=0)
    graph.add_hyperedge(
        Hyperedge(
            id="fam",
            context=ctx,
            facts=[
                Fact("alice", "parent", "bob", ctx),
                Fact("bob", "parent", "carol", ctx),
            ],
        )
    )
    return graph


GRANDPARENT_RULE = {
    "id": "gp",
    "name": "grandparent",
    "pattern": "(and (match ?x parent ?y) (match ?y parent ?z))",
    "action": {"head": "?x", "relation": "grandparent", "tail": "?z"},
}


class BrainSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = SkillRegistry()
        self.graph = family_graph()
        self.stack = MemoryStack()
        load_brain_skills(self.registry, self.graph, self.stack)

    def route(self, recipient: str, type: str, payload: dict | None = None):
        return self.registry.route(SkillMessage("host", recipient, type, payload or {}))

    def test_maths_query_answers_from_the_graph(self) -> None:
        reply = self.route(
            "kosika.maths", "query", {"query": "(query (where (match ?x parent ?y)))"}
        )
        self.assertEqual(reply.type, "result", reply.payload)
        grid = reply.payload["value"]
        self.assertTrue(grid["ok"], grid["problem"])
        self.assertEqual(grid["count"], 2)

    def test_maths_assert_adds_a_fact(self) -> None:
        reply = self.route(
            "kosika.maths",
            "assert",
            {"head": "carol", "relation": "parent", "tail": "dave"},
        )
        self.assertEqual(reply.type, "result", reply.payload)
        query = self.route(
            "kosika.maths", "query", {"query": "(query (where (match carol parent ?y)))"}
        )
        self.assertEqual(query.payload["value"]["rows"], [["dave"]])

    def test_maths_infer_derives_grandparent(self) -> None:
        # Force a distinct timestamp on every derived fact so the dedup
        # cannot accidentally collide on the clock: inference must still
        # derive one logical fact and reach a fixpoint.
        clock = iter(range(1_000_000, 2_000_000))
        with patch.object(kl_module.time, "time", lambda: next(clock)):
            first = self.route("kosika.maths", "infer", {"rules": [GRANDPARENT_RULE]})
            self.assertEqual(first.type, "result", first.payload)
            self.assertEqual(first.payload["value"]["inferred"], 1)
            again = self.route("kosika.maths", "infer", {"rules": [GRANDPARENT_RULE]})
            self.assertEqual(again.payload["value"]["inferred"], 0)
        query = self.route(
            "kosika.maths",
            "query",
            {"query": "(query (where (match ?x grandparent ?y)))"},
        )
        self.assertEqual(query.payload["value"]["rows"], [["alice", "carol"]])

    def test_maths_visualize_renders_the_solved_knowledge_with_labels(self) -> None:
        clock = iter(range(1_000_000, 2_000_000))
        with patch.object(kl_module.time, "time", lambda: next(clock)):
            self.route("kosika.maths", "infer", {"rules": [GRANDPARENT_RULE]})
        reply = self.route(
            "kosika.maths", "visualize", {"relation": "grandparent", "title": "Derived"}
        )
        self.assertEqual(reply.type, "result", reply.payload)
        picture = reply.payload["value"]
        self.assertEqual(picture["count"], 1)
        self.assertTrue(picture["digest"])
        self.assertIn(">grandparent</text>", picture["svg"])
        self.assertIn(">alice</text>", picture["svg"])
        self.assertIn(">carol</text>", picture["svg"])
        self.assertIn("Derived", picture["html"])

    def test_memory_remembers_and_recalls(self) -> None:
        remembered = self.route(
            "kosika.memory",
            "remember",
            {"facts": [{"head": "alice", "relation": "parent", "tail": "bob"}]},
        )
        self.assertEqual(remembered.payload["value"]["remembered"], 1)
        recall = self.route("kosika.memory", "recall", {"relation": "parent"})
        self.assertEqual(recall.payload["value"]["count"], 1)
        self.assertEqual(recall.payload["value"]["facts"][0]["tail"], "bob")

    def test_maths_consolidate_composes_into_memory(self) -> None:
        reply = self.route("kosika.maths", "consolidate", {})
        self.assertEqual(reply.type, "result", reply.payload)
        self.assertGreaterEqual(reply.payload["value"]["remembered"], 2)
        recall = self.route("kosika.memory", "recall", {"relation": "parent"})
        self.assertEqual(recall.payload["value"]["count"], 2)

    def test_tick_all_consolidates_memory(self) -> None:
        self.route(
            "kosika.memory",
            "remember",
            {"facts": [{"head": "a", "relation": "r", "tail": "b"}]},
        )
        results = self.registry.tick_all(1.0)
        self.assertIn("kosika.memory", results)
        self.assertIn("working_to_episodic", results["kosika.memory"])

    def test_an_unknown_message_type_is_an_error_reply(self) -> None:
        reply = self.route("kosika.maths", "bogus", {})
        self.assertEqual(reply.type, "error")
        self.assertTrue(reply.payload["problem"])


if __name__ == "__main__":
    unittest.main()