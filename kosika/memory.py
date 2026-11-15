"""Memory: facts a program keeps between runs.

A program declares a memory by name and by the record it holds:

    memory customers of CustomerNote

and uses it from workflow steps:

    saved = remember note in customers under ticket.customer
    known = recall customers under ticket.customer

`recall` gives a record with `found: Bool` and `value: CustomerNote?`.
`remember` replaces what was stored under that key and gives
`{key: Text, entries: Number}`.

    similar = recall customers like ticket.text

gives the entries nearest to a text, nearest first, at most 10:
`{matches: [{key, value, score}], by: Text}`. With an embedding model
granted (`--embed-model`), `score` is how near in meaning, -1 to 1, and `by`
is "meaning"; without one it is the share of their words the text and the
entry have in common, 0 to 1, and `by` is "words". An entry is read as its
key and the text in its record.

Memory is not a second execution path. `lower_memory` turns each declaration
into two ordinary tools and each step into an ordinary tool call, so the
checker, traces, replay, limits, and the mind map treat memory like any other
tool. Replay never touches the store: a trace already holds what was recalled.

Authority follows the usual rule. Declaring a memory grants nothing. The
person running the program names a folder (`kosika run --memory DIR`), and
each memory is one JSON file in it, `DIR/<name>.json`. Without that grant a
program that uses memory is refused before it starts. A program can read and
write only the memories it declares, only as the declared record, and only
inside that folder.

A memory write changes a file, yet it does not ask for approval the way an
`execute` tool does: the folder is a place the runner set aside for exactly
this, and everything written is the program's own typed record. Do not grant
a folder whose contents matter for anything else.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from .ast import AgentDecl, Field, MemoryDecl, Program, RecordDecl, ToolDecl

#: The agent every memory step runs through. It can use the memory tools only.
AGENT = "memory"
SOURCE_PREFIX = "memory:"
KEY_RECORD = "MemoryKey"
SAVED_RECORD = "MemorySaved"
TEXT_RECORD = "MemoryText"
#: The most entries `recall NAME like TEXT` gives.
MAX_LIKE = 10
#: The most entries a recall by meaning sends to the model at once; the
#: rest are picked up by later recalls, which remember.
MAX_LIKE_NEW = 400
MAX_ENTRIES = 10_000
MAX_KEY_LENGTH = 200

_SHARED = {
    KEY_RECORD: (Field("key", "Text"),),
    SAVED_RECORD: (Field("key", "Text"), Field("entries", "Number")),
}


class MemoryError_(ValueError):
    """A memory is declared or used in a way Kosika cannot honour."""


def _title(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_") if part)


def entry_record(name: str) -> str:
    return f"{_title(name)}Entry"


def recalled_record(name: str) -> str:
    return f"{_title(name)}Recalled"


def tool_names(name: str) -> tuple[str, str]:
    return f"remember_{name}", f"recall_{name}"


def like_tool(name: str) -> str:
    return f"like_{name}"


def match_record(name: str) -> str:
    return f"{_title(name)}Match"


def like_record(name: str) -> str:
    return f"{_title(name)}Like"


def _calls(program: Program) -> set[str]:
    """The tools a program's workflow steps call."""

    named: set[str] = set()
    for workflow in program.workflows:
        for step in workflow.steps:
            for call in (step.call, step.fallback, *(c for _, c in step.branches)):
                name = getattr(call, "tool_name", None)
                if name:
                    named.add(name)
    return named


def entry_text(key: str, value: Any) -> str:
    """What an entry says, for finding it by likeness: its key, and the text
    in its record, field by field in name order."""

    found = [key]

    def collect(part: Any) -> None:
        if isinstance(part, str):
            found.append(part)
        elif isinstance(part, dict):
            for name in sorted(part):
                collect(part[name])
        elif isinstance(part, list):
            for item in part:
                collect(item)

    collect(value)
    return "\n".join(found)


def supplied_names(memories: list[MemoryDecl]) -> set[str]:
    """The names a module may use because it declares these memories."""

    names: set[str] = set()
    for memory in memories:
        names |= {AGENT, KEY_RECORD, SAVED_RECORD}
        names |= {entry_record(memory.name), recalled_record(memory.name)}
        names |= set(tool_names(memory.name))
        names |= {TEXT_RECORD, like_tool(memory.name)}
        names |= {match_record(memory.name), like_record(memory.name)}
    return names


