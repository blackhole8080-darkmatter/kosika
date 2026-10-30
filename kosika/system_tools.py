"""Shell and file access for Kosika programs, behind approval every time.

A program asks for system access by declaring a tool with a `system` source:

    tool run_tests {
      source system "shell"
      operation: "python"          // the one program this tool may start
      access: execute
      input: ShellArguments        // {args: Text[]}
      output: ShellResult          // {exit_code: Number, stdout: Text, stderr: Text}
    }

    tool save_report {
      source system "files"
      operation: "write"           // read, peek, search, list, write, or create
      access: execute
      input: FileWrite             // {path: Text, text: Text}
      output: FileWritten          // {bytes: Number}
    }

A fourth kind, `source system "kosika"` with `operation: "check"`, checks a
Kosika program given as text and runs its tests. It reaches nothing outside
itself, so unlike the others it needs no grant and no approval.

A fifth kind, `source system "agents"` with `operation: "ask"`, hands a
request to another agent in the same folder and gives back what it said:

    tool hand_over {
      source system "agents"
      operation: "ask"
      access: observe
      input: Handover              // {agent: Text, text: Text}
      output: Handed               // {ok: Bool, agent: Text, text: Text}
    }

Handing over grants nothing. The other agent runs as if the person had
opened it: with its own tools, the same grants, and its own questions to
the person before anything changes. So the tool itself may be `observe`.
It is bound only by a host that has a folder of agents, such as Studio. An
empty `agent` lets the host choose; an agent cannot hand to itself, and an
agent that was handed a request cannot hand it on.

A sixth kind, `source system "outside"` with `operation: "propose"`, asks
an outside command-line agent the owner has installed and allowed, such as
devin or omp, to do something to the granted folder:

    tool ask_outside {
      source system "outside"
      operation: "propose"
      access: observe
      input: Handover              // {agent: Text, text: Text}
      output: Proposed             // {ok: Bool, agent: Text, said: Text, files: Planned[]}
    }

It may be `observe` because it changes nothing: the outside agent works in
a copy of the folder, and what it changed there comes back as `files`,
each `{path, text, before, exists}`. Writing them is a separate step that
needs approval, usually one `system "files"` tool of operation `write_all`.
What the outside agent reads is sent to the company that runs it, so the
host binds this tool only for agents the owner allowed and only when the
granted folder is inside the one root the owner named; see kosika.outside.

With `operation: "answer"` and the shape `{agent, text}` to `{ok, agent,
text}`, an outside agent is only asked a question. It is started in an
empty folder and shown no file, so the host binds it whenever the owner has
allowed an outside agent, with no folder granted. The words of the question
still go to the company that runs the agent.

One more kind, `source system "db"`, reaches a database file:

    record Note { id: Number title: Text }
    record Notes { rows: Note[] more: Bool }

    tool find_orders {
      source system "db"
      operation: "query"           // only looks, so it may be `observe`
      access: observe
      input: Statement             // {sql: Text, values: Text[]}
      output: Grid                 // {columns: Text[], rows: Text[][], more: Bool}
    }

    tool read_notes {
      source system "db"
      operation: "rows"            // only looks; the answer as the row record
      access: observe
      input: Statement
      output: Notes                // {rows: Note[], more: Bool}
    }

The person running the program names the one file (`--database notes.db`).
`query` opens it read-only and gives the rows back as a grid of text, and
`rows` gives the same statement back as the program's own records, mapping
each column by name onto the field of that name and refusing a row that does
not fit; `run` changes the database, so it is `execute` and every call needs a
yes. Only `values` are bound, a statement is stopped after 5 seconds, and the
answers replay from the trace like any other tool's. See kosika.database.

For the other kinds, three independent things must all be true before
anything happens:

1. The declaration. A system tool must have `access: execute`, so the checker
   forces every workflow that uses one to state `require approval`. The
   exceptions are the tools that cannot change anything: a files tool whose
   operation is `read` or `list`, and a db tool whose operation is `query` or
   `rows`. Those may be `access: observe`. They still need their grant (the
   folder, or the database file), and reach only inside it.
2. The host grant. Nothing here is bound unless the person running the program
   passes a folder (and, for shell tools, allows shell). A declaration alone
   reaches nothing.
3. The approval. Because the tool is `execute`, the runtime pauses before each
   call until a matching approval exists for that exact tool and arguments.

Limits that hold even after approval: a shell tool starts only the program
named in its declaration, with arguments passed as a list and never through a
shell, inside the granted folder, for at most 60 seconds. File tools touch only
paths inside the granted folder; a path that leaves it, or passes through a
symlink, is refused. This is not an operating-system sandbox: an approved
program runs with the authority of the person who approved it.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

from .ast import PLAN_FILE_FIELDS, Program, ToolDecl
from .embeddings import EmbeddingError, Meaning
from .notify import NotifyError, bind_notify_tool
from .sessions import Session, SessionError
from .web import WebError, bind_web_tool

SYSTEM_PREFIX = "system:"
#: The source a db tool declares, as the parser records it: `system "db"`.
#: Replay judges a recorded statement against it (see kosika.database).
DB_SOURCE = SYSTEM_PREFIX + "db"
SYSTEM_KINDS = (
    "shell",
    "files",
    "db",
    "web",
    "kosika",
    "meaning",
    "agents",
    "outside",
    "notify",
    "services",
    "linux",
    "computer",
)
#: The largest program text the `kosika` tool will look at.
MAX_CHECKED_SOURCE = 64 * 1024
#: How many of a checked program's tests are run.
MAX_CHECKED_TESTS = 30
FILE_OPERATIONS = (
    "read",
    "peek",
    "search",
    "find",
    "write",
    "create",
    "write_all",
    "list",
)
#: In a shape, an array of records that each have exactly the fields of a
#: planned file: {path, text, before, exists}.
PLANNED_FILES = "<planned files>"
#: In a shape, an array of records that each map the columns of a database
#: answer onto their fields: every field Text, Number or Bool, which are the
#: types a cell can be read as (see kosika.database).
TYPED_ROWS = "<typed rows>"
#: How a shape says what a typed row must be, in a refusal.
TYPED_ROW_FIELDS = "R[], where R is a record whose every field is Text, Number or Bool"
READ_ONLY_OPERATIONS = ("read", "peek", "search", "find", "list")
#: Limits on one search of a folder, so it always comes back.
MAX_SEARCH_MATCHES = 40
MAX_SEARCH_FILES = 5000
MAX_SEARCH_SECONDS = 8.0
MAX_SEARCH_LINE = 240
#: Limits on one search by meaning. Only so many lines are compared,
#: and only so many new ones are sent to the embedding model in one
#: call; the rest are picked up by later calls, which remember.
MAX_FIND_MATCHES = 12
MAX_FIND_LINES = 4000
MAX_FIND_NEW = 400
#: A line shorter than this says too little to compare meanings.
MIN_FIND_LINE = 12
#: Folders a search never walks into: tools' own stores, not writing.
_NOT_SEARCHED = {
    "node_modules",
    "__pycache__",
    "venv",
    "dist",
    "build",
    "target",
    "_trash",
}
#: How much of a file `peek` gives back.
PEEK_CHARACTERS = 4000
SHELL_TIMEOUT_SECONDS = 60
MAX_OUTPUT_CHARACTERS = 64 * 1024
MAX_FILE_BYTES = 1024 * 1024
MAX_LISTED_NAMES = 1000

#: The record fields each kind of system tool must take and return.
SHAPES: dict[str, tuple[dict[str, str], dict[str, str]]] = {
    "shell": (
        {"args": "Text[]"},
        {"exit_code": "Number", "stdout": "Text", "stderr": "Text"},
    ),
    "read": ({"path": "Text"}, {"text": "Text"}),
    # The beginning of a file. A model that chooses its own tools can look
    # at a long file without the whole of it being handed back.
    "peek": ({"path": "Text"}, {"text": "Text"}),
    # Find the lines of the text files under a folder that hold the words
    # asked for. Each match is `path:line: text`, best matches first, so an
    # answer can be tied to the exact line it came from.
    "search": ({"path": "Text", "query": "Text"}, {"matches": "Text[]"}),
    # The lines nearest in meaning to the query, in the same form as
    # `search`, nearest first. It needs an embedding model, which the
    # person running the program grants; see kosika.embeddings.
    "find": ({"path": "Text", "query": "Text"}, {"matches": "Text[]"}),
    # One statement against the database file the person named. `query`
    # only looks, through a read-only connection, and gives rows back as
    # a grid of text; `run` changes the database, so it needs approval.
    # Each cell is text, and `more` says the row limit cut the answer
    # short. See kosika.database.
    "db:query": (
        {"sql": "Text", "values": "Text[]"},
        {"columns": "Text[]", "rows": "Text[][]", "more": "Bool"},
    ),
    # The same statement, answered as the program's own records: each
    # column is mapped by name onto the field of that name in R, and a
    # row that does not fit is refused, naming the row and the column.
    # `more` says the row limit cut the answer short.
    "db:rows": (
        {"sql": "Text", "values": "Text[]"},
        {"rows": TYPED_ROWS, "more": "Bool"},
    ),
    "db:run": (
        {"sql": "Text", "values": "Text[]"},
        {"changed": "Number"},
    ),
    "write": ({"path": "Text", "text": "Text"}, {"bytes": "Number"}),
    # Like write, but refuses when anything is already at that path, so it
    # can add to a folder and never destroy what is there.
    "create": ({"path": "Text", "text": "Text"}, {"bytes": "Number"}),
    # Write several whole files in one step, so one approval covers exactly
    # that list. Each is given as a planned file; only its path and text
    # are used. If one cannot be written, the ones after it are not.
    # `note` is for the person who is asked: it is shown with the list and
    # changes nothing that is written.
    "write_all": ({"files": PLANNED_FILES, "note": "Text"}, {"count": "Number"}),
    # A second outside agent reads a proposed change, applied in a copy
    # with the old files beside it, and says what it finds wrong.
    "review": (
        {"agent": "Text", "text": "Text", "files": PLANNED_FILES},
        {"ok": "Bool", "agent": "Text", "said": "Text"},
    ),
    # An outside agent's answer: what it said, and the files it would
    # change, worked out in a copy. See kosika.outside.
    "propose": (
        {"agent": "Text", "text": "Text"},
        {"ok": "Bool", "agent": "Text", "said": "Text", "files": PLANNED_FILES},
    ),
    # An outside agent asked a question and shown no file. See
    # kosika.outside.answer.
    "answer": (
        {"agent": "Text", "text": "Text"},
        {"ok": "Bool", "agent": "Text", "text": "Text"},
    ),
    "list": ({"path": "Text"}, {"names": "Text[]"}),
    # A Kosika program, given as text, is checked and its own tests are run.
    # `problem` is empty when it checks and every test passes; otherwise it
    # is the first thing wrong, in the checker's own words.
    "check": (
        {"source": "Text"},
        {"ok": "Bool", "problem": "Text", "tests": "Number", "passed": "Number"},
    ),
    # The engine's own reading of files a program means to write: what is
    # not valid Python or Kosika, a name never defined, a Kosika test that
    # fails. It reaches nothing, like `check`.
    # `text` is what was asked for: it chooses the skills whose rules the
    # files are held to, when the host has skills for the program.
    "check_files": (
        {"text": "Text", "files": PLANNED_FILES},
        {"problems": "Text[]"},
    ),
    # How near two texts are in meaning, from -1 to 1. It reaches nothing
    # but the embedding model the runner granted.
    "near": ({"a": "Text", "b": "Text"}, {"score": "Number"}),
    # A web tool reads one page from a site the runner allowed; see kosika.web.
    "fetch": ({"url": "Text"}, {"url": "Text", "title": "Text", "text": "Text"}),
    # The rest of a long page, a part at a time: `next` is where the part
    # after this one begins, or -1 when this one ends the page.
    "fetch_from": (
        {"url": "Text", "start": "Number"},
        {"url": "Text", "title": "Text", "text": "Text", "next": "Number"},
    ),
    # A note sent to the one address the person named; see kosika.notify.
    # `link` is the address tapping the note opens ("" for none), and
    # `file_name` with `file` is one attached text page ("" for a plain
    # note).
    "send": (
        {
            "title": "Text",
            "text": "Text",
            "link": "Text",
            "file_name": "Text",
            "file": "Text",
        },
        {"sent": "Bool"},
    ),
    # The owner's services (see kosika.services): their names, which are
    # paused, and how each last ran, as the runner last wrote it. Pausing
    # and resuming change only the paused list, which the runner reads.
    "status": ({}, {"services": "Text[]", "paused": "Text[]", "text": "Text"}),
    "pause": ({"name": "Text"}, {"name": "Text", "paused": "Bool"}),
    "resume": ({"name": "Text"}, {"name": "Text", "paused": "Bool"}),
    # How the Linux the person named is doing, read by fixed commands that
    # change nothing (see kosika.wsl). Kept by kind, as `status` is taken.
    "linux:status": (
        {},
        {
            "distro": "Text",
            "kernel": "Text",
            "uptime": "Text",
            "load": "Text",
            "memory": "Text",
            "disk": "Text",
            "upgrades": "Text",
            "top": "Text[]",
        },
    ),
    # How this computer is doing, as its system tells it. Only looks.
    "computer:status": (
        {},
        {
            "name": "Text",
            "system": "Text",
            "cores": "Number",
            "memory": "Text",
            "disk": "Text",
            "uptime": "Text",
        },
    ),
    # A request handed to another agent in the same folder. `agent` may be
    # empty, which lets the host choose. `ok` is false, with the reason in
    # `text`, when nobody could be asked or the other agent did not finish.
    "ask": (
        {"agent": "Text", "text": "Text"},
        {"ok": "Bool", "agent": "Text", "text": "Text"},
    ),
}


class SystemToolError(ValueError):
    """A system tool is declared, granted, or called in a way that is refused."""


def system_kind(tool: ToolDecl) -> str | None:
    """`shell` or `files` for a system tool, else None."""

    if not tool.source.startswith(SYSTEM_PREFIX):
        return None
    return tool.source[len(SYSTEM_PREFIX) :]


def row_fields_fit(fields: dict[str, str] | None) -> bool:
    """Whether a record can be a row of a database answer: at least one
    field, and every field one of the types a cell can be read as.

    The types are kosika.database's, which maps the cells onto them; the
    import is here so that the two cannot drift apart.
    """

    from .database import ROW_FIELD_TYPES

    if not fields:
        return False
    return all(kind in ROW_FIELD_TYPES for kind in fields.values())


def _fits(
    records: dict[str, dict[str, str]],
    actual: dict[str, str] | None,
    wanted: dict[str, str],
) -> bool:
    """Whether a record has exactly the wanted fields. A wanted type of
    PLANNED_FILES is met by an array of any record that has exactly the
    fields of a planned file, and one of TYPED_ROWS by an array of any
    record whose every field a database cell can be read as, whatever the
    program calls them."""

    if actual is None:
        # A tool that takes nothing declares no input record.
        return not wanted
    if set(actual) != set(wanted):
        return False
    for name, kind in wanted.items():
        have = actual[name]
        if kind not in (PLANNED_FILES, TYPED_ROWS):
            if have != kind:
                return False
        elif not have.endswith("[]"):
            return False
        elif kind == PLANNED_FILES:
            if records.get(have[:-2]) != PLAN_FILE_FIELDS:
                return False
        elif not row_fields_fit(records.get(have[:-2])):
            return False
    return True


def _described(kind: str, planned: str) -> str:
    """One wanted field, said in a refusal as the program must write it."""

    if kind == PLANNED_FILES:
        return f"R[], where R is a record {{{planned}}}"
    if kind == TYPED_ROWS:
        return TYPED_ROW_FIELDS
    return kind


def declaration_problem(program: Program, tool: ToolDecl) -> str | None:
    """Why a system tool's declaration is unacceptable, if it is."""

    kind = system_kind(tool)
    if kind is None:
        return None
    if kind not in SYSTEM_KINDS:
        return (
            f"Tool {tool.name!r} has unknown system source {kind!r}; "
            f"use one of: {', '.join(SYSTEM_KINDS)}"
        )
    # Looking is not changing. A files tool that only reads or lists may be
    # `observe`, which needs the folder grant but no approval for each call.
    # Writing a file and starting a program always need approval.
    looks_only = (
        (kind == "files" and tool.operation in READ_ONLY_OPERATIONS)
        or (kind == "db" and tool.operation in ("query", "rows"))
        or kind == "web"
        or kind == "kosika"
        or kind == "meaning"
        or kind == "agents"
        or kind == "outside"
        or kind == "notify"
        or (kind == "services" and tool.operation == "status")
        or kind == "linux"
        or kind == "computer"
    )
    if kind == "outside" and tool.operation not in ("propose", "review", "answer"):
        return (
            f"Tool {tool.name!r} has unknown outside operation {tool.operation!r}; "
            "use one of: propose, review, answer"
        )
    if kind == "agents" and tool.operation != "ask":
        return (
            f"Tool {tool.name!r} has unknown agents operation {tool.operation!r}; "
            "the only one is ask"
        )
    if kind == "db" and tool.operation not in ("query", "rows", "run"):
        return (
            f"Tool {tool.name!r} has unknown db operation {tool.operation!r}; "
            "use one of: query (only looks, a grid of text), rows (only looks, "
            "the program's own records), run (changes the database)"
        )
    if kind == "meaning" and tool.operation != "near":
        return (
            f"Tool {tool.name!r} has unknown meaning operation {tool.operation!r}; "
            "the only one is near"
        )
    if kind == "kosika" and tool.operation not in ("check", "check_files"):
        return (
            f"Tool {tool.name!r} has unknown kosika operation {tool.operation!r}; "
            "use one of: check, check_files"
        )
    if kind == "notify" and tool.operation != "send":
        return (
            f"Tool {tool.name!r} has unknown notify operation {tool.operation!r}; "
            "the only one is send"
        )
    if kind == "services" and tool.operation not in ("status", "pause", "resume"):
        return (
            f"Tool {tool.name!r} has unknown services operation {tool.operation!r}; "
            "use one of: status, pause, resume"
        )
    if kind in ("linux", "computer") and tool.operation != "status":
        return (
            f"Tool {tool.name!r} has unknown {kind} operation {tool.operation!r}; "
            "the only one is status"
        )
    if kind == "web" and tool.operation not in ("fetch", "fetch_from"):
        return (
            f"Tool {tool.name!r} has unknown web operation {tool.operation!r}; "
            "use one of: fetch, fetch_from"
        )
    if tool.access != "execute" and not looks_only:
        return (
            f"Tool {tool.name!r} uses system {kind} and must declare "
            "access: execute, so that every call needs approval"
        )
    if kind == "files" and tool.operation not in FILE_OPERATIONS:
        return (
            f"Tool {tool.name!r} has unknown files operation {tool.operation!r}; "
            f"use one of: {', '.join(FILE_OPERATIONS)}"
        )
    if kind == "shell" and (
        not tool.operation.strip() or " " in tool.operation.strip()
    ):
        return (
            f"Tool {tool.name!r} must name exactly one program in operation, "
            "with its arguments passed in args"
        )
    wanted_input, wanted_output = SHAPES[
        "shell"
        if kind == "shell"
        else f"{kind}:{tool.operation}"
        if f"{kind}:{tool.operation}" in SHAPES
        else tool.operation
    ]
    planned = ", ".join(f"{name}: {kind_}" for name, kind_ in PLAN_FILE_FIELDS.items())
    records = {
        record.name: {field.name: field.type_name for field in record.fields}
        for record in program.records
    }
    for label, record_name, wanted in (
        ("input", tool.input, wanted_input),
        ("output", tool.output, wanted_output),
    ):
        if not _fits(records, records.get(record_name or ""), wanted):
            shape = ", ".join(
                f"{name}: {_described(kind_, planned)}" for name, kind_ in wanted.items()
            )
            return (
                f"Tool {tool.name!r} {label} record must have exactly the "
                f"fields {{{shape}}}"
            )
    return None


