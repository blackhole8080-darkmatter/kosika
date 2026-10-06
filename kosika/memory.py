"""Memory: facts a program keeps between runs.

A program declares a memory by name and by the record it holds:

    memory customers of CustomerNote

and uses it from workflow steps:

    saved = remember note in customers under ticket.customer
    known = recall customers under ticket.customer

`recall` gives a record with `found: Bool` and `value: CustomerNote?`.
`remember` replaces what was stored under that key and gives
`{key: Text, entries: Number}`.

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


def supplied_names(memories: list[MemoryDecl]) -> set[str]:
    """The names a module may use because it declares these memories."""

    names: set[str] = set()
    for memory in memories:
        names |= {AGENT, KEY_RECORD, SAVED_RECORD}
        names |= {entry_record(memory.name), recalled_record(memory.name)}
        names |= set(tool_names(memory.name))
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
    program: Program, directory: str | Path | None, *, store: MemoryStore | None = None
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
            return kept.remember(name, arguments["key"], arguments["value"])

        return run

    bindings = {}
    for memory in program.memories:
        for tool_name in tool_names(memory.name):
            bindings[tool_name] = executor(memory.name)
    return bindings
