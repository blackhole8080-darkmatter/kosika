"""A map of one program that a person new to it can read.

The map answers four questions: what is this for, what goes in and comes
out, what may it look at and change, and what does it do, in order. It is
built only from the checked program and from words a person wrote. No model
is asked, and nothing is guessed: where Kosika cannot see, the map says so.

Every item says where it comes from:

* ``checked``  read from the checked program, with its line in the file;
* ``comment``  the comment the author put at the top of the file;
* ``person``   a purpose note someone saved for this program;
* ``unseen``   something the program reaches that Kosika cannot look
  inside, such as an outside server or a program it starts.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .ast import (
    AgentCall,
    FunctionCall,
    ModelCall,
    ModelDecl,
    Program,
    Step,
    ToolCall,
    WorkflowCall,
)

#: A map shows at most this many steps; the rest are counted, not drawn.
MAX_STEPS = 10
MAX_NOTE = 600
NOTE_FILE = "purpose.txt"

_FILE_WORDS = {
    "read": "reads a file you name",
    "peek": "reads the start of a file",
    "list": "lists the names in a folder",
    "search": "searches the folder for words",
    "find": "searches the folder by meaning",
    "write": "writes a file",
    "create": "creates a new file, never over an old one",
    "write_all": "writes several files in one step",
}


def read_note(folder: Path) -> str:
    try:
        return (folder / NOTE_FILE).read_text(encoding="utf-8").strip()[:MAX_NOTE]
    except (OSError, UnicodeDecodeError):
        return ""


def save_note(folder: Path, text: str) -> str:
    """Keep a person's sentence about what the program is for."""

    note = " ".join(text.split())[:MAX_NOTE]
    path = folder / NOTE_FILE
    if note:
        path.write_text(note + "\n", encoding="utf-8")
    elif path.is_file():
        path.unlink()
    return note


def _lines(source: str) -> dict[tuple[str, str], int]:
    """Where each declaration starts: (kind, name) -> line."""

    found: dict[tuple[str, str], int] = {}
    pattern = re.compile(
        r"^\s*(?:default\s+)?(tool|model|agent|workflow|memory|board|record|choice|fn|swarm)"
        r"\s+([A-Za-z_]\w*)"
    )
    for number, line in enumerate(source.splitlines(), 1):
        match = pattern.match(line)
        if match:
            found.setdefault((match.group(1), match.group(2)), number)
    return found


def _step_lines(source: str, workflow: str) -> dict[str, int]:
    """Where each step of a workflow is written: name -> line."""

    found: dict[str, int] = {}
    inside = False
    start = re.compile(rf"^\s*workflow\s+{re.escape(workflow)}\b")
    step = re.compile(r"^\s*([A-Za-z_]\w*)\s*=")
    for number, line in enumerate(source.splitlines(), 1):
        if start.match(line):
            inside = True
            continue
        if inside:
            if line.startswith("}"):
                break
            match = step.match(line)
            if match:
                found.setdefault(match.group(1), number)
    return found


def _tool_item(tool: Any, line: int | None) -> tuple[str, dict[str, Any]]:
    """Which list a tool belongs in, and its entry."""

    item: dict[str, Any] = {"name": tool.name, "line": line, "source": "checked"}
    source, changes = tool.source, tool.access == "execute"
    if source.startswith("system:files"):
        item["does"] = _FILE_WORDS.get(tool.operation, "uses the granted folder")
        item["needs"] = "a folder you grant"
    elif source == "system:web":
        item["does"] = "reads a web page"
        item["needs"] = "sites you allow"
    elif source == "system:shell":
        item["does"] = f"starts the program {tool.operation}"
        item["needs"] = "your permission to start programs"
        item["unseen"] = "What that program then does is not visible to Kosika."
    elif source == "system:meaning":
        item["does"] = "compares two texts for meaning"
        item["needs"] = "an embedding model you grant"
    elif source == "system:kosika":
        item["does"] = "checks a Kosika program given as text"
        item["needs"] = "nothing: it reaches no file and no network"
    elif ":" not in source:
        # A tool whose source is a bare name comes from the MCP server the
        # person running the program gave that name to.
        item["does"] = f"asks the outside server {source} to {tool.operation}"
        item["needs"] = "that server, named by you"
        item["unseen"] = "What the server does is not visible to Kosika."
    else:
        item["does"] = f"uses {tool.operation or tool.name} from {source}"
        item["needs"] = "a binding from whoever runs it"
        item["unseen"] = "What it does is not visible to Kosika."
    # Anything that can change the world is listed as a change, whether or
    # not Kosika can see inside it. What only looks, but through something
    # Kosika cannot see into, is listed apart.
    if changes:
        return "changes", item
    return ("outside" if "unseen" in item else "reads"), item


def _does(program: Program, call: Any) -> str:
    if isinstance(call, ModelCall):
        model = next((m for m in program.models if m.name == call.model_name), None)
        if model is not None and model.verb:
            return f"asks a model to {model.verb}"
        return f"asks the model {call.model_name}"
    if isinstance(call, AgentCall):
        agent = next((a for a in program.agents if a.name == call.agent_name), None)
        if agent is not None and agent.plans_first(program.tools):
            return (
                f"asks the agent {call.agent_name}, which shows you its whole "
                "plan of changes and waits for one yes"
            )
        return f"asks the agent {call.agent_name}, which may choose its own tools"
    if isinstance(call, WorkflowCall):
        return f"runs the workflow {call.workflow_name}"
    if isinstance(call, ToolCall):
        tool = next((t for t in program.tools if t.name == call.tool_name), None)
        source = tool.source if tool else ""
        if source.startswith("memory:"):
            verb = "looks up" if tool and tool.operation == "recall" else "saves to"
            return f"{verb} its memory {source[7:]}"
        if source.startswith("board:"):
            return f"uses the shared board {source[6:]}"
        if source == "world:clock":
            return "looks at the clock"
        if source.startswith("world:"):
            return "draws a random number"
        if tool is not None:
            return _tool_item(tool, None)[1]["does"]
        return f"uses the tool {call.tool_name}"
    if isinstance(call, FunctionCall):
        if "$" in call.function_name:
            return "uses a value written in the program"
        return f"works out {call.function_name} in plain code"
    return "does something this map cannot name"


