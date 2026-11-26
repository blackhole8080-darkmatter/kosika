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

A turn that reaches a tool which changes the world stops and asks. The
approval is for that one call with those exact arguments; the next action is
asked about separately, and nothing is remembered between turns. Without an
approver, such a turn fails and nothing is changed.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from .agent import ApprovalRecord
from .ast import Program
from .durable import ApprovalPending, ExecutionSession
from .model import ModelProvider
from .runtime import run

#: Asked once for each action that would change the world. It is shown the
#: exact tool and arguments and answers whether that one action may run.
Approver = Callable[[dict[str, Any]], bool]


class Refused(Exception):
    """The person said no to an action, so the turn ends with nothing done."""


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


#: Fields that hold what an agent says, in the order they are looked for.
SAYING = ("text", "answer", "reply", "message", "summary")
#: Fields that are for the page, not for the person to read.
UNSAID = {"suggest", "canvas", "svg", "html", "page", "story", "trace"}


def _said(value: Any) -> str:
    """One value in a few words: never braces and quotes."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (int, float, str)):
        return str(value).strip()
    if isinstance(value, dict):
        parts = [
            f"{key.replace('_', ' ')} {_said(item)}"
            for key, item in value.items()
            if not _empty(item) and key not in UNSAID
        ]
        return ", ".join(part for part in parts if part.strip())
    if isinstance(value, list):
        return "; ".join(_said(item) for item in value if not _empty(item))
    return str(value)


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def in_words(result: Any) -> str:
    """What an agent answered, in plain words for a person: its text when
    it has one, else its summary followed by each field that holds
    something, one short line each. A record is never shown as JSON."""
    if result is None:
        return "Done. There was nothing to say."
    if not isinstance(result, dict):
        if isinstance(result, list):
            if not result:
                return "Nothing was found."
            return "\n".join("- " + _said(item) for item in result if not _empty(item))
        return _said(result)
    if isinstance(result.get("text"), str) and result["text"].strip():
        return result["text"]
    lead_key = next(
        (key for key in SAYING if isinstance(result.get(key), str) and result[key].strip()),
        "",
    )
    lines = [result[lead_key].strip()] if lead_key else []
    for key, value in result.items():
        if key == lead_key or key in UNSAID or _empty(value):
            continue
        if isinstance(value, list):
            items = [_said(item) for item in value if not _empty(item)]
            lines.append(key.replace("_", " ").capitalize() + ":")
            lines.extend("- " + item for item in items)
        else:
            lines.append(f"{key.replace('_', ' ').capitalize()}: {_said(value)}")
    return "\n".join(lines) if lines else "Done. There was nothing to say."


def answer_text(result: Any) -> str:
    return in_words(result)


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
    approve: Approver | None = None,
    approved_by: str = "chat",
) -> int:
    """Run one workflow per line read. Returns how many turns were answered."""

    parameter, _ = message_parameter(program, workflow_name)

    def turn(text: str) -> Any:
        inputs = {parameter: {"text": text}}
        if approve is None:
            return run(
                program,
                workflow_name,
                trace,
                inputs=inputs,
                model_bindings=model_bindings,
                tool_bindings=tool_bindings,
            )
        # The journal lets the turn stop at an action, wait for the answer,
        # and carry on from that step without redoing the ones before it.
        with tempfile.TemporaryDirectory(prefix="kosika-chat-") as scratch:
            journal = Path(scratch) / "turn.sqlite"
            approvals: list[ApprovalRecord] = []
            while True:
                session = ExecutionSession(
                    journal, binding_id="chat", approvals=approvals
                )
                try:
                    return run(
                        program,
                        workflow_name,
                        trace,
                        inputs=inputs,
                        model_bindings=model_bindings,
                        tool_bindings=tool_bindings,
                        session=session,
                    )
                except ApprovalPending as pending:
                    request = pending.request
                    if not approve(request):
                        raise Refused from None
                    approvals.append(
                        ApprovalRecord(
                            plan_hash=request["plan_hash"],
                            action_hash=request["action_hash"],
                            approved_by=approved_by,
                            reason="approved in chat",
                        )
                    )

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
            result = turn(text)
        except Refused:
            write("Refused. Nothing was changed.")
            continue
        except (RuntimeError, ValueError, PermissionError) as exc:
            # One failed turn should not end the conversation.
            write(f"(that did not work: {exc})")
            continue
        answered += 1
        took = f"  [{time.monotonic() - started:.1f} s]" if show_time else ""
        write(answer_text(result) + took)
    return answered