def service_names(text: str) -> list[str]:
    """The services a services file names, in the order written: each
    `service NAME {` outside any braces."""

    from .lexer import LexError, lex

    try:
        tokens = lex(text)
    except LexError:
        return []
    names: list[str] = []
    depth = 0
    for index, token in enumerate(tokens):
        if token.value == "{":
            depth += 1
        elif token.value == "}":
            depth -= 1
        elif (
            depth == 0
            and token.kind == "IDENT"
            and token.value == "service"
            and index + 1 < len(tokens)
            and tokens[index + 1].kind == "IDENT"
        ):
            names.append(tokens[index + 1].value)
    return names


def bind_linux_tool(distro: str) -> Callable[[ToolDecl, dict[str, Any]], Any]:
    """How the one Linux distribution the person named (`--linux NAME`) is
    doing, for a tool with a `system "linux"` source. The commands are fixed
    in kosika.wsl and change nothing; the program chooses none of them."""

    name = distro.strip()
    if not name or any(not (c.isalnum() or c in "-_.") for c in name):
        raise SystemToolError(f"Linux distribution {distro!r} is not a plain name")

    def call(_tool: ToolDecl, _arguments: dict[str, Any]) -> dict[str, Any]:
        from .wsl import WslError, status

        try:
            found = status(name)
        except (WslError, OSError) as exc:
            raise SystemToolError(str(exc)) from exc
        return {
            "distro": str(found.get("distro", name)),
            **{
                key: str(found.get(key, "unknown"))
                for key in ("kernel", "uptime", "load", "memory", "disk", "upgrades")
            },
            "top": [str(item) for item in found.get("top", [])],
        }

    return call