def _inside(program: Program, call: Any, deep: bool) -> list[dict[str, Any]]:
    """The steps of a workflow that a step runs, one level down."""

    if not deep or not isinstance(call, WorkflowCall):
        return []
    called = next((w for w in program.workflows if w.name == call.workflow_name), None)
    if called is None:
        return []
    return [_step(program, step, None, deep=False) for step in called.steps][:MAX_STEPS]


def _step(
    program: Program, step: Step, line: int | None, *, deep: bool = True
) -> dict[str, Any]:
    view: dict[str, Any] = {"name": step.name, "line": line, "source": "checked"}
    if step.branches:
        view["does"] = "chooses one of these, by what it was given"
        view["choices"] = [
            {
                "when": "anything else" if pattern is None else str(pattern),
                "does": _does(program, call),
                "inside": _inside(program, call, deep),
            }
            for pattern, call in step.branches
        ]
    else:
        view["does"] = _does(program, step.call)
        inside = _inside(program, step.call, deep)
        if inside:
            view["inside"] = inside
    notes: list[str] = []
    if step.verify is not None:
        notes.append("the answer must pass a check written in the program")
    if step.retries:
        notes.append(f"asked again up to {step.retries} more time(s) if refused")
    if step.votes:
        notes.append(f"asked {step.votes} times; most must agree")
    if step.escalate:
        notes.append(f"a stronger model ({step.escalate}) is asked if needed")
    if step.fallback is not None:
        notes.append("if it fails: " + _does(program, step.fallback))
    if notes:
        view["notes"] = notes
    return view


def _shape(program: Program, type_name: str) -> list[dict[str, str]]:
    record = next((r for r in program.records if r.name == type_name), None)
    if record is None:
        return []
    return [{"name": f.name, "type": f.type_name} for f in record.fields]


def beginner_map(
    program: Program, source: str, *, about: str = "", note: str = ""
) -> dict[str, Any]:
    """The map described at the top of this module, as plain data."""

    lines = _lines(source)
    if note:
        purpose = {"text": note, "source": "person"}
    elif about:
        purpose = {"text": about, "source": "comment"}
    else:
        purpose = {"text": "", "source": "none"}
    groups: dict[str, list[dict[str, Any]]] = {
        "reads": [],
        "changes": [],
        "outside": [],
    }
    for tool in program.tools:
        if tool.source.startswith(("memory:", "board:", "world:", "direct:")):
            continue
        group, item = _tool_item(tool, lines.get(("tool", tool.name)))
        groups[group].append(item)
    def answers(model: ModelDecl) -> str:
        choice = next((c for c in program.choices if c.name == model.output), None)
        if choice is None:
            return "answers when the program asks it"
        return "answers with one of: " + ", ".join(
            alternative.name for alternative in choice.alternatives
        )

    models = [
        {
            "name": model.name,
            "line": lines.get(("model", model.name)),
            "source": "checked",
            "does": answers(model) + (" (the default)" if model.default else ""),
        }
        for model in program.models
        if model.provider is None and not model.verb
    ]
    thinking = sorted({model.verb for model in program.models if model.verb})
    memories = [
        {
            "name": memory.name,
            "line": lines.get(("memory", memory.name)),
            "source": "checked",
            "does": f"remembers {memory.record} values between runs",
        }
        for memory in program.memories
    ]
    entry = next((w for w in program.workflows if w.name == "main"), None)
    steps: list[dict[str, Any]] = []
    given: list[dict[str, Any]] = []
    gives: dict[str, Any] = {}
    if entry is not None:
        at = _step_lines(source, "main")
        steps = [_step(program, step, at.get(step.name)) for step in entry.steps]
        given = [
            {
                "name": p.name,
                "type": p.type_name,
                "fields": _shape(program, p.type_name),
            }
            for p in entry.parameters
        ]
        gives = {
            "type": entry.output_type,
            "fields": _shape(program, entry.output_type),
        }
    shown = [
        *groups["reads"],
        *groups["changes"],
        *groups["outside"],
        *models,
        *memories,
    ]
    return {
        "purpose": purpose,
        "given": given,
        "gives": gives,
        "reads": groups["reads"],
        "changes": groups["changes"],
        "outside": groups["outside"],
        "models": models,
        "thinking": thinking,
        "memories": memories,
        "steps": steps[:MAX_STEPS],
        "more_steps": max(0, len(steps) - MAX_STEPS),
        "other_workflows": sorted(
            w.name for w in program.workflows if w.name != "main"
        ),
        "budget": [list(item) for item in entry.budget] if entry else [],
        "has_main": entry is not None,
        "coverage": {
            "checked": len(shown) + len(steps[:MAX_STEPS]),
            "person": 1 if purpose["source"] == "person" else 0,
            "comment": 1 if purpose["source"] == "comment" else 0,
            "unseen": sum(
                1
                for item in (*groups["changes"], *groups["outside"])
                if "unseen" in item
            ),
        },
    }
