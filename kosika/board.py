"""Boards: how the agents of one run leave messages for each other.

A program declares a board by name and by the record its messages hold:

    board desk of Remark

and uses it from workflow steps:

    posted = post remark to desk as critic
    seen   = read desk

`post` appends one message, signed with the name of a declared agent, and
gives `BoardPosted {posts: Number}`. `read` gives a record named after the
board, `DeskBoard {posts: DeskPost[]}`, where each `DeskPost` is
`{author: Text, body: Remark}`, oldest first.

A board lives for one run. In a swarm run it lives for the whole run, across
every round, which is what lets a later round read what an earlier one said:
the writer posts a draft, the critic reads it and posts a remark, and in the
next round the writer reads the remark. Nothing is kept afterwards except in
the trace, where every post and every read is recorded like any other step.

Like memory, a board is not a second execution path. `lower_board` turns each
declaration into two ordinary read-only tools, so the checker, traces,
replay, limits, the mind map, and `kosika story` already understand it.

A board needs no grant from the person running the program. It can reach
nothing outside the run: it is a list in memory that exists only while the
run does. The author of a post is whatever the step says, checked to be a
declared agent. It records who the program says spoke; it is not proof that
a particular model wrote the words.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable

from .ast import (
    AgentDecl,
    BoardDecl,
    Field,
    LiteralExpr,
    Program,
    RecordDecl,
    ToolCall,
    ToolDecl,
)

#: The agent every board step runs through. It can use the board tools only.
AGENT = "board"
SOURCE_PREFIX = "board:"
POSTED_RECORD = "BoardPosted"
MAX_POSTS = 1000


class BoardError(ValueError):
    """A board is declared or used in a way Kosika cannot honour."""


def _title(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_") if part)


def post_record(name: str) -> str:
    return f"{_title(name)}Post"


def board_record(name: str) -> str:
    return f"{_title(name)}Board"


def tool_names(name: str) -> tuple[str, str]:
    return f"post_{name}", f"read_{name}"


def supplied_names(boards: list[BoardDecl]) -> set[str]:
    """The names a module may use because it declares these boards."""

    names: set[str] = set()
    for board in boards:
        names |= {
            AGENT,
            POSTED_RECORD,
            post_record(board.name),
            board_record(board.name),
        }
        names |= set(tool_names(board.name))
    return names


def lower_board(program: Program) -> Program:
    """Add the records, tools, and agent that the declared boards stand for.

    A program with no board is returned unchanged, so it keeps its tree and
    its hash.
    """

    if not program.boards:
        return program
    known = {record.name: record for record in program.records}
    records = list(program.records)
    tools = list(program.tools)
    declared_agents = {agent.name for agent in program.agents}
    taken = (
        {tool.name for tool in program.tools}
        | declared_agents
        | {function.name for function in program.functions}
    )
    if AGENT in taken:
        raise BoardError(
            f"Board cannot be used: the name {AGENT!r} is already declared"
        )

    def add_record(name: str, fields: tuple[Field, ...]) -> None:
        existing = known.get(name)
        if existing is None:
            record = RecordDecl(name, fields)
            known[name] = record
            records.append(record)
        elif existing.fields != fields:
            raise BoardError(
                f"Board cannot be used: record {name!r} is needed for boards "
                "and is already declared differently"
            )

    seen: set[str] = set()
    granted: list[str] = []
    for board in program.boards:
        if board.name in seen:
            raise BoardError(f"Board {board.name!r} is declared more than once")
        seen.add(board.name)
        if board.record not in {record.name for record in program.records}:
            raise BoardError(
                f"Board {board.name!r} holds unknown record {board.record!r}"
            )
        add_record(POSTED_RECORD, (Field("posts", "Number"),))
        add_record(
            post_record(board.name),
            (Field("author", "Text"), Field("body", board.record)),
        )
        add_record(
            board_record(board.name),
            (Field("posts", post_record(board.name) + "[]"),),
        )
        post, read = tool_names(board.name)
        for name in (post, read):
            if name in taken:
                raise BoardError(
                    f"Board {board.name!r} cannot be used: the name {name!r} "
                    "is already declared"
                )
        source = SOURCE_PREFIX + board.name
        tools.append(
            ToolDecl(
                post, source, "post", "observe", POSTED_RECORD, post_record(board.name)
            )
        )
        tools.append(
            ToolDecl(read, source, "read", "observe", board_record(board.name))
        )
        granted += [post, read]

    posting = {tool_names(board.name)[0]: board.name for board in program.boards}
    for workflow in program.workflows:
        for step in workflow.steps:
            calls = [step.call, step.fallback, *(call for _, call in step.branches)]
            for call in calls:
                if not isinstance(call, ToolCall) or call.tool_name not in posting:
                    continue
                author = dict(call.arguments).get("author")
                signed = author.value if isinstance(author, LiteralExpr) else None
                if signed not in declared_agents:
                    known_agents = ", ".join(sorted(declared_agents)) or "none"
                    raise BoardError(
                        f"Board {posting[call.tool_name]!r} post in step "
                        f"{step.name!r} is signed {signed!r}, which is not a "
                        f"declared agent; declared: {known_agents}"
                    )
    return replace(
        program,
        records=records,
        tools=tools,
        agents=[*program.agents, AgentDecl(AGENT, tuple(granted), (), max_steps=1)],
    )


class BoardStore:
    """The boards of one run. Nothing here outlives the run."""

    def __init__(self) -> None:
        self._posts: dict[str, list[dict[str, Any]]] = {}

    def post(self, name: str, author: str, body: Any) -> dict[str, Any]:
        posts = self._posts.setdefault(name, [])
        if len(posts) >= MAX_POSTS:
            raise RuntimeError(f"Board {name!r} is full ({MAX_POSTS} posts)")
        posts.append({"author": author, "body": body})
        return {"posts": len(posts)}

    def read(self, name: str) -> dict[str, Any]:
        return {"posts": [dict(post) for post in self._posts.get(name, [])]}


def bind_board_tools(
    program: Program, store: BoardStore
) -> dict[str, Callable[[ToolDecl, dict[str, Any]], Any]]:
    """Executors for the program's board tools, over one run's store."""

    def executor(name: str) -> Callable[[ToolDecl, dict[str, Any]], Any]:
        def run(tool: ToolDecl, arguments: dict[str, Any]) -> Any:
            if tool.operation == "read":
                return store.read(name)
            return store.post(name, arguments["author"], arguments["body"])

        return run

    bindings = {}
    for board in program.boards:
        for tool_name in tool_names(board.name):
            bindings[tool_name] = executor(board.name)
    return bindings