def lower_memory(program: Program) -> Program:
    """Add the records, tools, and agent that the declared memories stand for.

    A program with no memory is returned unchanged, so it keeps its tree and
    its hash.
    """

    if not program.memories:
        return program
    known = {record.name: record for record in program.records}
    records = list(program.records)
    tools = list(program.tools)
    taken = (
        {tool.name for tool in program.tools}
        | {agent.name for agent in program.agents}
        | {function.name for function in program.functions}
    )
    if AGENT in taken:
        raise MemoryError_(
            f"Memory cannot be used: the name {AGENT!r} is already declared"
        )

    def add_record(name: str, fields: tuple[Field, ...]) -> None:
        existing = known.get(name)
        if existing is None:
            record = RecordDecl(name, fields)
            known[name] = record
            records.append(record)
        elif existing.fields != fields:
            raise MemoryError_(
                f"Memory cannot be used: record {name!r} is needed for memory "
                "and is already declared differently"
            )

    seen: set[str] = set()
    granted: list[str] = []
    called = _calls(program)
    for memory in program.memories:
        if memory.name in seen:
            raise MemoryError_(f"Memory {memory.name!r} is declared more than once")
        seen.add(memory.name)
        if memory.record not in {record.name for record in program.records}:
            raise MemoryError_(
                f"Memory {memory.name!r} holds unknown record {memory.record!r}"
            )
        for shared, fields in _SHARED.items():
            add_record(shared, fields)
        add_record(
            entry_record(memory.name),
            (Field("key", "Text"), Field("value", memory.record)),
        )
        add_record(
            recalled_record(memory.name),
            (Field("found", "Bool"), Field("value", memory.record + "?")),
        )
        remember, recall = tool_names(memory.name)
        for name in (remember, recall):
            if name in taken:
                raise MemoryError_(
                    f"Memory {memory.name!r} cannot be used: the name {name!r} "
                    "is already declared"
                )
        source = SOURCE_PREFIX + memory.name
        tools.append(
            ToolDecl(
                remember,
                source,
                "remember",
                "observe",
                SAVED_RECORD,
                entry_record(memory.name),
            )
        )
        tools.append(
            ToolDecl(
                recall,
                source,
                "recall",
                "observe",
                recalled_record(memory.name),
                KEY_RECORD,
            )
        )
        granted += [remember, recall]
        if like_tool(memory.name) in called:
            # Only a program that asks for likeness gets its tool, so every
            # other keeps its tree and its hash.
            if like_tool(memory.name) in taken:
                raise MemoryError_(
                    f"Memory {memory.name!r} cannot be used: the name "
                    f"{like_tool(memory.name)!r} is already declared"
                )
            add_record(TEXT_RECORD, (Field("text", "Text"),))
            add_record(
                match_record(memory.name),
                (Field("key", "Text"), Field("value", memory.record), Field("score", "Number")),
            )
            add_record(
                like_record(memory.name),
                (Field("matches", match_record(memory.name) + "[]"), Field("by", "Text")),
            )
            tools.append(
                ToolDecl(
                    like_tool(memory.name),
                    source,
                    "like",
                    "observe",
                    like_record(memory.name),
                    TEXT_RECORD,
                )
            )
            granted.append(like_tool(memory.name))
    return replace(
        program,
        records=records,
        tools=tools,
        agents=[*program.agents, AgentDecl(AGENT, tuple(granted), (), max_steps=1)],
    )