def computer_status(_tool: ToolDecl, _arguments: dict[str, Any]) -> dict[str, Any]:
    """How this computer is doing, for a tool with a `system "computer"`
    source, granted with `--computer`. Only looks."""

    from .mayaos import windows

    found = windows()
    return {
        "name": str(found.get("name", "")),
        "system": str(found.get("system", "")),
        "cores": int(found.get("cores", 0) or 0),
        **{key: str(found.get(key, "unknown")) for key in ("memory", "disk", "uptime")},
    }


def bind_services_tool(path: str | Path) -> Callable[[ToolDecl, dict[str, Any]], Any]:
    """The owner's services, for a tool with a `system "services"` source,
    bound to the one services file the person running the program named
    (`--services FILE`). Status reads it and what the runner keeps beside
    it; pause and resume change only the paused list beside it, which the
    runner reads before each run. Both of those change something, so they
    are `execute` tools and each needs the owner's yes."""

    services = Path(path)
    if not services.is_file():
        raise SystemToolError(f"services file {str(path)!r} is not an existing file")
    paused_list = services.with_name(services.name + ".paused.txt")
    status_file = services.with_name(services.name + ".status.txt")

    def paused() -> list[str]:
        try:
            text = paused_list.read_text(encoding="utf-8")
        except OSError:
            return []
        return sorted(
            {
                line.strip()
                for line in text.splitlines()
                if line.strip() and not line.strip().startswith("//")
            }
        )

    def call(tool: ToolDecl, arguments: dict[str, Any]) -> dict[str, Any]:
        names = service_names(services.read_text(encoding="utf-8"))
        if tool.operation == "status":
            try:
                text = status_file.read_text(encoding="utf-8").rstrip("\n")
            except OSError:
                text = ""
            return {"services": names, "paused": paused(), "text": text}
        name = str(arguments.get("name", ""))
        if name not in names:
            raise SystemToolError(f"There is no service {name!r} in the services file")
        kept = set(paused())
        if tool.operation == "pause":
            kept.add(name)
        else:
            kept.discard(name)
        spare = paused_list.with_name(paused_list.name + ".tmp")
        spare.write_text("".join(f"{each}\n" for each in sorted(kept)), encoding="utf-8")
        spare.replace(paused_list)
        return {"name": name, "paused": tool.operation == "pause"}

    return call


