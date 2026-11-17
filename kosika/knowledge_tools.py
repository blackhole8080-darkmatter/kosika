"""The `knowledge` tool: a .kos program reads its own hypergraph with KL.

A program asks to look at what it knows by declaring a tool with a `system`
source of `knowledge`:

    record KlQuery { query: Text }
    record KlResult { columns: Text[] rows: Text[][] count: Number ok: Bool problem: Text }

    tool kl_query {
      source system "knowledge"
      operation: "query"           // the only operation: it only looks
      access: observe
      input: KlQuery
      output: KlResult
    }

Like the `kosika` check tool, it reaches nothing outside the process: it
reads the active knowledge graph (kosika.knowledge_graph) with a KL query
(kosika.kl), so it needs no folder grant and no approval. `query` is the
text of a KL query, such as
"(query (where (match ?agent has-skill rust)))". The answer is a grid:
`columns` are the bound variables, sorted; `rows` are the binding sets
aligned to those columns; `count` is how many there were. A query that does
not parse, or a host with no active graph, comes back `ok: false` with a
`problem` rather than raising.

This tool only reads. Asserting new facts or running inference changes the
graph, so it is a separate operation and is not offered here.
"""

from __future__ import annotations

from typing import Any, Callable

from .ast import Program, ToolDecl

#: Passed as `graph` to mean "read the host's active graph at call time".
#: None is a real, fixed answer (no graph), so it cannot be the default.
_DEFAULT = object()


def bind_knowledge_tools(
    program: Program,
    *,
    graph: Any = _DEFAULT,
) -> dict[str, Callable[[ToolDecl, dict[str, Any]], Any]]:
    """Bind the program's `knowledge` tools. They need no grant.

    By default the active graph is read from the process's knowledge base
    when the tool is called, so a program sees whatever the host has loaded.
    An explicit `graph` (including None) is fixed instead, for tests.
    """

    def run(tool: ToolDecl, arguments: dict[str, Any]) -> Any:
        from .knowledge_graph import get_knowledge_base

        active = (
            get_knowledge_base().get_active_graph() if graph is _DEFAULT else graph
        )
        return _execute(active, arguments["query"])

    return {
        tool.name: run
        for tool in program.tools
        if tool.source == "system:knowledge" and tool.operation == "query"
    }


def _execute(graph: Any, text: str) -> dict[str, Any]:
    """One KL query as a grid, or a problem when it cannot be answered."""

    if graph is None:
        return {
            "columns": [],
            "rows": [],
            "count": 0,
            "ok": False,
            "problem": "no active knowledge graph",
        }

    from .kl import KLInterpreter, KLParseError, parse_query

    try:
        results = KLInterpreter(graph).execute(parse_query(text))
    except (KLParseError, ValueError, TypeError, KeyError, IndexError) as exc:
        return {
            "columns": [],
            "rows": [],
            "count": 0,
            "ok": False,
            "problem": str(exc),
        }

    columns = sorted({key for binding in results for key in binding.bindings})
    rows = [[binding.get(column) or "" for column in columns] for binding in results]
    return {
        "columns": columns,
        "rows": rows,
        "count": len(results),
        "ok": True,
        "problem": "",
    }