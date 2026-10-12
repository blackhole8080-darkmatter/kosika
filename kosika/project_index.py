"""Everything in a folder of Kosika programs, in one list, with suggestions.

Studio shows one agent at a time. This is the view across all of them:
every agent, workflow, model, tool, memory, board and record, each with the
program it belongs to and its line, so a name can be searched for in one
place.

It also makes suggestions. Each one comes from a rule applied to the checked
program, never from a model, and says which rule it is, so a suggestion can
be understood, argued with, or ignored.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .ast import AgentCall, ModelCall, Program, ToolCall, WorkflowCall
from .modules import load_program
from .project_map import NOTE_FILE, _lines, _step_lines, _tool_item

#: Declarations the language adds for itself, which nobody wrote.
_OWN = ("memory:", "board:", "world:", "direct:")


def _calls(program: Program) -> list[tuple[str, Any, Any]]:
    """Every call a step can make: (workflow, step, call)."""

    found: list[tuple[str, Any, Any]] = []
    for workflow in program.workflows:
        for step in workflow.steps:
            calls = [call for _, call in step.branches] or [step.call]
            if step.fallback is not None:
                calls.append(step.fallback)
            found.extend((workflow.name, step, call) for call in calls)
    return found


def _items(program: Program, source: str) -> list[dict[str, Any]]:
    lines = _lines(source)
    items: list[dict[str, Any]] = []

    def add(kind: str, name: str, detail: str, declared: str | None = None) -> None:
        items.append(
            {
                "kind": kind,
                "name": name,
                "detail": detail,
                "line": lines.get((declared or kind, name)),
            }
        )

    for workflow in program.workflows:
        given = ", ".join(f"{p.name}: {p.type_name}" for p in workflow.parameters)
        add(
            "workflow",
            workflow.name,
            f"({given}) gives {workflow.output_type}, {len(workflow.steps)} step(s)",
        )
    for model in program.models:
        if model.provider is None and not model.verb:
            add("model", model.name, f"given {model.input}, answers {model.output}")
    for tool in program.tools:
        if tool.source.startswith(_OWN):
            continue
        group, item = _tool_item(tool, None)
        add(
            "tool",
            tool.name,
            item["does"] + (" (asks you first)" if group == "changes" else ""),
        )
    for memory in program.memories:
        add("memory", memory.name, f"remembers {memory.record} values")
    for board in program.boards:
        add("board", board.name, f"agents post {board.record} messages")
    for record in program.records:
        fields = ", ".join(f"{f.name}: {f.type_name}" for f in record.fields)
        if (("record", record.name)) in lines:
            add("record", record.name, fields)
    for swarm in program.swarms:
        add("swarm", swarm.name, "agents: " + ", ".join(swarm.agents))
    return items


def _suggestions(program: Program, source: str, folder: Path) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def say(rule: str, text: str, line: int | None = None) -> None:
        found.append({"rule": rule, "text": text, "line": line})

    calls = _calls(program)
    asks_model = any(isinstance(call, (ModelCall, AgentCall)) for _, _, call in calls)
    tests = [test for test in program.tests if not test.live]
    evals = [test for test in program.tests if test.live]
    if not tests:
        say(
            "no-tests",
            "It has no tests. A test with scripted answers needs no model and "
            "runs in a moment.",
        )
    if asks_model and not evals:
        say(
            "no-evals",
            "It asks a model but has no eval, so `kosika measure` has nothing "
            "to tell you about how often it is right.",
        )
    for workflow in program.workflows:
        at = _step_lines(source, workflow.name)
        for step in workflow.steps:
            if isinstance(step.call, ModelCall) and not step.branches:
                if step.fallback is None:
                    say(
                        "model-step-no-fallback",
                        f"Step {step.name} in {workflow.name} asks a model and "
                        "has no `otherwise`: an unusable answer stops the run.",
                        at.get(step.name),
                    )
    entry = next((w for w in program.workflows if w.name == "main"), None)
    if entry is not None and asks_model and not entry.budget:
        say(
            "no-budget",
            "main states no budget, so only the limits of whoever runs it "
            "apply. `budget 3 model calls, 60 seconds` makes its own limit plain.",
        )
    lines = _lines(source)
    used_tools = {call.tool_name for _, _, call in calls if isinstance(call, ToolCall)}
    for agent in program.agents:
        if any(
            isinstance(call, AgentCall) and call.agent_name == agent.name
            for _, _, call in calls
        ):
            # An agent asked to choose for itself may use any tool it can.
            used_tools.update(agent.can)
    for tool in program.tools:
        if not tool.source.startswith(_OWN) and tool.name not in used_tools:
            say(
                "unused-tool",
                f"Tool {tool.name} is declared but no step uses it.",
                lines.get(("tool", tool.name)),
            )
    used_models = {
        call.model_name for _, _, call in calls if isinstance(call, ModelCall)
    }
    used_models.update(step.escalate for _, step, _ in calls if step.escalate)
    used_models.update(agent.model for agent in program.agents if agent.model)
    # A thinking step that names its model (`classify ... using judge`) is
    # lowered to a generated model whose provider is the one it named.
    used_models.update(
        model.provider
        for model in program.models
        if model.provider and model.name in used_models
    )
    for model in program.models:
        if (
            model.provider is None
            and not model.verb
            and model.name not in used_models
            and not (model.default and asks_model)
        ):
            say(
                "unused-model",
                f"Model {model.name} is declared but no step asks it.",
                lines.get(("model", model.name)),
            )
    called = {
        call.workflow_name for _, _, call in calls if isinstance(call, WorkflowCall)
    }
    called.update(swarm.workflow for swarm in program.swarms)
    for workflow in program.workflows:
        if workflow.name != "main" and workflow.name not in called:
            say(
                "unused-workflow",
                f"Workflow {workflow.name} is never run by another workflow.",
                lines.get(("workflow", workflow.name)),
            )
    first = next((line.strip() for line in source.splitlines() if line.strip()), "")
    if not first.startswith("//") and not (folder / NOTE_FILE).is_file():
        say(
            "no-purpose",
            "Nothing says what it is for. Add a // comment at the top, or a "
            "note on its Map tab.",
        )
    return found


def project_index(agents: list[tuple[str, str, Path]]) -> dict[str, Any]:
    """The whole folder: `agents` is (id, title, path) for each program."""

    items: list[dict[str, Any]] = []
    suggestions: list[dict[str, Any]] = []
    for agent_id, title, path in agents:
        where = {"agent": agent_id, "title": title}
        try:
            source = path.read_text(encoding="utf-8")
            program = load_program(path)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            items.append(
                {
                    **where,
                    "kind": "agent",
                    "name": title,
                    "detail": "does not check",
                    "line": None,
                }
            )
            suggestions.append(
                {
                    **where,
                    "rule": "does-not-check",
                    "text": f"It does not check: {exc}",
                    "line": None,
                    "must": True,
                }
            )
            continue
        steps = sum(len(workflow.steps) for workflow in program.workflows)
        items.append(
            {
                **where,
                "kind": "agent",
                "name": title,
                "detail": f"{len(program.workflows)} workflow(s), {steps} step(s), "
                f"{sum(1 for t in program.tests if not t.live)} test(s)",
                "line": 1,
            }
        )
        items.extend({**where, **item} for item in _items(program, source))
        suggestions.extend(
            {**where, **found, "must": False}
            for found in _suggestions(program, source, path.parent)
        )
    counts: dict[str, int] = {}
    for item in items:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    return {"items": items, "counts": counts, "suggestions": suggestions}