class MemoryStore:
    """The memories of one program, in a folder or only in this process."""

    def __init__(self, directory: str | Path | None = None):
        self.directory = None if directory is None else Path(directory)
        self._data: dict[str, dict[str, Any]] = {}

    def _path(self, name: str) -> Path:
        assert self.directory is not None
        return self.directory / f"{name}.json"

    def entries(self, name: str) -> dict[str, Any]:
        if name not in self._data:
            loaded: dict[str, Any] = {}
            if self.directory is not None and self._path(name).exists():
                try:
                    loaded = json.loads(self._path(name).read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise RuntimeError(
                        f"Memory {name!r} cannot be read: {exc}"
                    ) from exc
                if not isinstance(loaded, dict):
                    raise RuntimeError(f"Memory {name!r} is not a map of entries")
            self._data[name] = loaded
        return self._data[name]

    def recall(self, name: str, key: str) -> dict[str, Any]:
        entries = self.entries(name)
        return {"found": key in entries, "value": entries.get(key)}

    def like(self, name: str, text: str, meaning: Any = None) -> dict[str, Any]:
        """The entries nearest to `text`, nearest first, at most MAX_LIKE; of
        two as near, the one kept first."""

        entries = list(self.entries(name).items())
        if not entries or not text.strip():
            return {"matches": [], "by": "meaning" if meaning is not None else "words"}
        texts = [entry_text(key, value) for key, value in entries]
        if meaning is not None:
            from .embeddings import EmbeddingError

            try:
                ranked = meaning.nearest(text, texts, MAX_LIKE_NEW)
            except EmbeddingError as exc:
                raise RuntimeError(str(exc)) from exc
            by = "meaning"
        else:
            from .lessons import words_of

            asked = set(words_of(text))
            scored = []
            for position, said in enumerate(texts):
                theirs = set(words_of(said))
                union = asked | theirs
                score = len(asked & theirs) / len(union) if union else 0.0
                if score > 0:
                    scored.append((score, position))
            ranked = sorted(scored, key=lambda item: (-item[0], item[1]))
            by = "words"
        return {
            "matches": [
                {"key": entries[at][0], "value": entries[at][1], "score": round(score, 4)}
                for score, at in ranked[:MAX_LIKE]
            ],
            "by": by,
        }

    def remember(self, name: str, key: str, value: Any) -> dict[str, Any]:
        entries = self.entries(name)
        if not key or len(key) > MAX_KEY_LENGTH:
            raise RuntimeError(
                f"A memory key must have 1 to {MAX_KEY_LENGTH} characters"
            )
        if key not in entries and len(entries) >= MAX_ENTRIES:
            raise RuntimeError(f"Memory {name!r} is full ({MAX_ENTRIES} entries)")
        entries[key] = value
        if self.directory is not None:
            self.directory.mkdir(parents=True, exist_ok=True)
            # Written whole to a neighbour and swapped in, so a crash cannot
            # leave half a file where the memory was.
            handle, temporary = tempfile.mkstemp(
                dir=self.directory, prefix=f".{name}-", suffix=".tmp"
            )
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as stream:
                    json.dump(entries, stream, indent=2, sort_keys=True)
                os.replace(temporary, self._path(name))
            except OSError:
                Path(temporary).unlink(missing_ok=True)
                raise
        return {"key": key, "entries": len(entries)}


def declared(program: Program) -> list[MemoryDecl]:
    return list(program.memories)


def bind_memory_tools(
    program: Program,
    directory: str | Path | None,
    *,
    store: MemoryStore | None = None,
    meaning: Any = None,
) -> dict[str, Callable[[ToolDecl, dict[str, Any]], Any]]:
    """Executors for the program's memory tools, over one store.

    `directory=None` with no `store` binds nothing: the program's memory is
    then not granted, and a run that needs it is refused.
    """

    if not program.memories or (directory is None and store is None):
        return {}
    kept = store if store is not None else MemoryStore(directory)

    def executor(name: str) -> Callable[[ToolDecl, dict[str, Any]], Any]:
        def run(tool: ToolDecl, arguments: dict[str, Any]) -> Any:
            if tool.operation == "recall":
                return kept.recall(name, arguments["key"])
            if tool.operation == "like":
                return kept.like(name, arguments["text"], meaning)
            return kept.remember(name, arguments["key"], arguments["value"])

        return run

    declared_tools = {tool.name for tool in program.tools}
    bindings = {}
    for memory in program.memories:
        for tool_name in (*tool_names(memory.name), like_tool(memory.name)):
            # `like_NAME` is declared only where a program recalls by likeness.
            if tool_name in declared_tools:
                bindings[tool_name] = executor(memory.name)
    return bindings
