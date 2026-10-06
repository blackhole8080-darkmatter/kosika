"""Talk to a Kosika program, one message at a time.

    kosika chat main.kos --model MODEL --memory FOLDER

Each line you type is one run of the program's workflow. The program decides
what a conversation is: an assistant that should recall earlier turns keeps
them in a memory, like any other fact it wants to keep. So a chat has no
powers a single run does not have. The models, tools, and memory folder are
the ones granted on the command line, and every turn is an ordinary run that
can be traced, replayed, and told with `kosika story`.

The workflow must take exactly one record that has a `text: Text` field, and
its result is shown by its `text` field when it has one.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from .ast import Program
from .model import ModelProvider
from .runtime import run

QUIT = ("exit", "quit", "bye")


class ChatError(ValueError):
    """The program cannot be talked to."""


def message_parameter(program: Program, workflow_name: str) -> tuple[str, list[str]]:
    """The workflow's one parameter, and the other fields of its record."""

    workflow = next((w for w in program.workflows if w.name == workflow_name), None)
    if workflow is None:
        known = ", ".join(sorted(w.name for w in program.workflows)) or "none"
        raise ChatError(
            f"No workflow named {workflow_name!r}; this program has: {known}"
        )
    if len(workflow.parameters) != 1:
        raise ChatError(
            f"Workflow {workflow_name!r} must take exactly one record to be "
            "chatted with"
        )
    parameter = workflow.parameters[0]
    record = next((r for r in program.records if r.name == parameter.type_name), None)
    fields = {f.name: f.type_name for f in (record.fields if record else ())}
    if fields.get("text") != "Text":
        raise ChatError(
            f"Workflow {workflow_name!r} takes {parameter.type_name}, which "
            "needs a `text: Text` field to hold what you type"
        )
    others = [name for name in fields if name != "text"]
    if others:
        raise ChatError(
            f"Record {parameter.type_name} has other fields ({', '.join(others)}); "
            "a chat can fill in only `text`"
        )
    return parameter.name, others


def answer_text(result: Any) -> str:
    if isinstance(result, dict) and isinstance(result.get("text"), str):
        return result["text"]
    return json.dumps(result, ensure_ascii=False)


def chat_loop(
    program: Program,
    workflow_name: str,
    *,
    model_bindings: Mapping[str, ModelProvider],
    tool_bindings: Mapping[str, Any],
    read: Callable[[], str | None],
    write: Callable[[str], None],
    trace: Path | None = None,
    show_time: bool = True,
) -> int:
    """Run one workflow per line read. Returns how many turns were answered."""

    parameter, _ = message_parameter(program, workflow_name)
    answered = 0
    while True:
        line = read()
        if line is None:
            break
        text = line.strip()
        if not text:
            continue
        if text.lower() in QUIT:
            break
        started = time.monotonic()
        try:
            result = run(
                program,
                workflow_name,
                trace,
                inputs={parameter: {"text": text}},
                model_bindings=model_bindings,
                tool_bindings=tool_bindings,
            )
        except (RuntimeError, ValueError, PermissionError) as exc:
            # One failed turn should not end the conversation.
            write(f"(that did not work: {exc})")
            continue
        answered += 1
        took = f"  [{time.monotonic() - started:.1f} s]" if show_time else ""
        write(answer_text(result) + took)
    return answered
