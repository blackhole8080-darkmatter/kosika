"""The `knowledge` tool: a .kos program runs a KL query over the hypergraph.

Read-only and grant-free, like the `kosika` check tool: it reaches only the
process's own knowledge graph, so a program that declares it may `observe`.
"""

from __future__ import annotations

import unittest

from kosika.knowledge_graph import Context, Fact, Hyperedge, KnowledgeGraph
from kosika.knowledge_tools import bind_knowledge_tools
from kosika.parser import parse
from kosika.system_tools import bind_system_tools, declaration_problem

DECLARED = """
record KlQuery { query: Text }
record KlResult { columns: Text[] rows: Text[][] count: Number ok: Bool problem: Text }

tool kl_query {
  source system "knowledge"
  operation: "query"
  access: observe
  input: KlQuery
  output: KlResult
}
"""


def _graph() -> KnowledgeGraph:
    graph = KnowledgeGraph()
    ctx = Context(source="test", timestamp=0)
    graph.add_hyperedge(
        Hyperedge(
            id="e1",
            facts=[
                Fact("alice", "has-skill", "rust", ctx),
                Fact("carol", "has-skill", "rust", ctx),
                Fact("alice", "knows", "bob", ctx),
            ],
            context=ctx,
        )
    )
    return graph


class KlToolTests(unittest.TestCase):
    def test_the_declaration_only_looks_and_needs_no_grant(self) -> None:
        program = parse(DECLARED)
        self.assertIsNone(declaration_problem(program, program.tools[0]))
        # Bound with nothing granted at all, like the kosika check tool.
        self.assertIn("kl_query", bind_system_tools(program, folder=None))
        bad = parse(DECLARED.replace('operation: "query"', 'operation: "assert"'))
        self.assertIn(
            "use one of: query", declaration_problem(bad, bad.tools[0]) or ""
        )

    def test_a_query_returns_bindings_as_a_grid(self) -> None:
        program = parse(DECLARED)
        run = bind_knowledge_tools(program, graph=_graph())["kl_query"]
        result = run(
            program.tools[0],
            {"query": "(query (where (match ?agent has-skill rust)))"},
        )
        self.assertTrue(result["ok"], result["problem"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["columns"], ["?agent"])
        self.assertEqual(sorted(row[0] for row in result["rows"]), ["alice", "carol"])

    def test_a_join_over_two_relations(self) -> None:
        program = parse(DECLARED)
        run = bind_knowledge_tools(program, graph=_graph())["kl_query"]
        result = run(
            program.tools[0],
            {
                "query": "(query (where (and (match ?agent has-skill rust) "
                "(match ?agent knows ?friend))))"
            },
        )
        self.assertTrue(result["ok"], result["problem"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["columns"], ["?agent", "?friend"])
        self.assertEqual(result["rows"], [["alice", "bob"]])

    def test_a_bad_query_reports_a_problem_without_crashing(self) -> None:
        program = parse(DECLARED)
        run = bind_knowledge_tools(program, graph=_graph())["kl_query"]
        result = run(program.tools[0], {"query": "(query (where (bogus ?x)))"})
        self.assertFalse(result["ok"])
        self.assertTrue(result["problem"])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["rows"], [])

    def test_no_active_graph_is_graceful(self) -> None:
        program = parse(DECLARED)
        run = bind_knowledge_tools(program, graph=None)["kl_query"]
        result = run(program.tools[0], {"query": "(query (where (match ?x r ?y)))"})
        self.assertFalse(result["ok"])
        self.assertIn("graph", result["problem"].lower())

    def test_an_empty_graph_returns_no_rows(self) -> None:
        program = parse(DECLARED)
        run = bind_knowledge_tools(program, graph=KnowledgeGraph())["kl_query"]
        result = run(program.tools[0], {"query": "(query (where (match ?x r ?y)))"})
        self.assertTrue(result["ok"], result["problem"])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["rows"], [])

    def test_relation_and_tail_filter_excludes_other_tails(self) -> None:
        # Same relation, different tails: an unbound head must not let a
        # fact with the wrong tail leak in (query_facts narrows by relation
        # only, so the tail has to be re-checked against each candidate).
        graph = KnowledgeGraph()
        ctx = Context(source="test", timestamp=0)
        graph.add_hyperedge(
            Hyperedge(
                id="e",
                context=ctx,
                facts=[
                    Fact("sin", "is-a", "trig-function", ctx),
                    Fact("pythagoras", "is-a", "theorem", ctx),
                ],
            )
        )
        program = parse(DECLARED)
        run = bind_knowledge_tools(program, graph=graph)["kl_query"]
        result = run(
            program.tools[0],
            {"query": "(query (where (match ?x is-a trig-function)))"},
        )
        self.assertTrue(result["ok"], result["problem"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["rows"], [["sin"]])

    def test_head_and_tail_filter_excludes_other_tails(self) -> None:
        # Unbound relation, bound head and tail: a fact with the right head
        # but the wrong tail must not leak in either.
        graph = KnowledgeGraph()
        ctx = Context(source="test", timestamp=0)
        graph.add_hyperedge(
            Hyperedge(
                id="e",
                context=ctx,
                facts=[
                    Fact("alice", "knows", "bob", ctx),
                    Fact("alice", "likes", "bob", ctx),
                    Fact("alice", "knows", "carol", ctx),
                ],
            )
        )
        program = parse(DECLARED)
        run = bind_knowledge_tools(program, graph=graph)["kl_query"]
        result = run(
            program.tools[0],
            {"query": "(query (where (match alice ?r bob)))"},
        )
        self.assertTrue(result["ok"], result["problem"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(sorted(row[0] for row in result["rows"]), ["knows", "likes"])


if __name__ == "__main__":
    unittest.main()