def check_source(_tool: ToolDecl, arguments: dict[str, Any]) -> dict[str, Any]:
    """Check a Kosika program given as text and run the tests written in it.

    This reaches nothing. The text is put in a folder made for the purpose
    and deleted afterwards, it can import nothing, and its tests run the way
    `kosika test` runs them: scripted answers only, memories of their own,
    no real model, tool, file or network. So it needs no grant, and a
    program that writes programs can find out whether they are right.
    """

    import tempfile

    from .modules import load_program
    from .testing import run_tests

    source = arguments["source"]
    if len(source.encode("utf-8")) > MAX_CHECKED_SOURCE:
        return {
            "ok": False,
            "problem": "The program is larger than 64 KiB.",
            "tests": 0,
            "passed": 0,
        }
    with tempfile.TemporaryDirectory(prefix="kosika-check-") as scratch:
        path = Path(scratch) / "main.kos"
        path.write_text(source, encoding="utf-8")
        try:
            program = load_program(path)
        except (ValueError, TypeError, RecursionError, OSError) as exc:
            # OSError: it tried to import a file, and there are none here.
            problem = (
                str(exc).replace(str(path) + ": ", "").replace(str(path), "main.kos")
            )
            return {"ok": False, "problem": problem[:600], "tests": 0, "passed": 0}
        try:
            results = run_tests(program)[:MAX_CHECKED_TESTS]
        except (ValueError, RuntimeError, RecursionError) as exc:
            return {
                "ok": False,
                "problem": f"The tests could not be run: {str(exc)[:500]}",
                "tests": 0,
                "passed": 0,
            }
    failed = next((result for result in results if not result.passed), None)
    return {
        "ok": failed is None,
        "problem": ""
        if failed is None
        else f'Test "{failed.name}" failed at line {failed.line}: {failed.message}'[
            :600
        ],
        "tests": len(results),
        "passed": sum(1 for result in results if result.passed),
    }


def files_checker(
    skills: Any = None,
) -> Callable[[ToolDecl, dict[str, Any]], dict[str, Any]]:
    """What a `check_files` tool does: say what is wrong with files a
    program means to write.

    It is the reading a planning agent's plan is given before approval. A
    file that would be empty, or only repeats the request. Python that is
    not valid, uses a name it never defines, or imports what another of
    the files does not have. Kosika that does not check, or whose tests
    fail. And, when the host has `skills` for the program, the rules of
    the skills that fit what was asked: a stylesheet that must mention
    `prefers-reduced-motion`, say. Nothing on the machine is read or
    changed: each file's `before` is all that is known of the files as
    they are.
    """

    def check(_tool: ToolDecl, arguments: dict[str, Any]) -> dict[str, Any]:
        from .skills import pick_skills, plan_problems

        request = str(arguments.get("text", ""))
        planned = [item for item in arguments["files"] if isinstance(item, dict)]
        now = {
            str(item.get("path", "")).replace("\\", "/").removeprefix("./"): str(
                item.get("before", "")
            )
            for item in planned
            if item.get("exists") is True
        }
        picked = (
            pick_skills(skills, request) if skills is not None and len(skills) else None
        )
        return {
            "problems": plan_problems(
                picked,
                request,
                [
                    (
                        "write",
                        {
                            "path": str(item.get("path", "")),
                            "text": str(item.get("text", "")),
                        },
                    )
                    for item in planned
                ],
                now.get,
            )
        }

    return check


def bind_kosika_tools(
    program: Program,
    skills: Any = None,
) -> dict[str, Callable[[ToolDecl, dict[str, Any]], Any]]:
    """Bind the program's `kosika` tools. They need no grant; see
    check_source. `skills` are the host's skills for the program, whose
    rules `check_files` then holds files to."""

    return {
        tool.name: files_checker(skills)
        if tool.operation == "check_files"
        else check_source
        for tool in program.tools
        if system_kind(tool) == "kosika"
    }


def bind_agent_tools(
    program: Program, ask: Callable[[ToolDecl, dict[str, Any]], Any]
) -> dict[str, Callable[[ToolDecl, dict[str, Any]], Any]]:
    """Bind the program's `agents` tools to the host's way of asking
    another agent. Only a host with a folder of agents can offer one."""

    return {tool.name: ask for tool in program.tools if system_kind(tool) == "agents"}


def bind_outside_tools(
    program: Program, ask: Callable[[ToolDecl, dict[str, Any]], Any]
) -> dict[str, Callable[[ToolDecl, dict[str, Any]], Any]]:
    """Bind the program's `outside` tools to the host's way of asking an
    outside agent. A host binds them only when the owner has allowed one."""

    return {tool.name: ask for tool in program.tools if system_kind(tool) == "outside"}


def _inside(folder: Path, relative: str) -> Path:
    """Resolve `relative` inside `folder`, refusing anything that leaves it."""

    candidate = Path(relative)
    if not relative.strip() or candidate.is_absolute() or candidate.drive:
        raise SystemToolError(
            f"path {relative!r} must be relative to the granted folder"
        )
    current = folder
    for part in candidate.parts:
        if part in ("..", ""):
            raise SystemToolError(f"path {relative!r} leaves the granted folder")
        current = current / part
        if current.is_symlink():
            raise SystemToolError(f"path {relative!r} passes through a symlink")
    resolved = current.resolve()
    if resolved != folder and folder not in resolved.parents:
        raise SystemToolError(f"path {relative!r} leaves the granted folder")
    return resolved


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARACTERS:
        return text
    return text[:MAX_OUTPUT_CHARACTERS] + "\n[output cut]"


def _shell(tool: ToolDecl, folder: Path) -> Callable[[ToolDecl, dict[str, Any]], Any]:
    def run_program(_tool: ToolDecl, arguments: dict[str, Any]) -> dict[str, Any]:
        executable = shutil.which(tool.operation)
        if executable is None:
            raise SystemToolError(f"program {tool.operation!r} was not found")
        try:
            completed = subprocess.run(
                [executable, *arguments["args"]],
                cwd=folder,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=SHELL_TIMEOUT_SECONDS,
                stdin=subprocess.DEVNULL,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise SystemToolError(
                f"program {tool.operation!r} ran longer than "
                f"{SHELL_TIMEOUT_SECONDS} seconds and was stopped"
            ) from exc
        return {
            "exit_code": completed.returncode,
            "stdout": _clip(completed.stdout),
            "stderr": _clip(completed.stderr),
        }

    return run_program


def _session_shell(
    tool: ToolDecl, session: Session
) -> Callable[[ToolDecl, dict[str, Any]], Any]:
    """The program runs behind the session's wall, in its copy of the folder."""

    def run_program(_tool: ToolDecl, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            done = session.run(tool.operation, arguments["args"], SHELL_TIMEOUT_SECONDS)
        except SessionError as exc:
            raise SystemToolError(str(exc)) from exc
        return {
            "exit_code": done["exit_code"],
            "stdout": _clip(str(done["stdout"])),
            "stderr": _clip(str(done["stderr"])),
        }

    return run_program


def _lines(
    folder: Path, start: Path, deadline: float
) -> Iterator[tuple[str, int, str]]:
    """Every line of the text files under `start`: (file, number, line)."""

    seen = 0
    pending = [start]
    while pending and seen < MAX_SEARCH_FILES and time.monotonic() < deadline:
        current = pending.pop(0)
        try:
            entries = sorted(current.iterdir(), key=lambda entry: entry.name)
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith(".") or entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name not in _NOT_SEARCHED:
                    pending.append(entry)
                continue
            seen += 1
            try:
                if entry.stat().st_size > MAX_FILE_BYTES:
                    continue
                text = entry.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            name = entry.relative_to(folder).as_posix()
            for number, row in enumerate(text.splitlines(), 1):
                yield name, number, row


def _near(meaning: Meaning) -> Callable[[ToolDecl, dict[str, Any]], Any]:
    def near(_tool: ToolDecl, arguments: dict[str, Any]) -> dict[str, Any]:
        first, second = arguments["a"].strip(), arguments["b"].strip()
        if not first or not second:
            raise SystemToolError("near needs two texts to compare")
        try:
            ranked = meaning.nearest(first, [second])
        except EmbeddingError as exc:
            raise SystemToolError(str(exc)) from exc
        if not ranked:
            raise SystemToolError("the embedding model gave nothing to compare")
        return {"score": round(ranked[0][0], 4)}

    return near


def _files(
    tool: ToolDecl, folder: Path, meaning: Meaning | None = None
) -> Callable[[ToolDecl, dict[str, Any]], Any]:
    def read(_tool: ToolDecl, arguments: dict[str, Any]) -> dict[str, Any]:
        path = _inside(folder, arguments["path"])
        if not path.is_file():
            raise SystemToolError(f"{arguments['path']!r} is not a file")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise SystemToolError(f"{arguments['path']!r} is larger than 1 MiB")
        try:
            return {"text": path.read_text(encoding="utf-8")}
        except UnicodeDecodeError as exc:
            raise SystemToolError(f"{arguments['path']!r} is not UTF-8 text") from exc

    def peek(_tool: ToolDecl, arguments: dict[str, Any]) -> dict[str, Any]:
        path = _inside(folder, arguments["path"])
        if not path.is_file():
            raise SystemToolError(f"{arguments['path']!r} is not a file")
        try:
            with path.open("r", encoding="utf-8") as stream:
                text = stream.read(PEEK_CHARACTERS + 1)
        except UnicodeDecodeError as exc:
            raise SystemToolError(f"{arguments['path']!r} is not UTF-8 text") from exc
        if len(text) > PEEK_CHARACTERS:
            text = text[:PEEK_CHARACTERS] + "\n[the file goes on]"
        return {"text": text}

    def search(_tool: ToolDecl, arguments: dict[str, Any]) -> dict[str, Any]:
        start = (
            folder
            if arguments["path"] in (".", "")
            else _inside(folder, arguments["path"])
        )
        if not start.is_dir():
            raise SystemToolError(f"{arguments['path']!r} is not a folder")
        words = [word for word in arguments["query"].lower().split() if word]
        if not words:
            raise SystemToolError("a search needs at least one word")
        # A word is looked for where a word begins, so that "rent" finds
        # "rental" and not "current". A word of three letters or fewer
        # must be the whole word, or "see" would find "seedlings".
        wanted = [
            re.compile(
                r"(?<![a-z0-9])"
                + re.escape(word)
                + (r"(?![a-z0-9])" if len(word) <= 3 else "")
            )
            for word in words
        ]
        deadline = time.monotonic() + MAX_SEARCH_SECONDS
        found: list[tuple[int, str, int, str]] = []
        for name, number, row in _lines(folder, start, deadline):
            lower = row.lower()
            hits = sum(1 for pattern in wanted if pattern.search(lower))
            if hits:
                found.append((hits, name, number, row.strip()))
        # Lines holding more of the words first; then by file and line, so
        # the same search always gives the same answer.
        found.sort(key=lambda item: (-item[0], item[1], item[2]))
        return {
            "matches": [
                f"{name}:{number}: {row[:MAX_SEARCH_LINE]}"
                for _, name, number, row in found[:MAX_SEARCH_MATCHES]
            ]
        }

    def find(_tool: ToolDecl, arguments: dict[str, Any]) -> dict[str, Any]:
        assert meaning is not None
        start = (
            folder
            if arguments["path"] in (".", "")
            else _inside(folder, arguments["path"])
        )
        if not start.is_dir():
            raise SystemToolError(f"{arguments['path']!r} is not a folder")
        question = arguments["query"].strip()
        if not question:
            raise SystemToolError("a search by meaning needs a question")
        deadline = time.monotonic() + MAX_SEARCH_SECONDS
        rows: list[tuple[str, int, str]] = []
        for name, number, row in _lines(folder, start, deadline):
            line = row.strip()
            if len(line) >= MIN_FIND_LINE:
                rows.append((name, number, line[:MAX_SEARCH_LINE]))
                if len(rows) >= MAX_FIND_LINES:
                    break
        if not rows:
            return {"matches": []}
        try:
            ranked = meaning.nearest(
                question, [line for _, _, line in rows], MAX_FIND_NEW
            )
        except EmbeddingError as exc:
            raise SystemToolError(str(exc)) from exc
        return {
            "matches": [
                f"{rows[at][0]}:{rows[at][1]}: {rows[at][2]}"
                for _, at in ranked[:MAX_FIND_MATCHES]
            ]
        }

    def write(_tool: ToolDecl, arguments: dict[str, Any]) -> dict[str, Any]:
        data = arguments["text"].encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise SystemToolError("text to write is larger than 1 MiB")
        path = _inside(folder, arguments["path"])
        if path.is_dir():
            raise SystemToolError(f"{arguments['path']!r} is a folder")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"bytes": len(data)}

    def create(_tool: ToolDecl, arguments: dict[str, Any]) -> dict[str, Any]:
        data = arguments["text"].encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise SystemToolError("text to write is larger than 1 MiB")
        path = _inside(folder, arguments["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # "x" fails if the path exists, checked and created in one step.
            with path.open("xb") as stream:
                stream.write(data)
        except FileExistsError as exc:
            raise SystemToolError(
                f"{arguments['path']!r} already exists; create never overwrites"
            ) from exc
        return {"bytes": len(data)}

    def write_all(_tool: ToolDecl, arguments: dict[str, Any]) -> dict[str, Any]:
        files = arguments["files"]
        # Every path is looked at before any file is written, so a list
        # with one bad path writes nothing.
        ready = []
        for item in files:
            data = item["text"].encode("utf-8")
            if len(data) > MAX_FILE_BYTES:
                raise SystemToolError(f"{item['path']!r} would be larger than 1 MiB")
            path = _inside(folder, item["path"])
            if path.is_dir():
                raise SystemToolError(f"{item['path']!r} is a folder")
            ready.append((path, data))
        for path, data in ready:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return {"count": len(ready)}

    def list_names(_tool: ToolDecl, arguments: dict[str, Any]) -> dict[str, Any]:
        path = (
            folder
            if arguments["path"] in (".", "")
            else _inside(folder, arguments["path"])
        )
        if not path.is_dir():
            raise SystemToolError(f"{arguments['path']!r} is not a folder")
        names = sorted(
            entry.name + ("/" if entry.is_dir() else "") for entry in path.iterdir()
        )
        if len(names) > MAX_LISTED_NAMES:
            raise SystemToolError(f"folder has more than {MAX_LISTED_NAMES} entries")
        return {"names": names}

    return {
        "read": read,
        "peek": peek,
        "search": search,
        "find": find,
        "write": write,
        "create": create,
        "write_all": write_all,
        "list": list_names,
    }[tool.operation]


def bind_system_tools(
    program: Program,
    *,
    folder: str | Path | None,
    allow_shell: bool = False,
    sites: Sequence[str] = (),
    web_opener: Any = None,
    meaning: Meaning | None = None,
    session: Session | None = None,
    notify: str | None = None,
    services: str | Path | None = None,
    linux: str | None = None,
    computer: bool = False,
    database: str | Path | None = None,
) -> dict[str, Callable[[ToolDecl, dict[str, Any]], Any]]:
    """Bind the program's system tools that the host has granted.

    A files tool is bound when a folder is granted, and one that searches by
    meaning only when an embedding model is granted too. A shell tool is bound only
    when a folder is granted and shell is allowed. A web tool is bound when
    at least one site is allowed. Anything not bound stays unreachable.

    With a `session`, file tools work in the session's copy of the folder
    and shell tools run behind its wall, so nothing they do reaches the
    granted folder itself.
    """

    system = [tool for tool in program.tools if system_kind(tool) is not None]
    for tool in system:
        problem = declaration_problem(program, tool)
        if problem is not None:
            raise SystemToolError(problem)
    web: dict[str, Callable[[ToolDecl, dict[str, Any]], Any]] = dict(
        bind_kosika_tools(program)
    )
    if sites:
        try:
            reader = (
                bind_web_tool(sites)
                if web_opener is None
                else bind_web_tool(sites, web_opener)
            )
        except WebError as exc:
            raise SystemToolError(str(exc)) from exc
        web.update({tool.name: reader for tool in system if system_kind(tool) == "web"})
    if notify:
        try:
            note = bind_notify_tool(notify)
        except NotifyError as exc:
            raise SystemToolError(str(exc)) from exc
        web.update({tool.name: note for tool in system if system_kind(tool) == "notify"})
    if services:
        keeper = bind_services_tool(services)
        web.update(
            {tool.name: keeper for tool in system if system_kind(tool) == "services"}
        )
    if linux:
        reader = bind_linux_tool(linux)
        web.update({tool.name: reader for tool in system if system_kind(tool) == "linux"})
    if computer:
        web.update(
            {tool.name: computer_status for tool in system if system_kind(tool) == "computer"}
        )
    if meaning is not None:
        web.update(
            {
                tool.name: _near(meaning)
                for tool in system
                if system_kind(tool) == "meaning"
            }
        )
    if database is not None:
        from .database import DatabaseError, bind_database_tool

        try:
            keeper = bind_database_tool(database, program)
        except DatabaseError as exc:
            raise SystemToolError(str(exc)) from exc
        web.update({tool.name: keeper for tool in system if system_kind(tool) == "db"})
    if folder is None:
        if allow_shell:
            raise SystemToolError(
                "allowing shell also needs a granted folder to run in"
            )
        return web
    root = (session.work if session is not None else Path(folder)).resolve()
    if not root.is_dir():
        raise SystemToolError(
            f"granted folder {str(folder)!r} is not an existing folder"
        )
    bindings: dict[str, Callable[[ToolDecl, dict[str, Any]], Any]] = dict(web)
    for tool in system:
        if system_kind(tool) == "files":
            if tool.operation == "find" and meaning is None:
                continue
            bindings[tool.name] = _files(tool, root, meaning)
        elif system_kind(tool) == "shell" and allow_shell:
            bindings[tool.name] = (
                _shell(tool, root) if session is None else _session_shell(tool, session)
            )
    return bindings
