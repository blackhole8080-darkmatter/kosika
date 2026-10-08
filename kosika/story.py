"""Tell what happened in a run, in plain words, from its trace.

    kosika story run.jsonl

A trace already records everything a run did. This module reads one and says
it the way a person would: each step, what was asked and answered, which
quotes backed an answer, what was rejected and retried, how a vote went, what
was recalled or remembered, when a fallback took over, and what the run cost.

Nothing is run and no model is asked: every word comes from the trace. The
trace is first checked by replay, so a story is only told as fact for a run
that replays; for one that does not, the story says so and tells what it can.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .trace_replay import replay_events

_CLIP = 110
_CALLS = {
    "model_call": "model",
    "tool_call": "tool",
    "agent_call": "agent",
    "workflow_call": "workflow",
    "function_call": "function",
}


class StoryError(ValueError):
    """A trace cannot be read well enough to tell its story."""


def read_trace(path: str | Path) -> list[dict[str, Any]]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        events = [json.loads(line) for line in lines if line.strip()]
    except (OSError, ValueError) as exc:
        raise StoryError(f"Cannot read trace {path}: {exc}") from exc
    if not events or not all(isinstance(event, dict) for event in events):
        raise StoryError(f"{path} is not a Kosika trace")
    return events


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _show(value: Any, limit: int = _CLIP) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _seconds(first: dict[str, Any], last: dict[str, Any]) -> float | None:
    try:
        start = datetime.fromisoformat(first["timestamp"])
        end = datetime.fromisoformat(last["timestamp"])
    except (KeyError, TypeError, ValueError):
        return None
    return max(0.0, (end - start).total_seconds())


def _model_step(call: dict[str, Any], returned: dict[str, Any]) -> dict[str, Any]:
    name = str(call.get("model", ""))
    base, _, rest = name.partition("/")
    verb = rest.split("/")[0] if rest else ""
    arguments = call.get("arguments") or {}
    result = returned.get("result") or {}
    if verb == "classify":
        did = f"asked {base} to classify into {_show(arguments.get('choices'), 70)}"
        answer = f"{result.get('label')} (confidence {result.get('confidence')})"
    elif verb == "decide":
        did = f"asked {base} to decide: {_show(arguments.get('question'), 80)}"
        answer = (
            f"{'yes' if result.get('answer') else 'no'} "
            f"(confidence {result.get('confidence')}): {_show(result.get('reason'), 90)}"
        )
    elif verb == "extract":
        did = f"asked {base} to extract {rest.split('/')[1] if '/' in rest else 'a record'}"
        answer = _show(result)
    elif verb == "summarize":
        did = f"asked {base} to summarize"
        answer = _show(result.get("text"))
    else:
        did = f"asked model {name}"
        answer = _show(result)
    step: dict[str, Any] = {"kind": "model", "did": did, "answer": answer}
    # The request as it was sent and the reply as it came back, for anyone
    # who wants to see exactly what the model was asked.
    step["asked"] = _show(arguments, 1500)
    if isinstance(returned.get("content"), str):
        step["raw"] = _show(returned["content"], 1500)
    step["model"] = name
    quotes = returned.get("evidence")
    if isinstance(quotes, dict):
        step["evidence"] = [
            f"{field}: {_show(quote, 80)!r}"
            for field, quote in sorted(quotes.items())
            if quote is not None
        ]
    elif isinstance(quotes, list):
        step["evidence"] = [repr(_show(quote, 90)) for quote in quotes]
    rejected = returned.get("rejected")
    if isinstance(rejected, list) and rejected:
        step["rejected"] = [_show(item.get("reason"), 100) for item in rejected]
    votes = returned.get("votes")
    if isinstance(votes, dict):
        tally = ", ".join(
            f"{count} for {key}" for key, count in (votes.get("tally") or {}).items()
        )
        models = votes.get("models") or []
        step["vote"] = (
            f"{votes.get('agreed')} of {votes.get('asked')} agreed ({tally})"
            + (f"; asked {', '.join(models)}" if len(set(models)) > 1 else "")
            + (
                f"; {len(votes['rejected'])} answer(s) were rejected"
                if votes.get("rejected")
                else ""
            )
        )
    usage = returned.get("usage")
    if isinstance(usage, dict) and isinstance(usage.get("total_tokens"), int):
        step["tokens"] = usage["total_tokens"]
    return step


def _tool_step(call: dict[str, Any], returned: dict[str, Any]) -> dict[str, Any]:
    source = str(call.get("source", ""))
    arguments = call.get("arguments") or {}
    result = returned.get("result") or {}
    if source.startswith("memory:"):
        memory = source.removeprefix("memory:")
        key = arguments.get("key")
        if call.get("operation") == "recall":
            found = result.get("found")
            return {
                "kind": "memory",
                "did": f"looked up {key!r} in memory {memory}",
                "answer": _show(result.get("value")) if found else "nothing stored yet",
            }
        return {
            "kind": "memory",
            "did": f"remembered {_show(arguments.get('value'), 70)} under {key!r} "
            f"in memory {memory}",
            "answer": f"memory {memory} now holds {result.get('entries')} entr"
            + ("y" if result.get("entries") == 1 else "ies"),
        }
    if source.startswith("board:"):
        board = source.removeprefix("board:")
        if call.get("operation") == "post":
            return {
                "kind": "board",
                "did": f"{arguments.get('author')} posted to board {board}",
                "answer": _show(arguments.get("body")),
            }
        posts = result.get("posts") or []
        last = posts[-1] if posts else None
        return {
            "kind": "board",
            "did": f"read board {board}: {len(posts)} post(s)",
            "answer": (
                f"latest, from {last.get('author')}: {_show(last.get('body'), 80)}"
                if isinstance(last, dict)
                else "nothing posted yet"
            ),
        }
    if source == "system:web":
        return {
            "kind": "web",
            "did": f"read the web page {arguments.get('url')}",
            "answer": f"{result.get('title') or 'untitled'} "
            f"({len(result.get('text') or '')} characters) from {result.get('url')}",
        }
    effect = "changes the world" if call.get("access") == "execute" else "read-only"
    return {
        "kind": "tool",
        "did": f"used tool {call.get('tool')} ({effect}) through agent "
        f"{call.get('agent')} with {_show(arguments, 70)}",
        "answer": _show(result),
    }


def _step(events: list[dict[str, Any]]) -> dict[str, Any]:
    start = events[0]
    call = next((e for e in events if e.get("event") in _CALLS), None)
    complete = next((e for e in events if e.get("event") == "step_complete"), None)
    told: dict[str, Any] = {"name": start.get("step"), "kind": "unknown", "did": ""}
    if call is not None:
        kind = _CALLS[call["event"]]
        returned = next((e for e in events if e.get("event") == f"{kind}_return"), {})
        if kind == "model":
            told.update(_model_step(call, returned))
        elif kind == "tool":
            told.update(_tool_step(call, returned))
        elif kind == "function":
            told.update(
                kind="code",
                did=f"computed {call.get('function')} in code",
                answer=_show(returned.get("result")),
            )
        elif kind == "workflow":
            told.update(
                kind="workflow",
                did=f"ran workflow {call.get('workflow')}",
                answer=_show(returned.get("result")),
            )
        else:
            told.update(
                kind="agent",
                did=f"asked agent {call.get('agent')}",
                answer=_show(returned.get("result")),
            )
    recovered = start.get("recovered_from")
    if isinstance(recovered, dict):
        told["fallback"] = _show(recovered.get("message"), 120)
    if complete is None:
        told["unfinished"] = True
    else:
        seconds = _seconds(start, complete)
        if seconds is not None:
            told["seconds"] = round(seconds, 1)
    return told


def build_story(events: list[dict[str, Any]]) -> dict[str, Any]:
    """The run as data: its steps and totals, ready to print or to inspect."""

    start = events[0]
    if start.get("event") != "run_start":
        raise StoryError("A trace must begin with the start of a run")
    try:
        replay_events(events)
        replays, problem = True, ""
    except ValueError as exc:
        replays, problem = False, str(exc)
    groups: list[list[dict[str, Any]]] = []
    for event in events[1:]:
        if event.get("event") == "step_start":
            groups.append([event])
        elif groups and event.get("event") not in ("run_complete", "run_failed"):
            groups[-1].append(event)
    steps = [_step(group) for group in groups]
    last = events[-1]
    finished = last.get("event") == "run_complete"
    execution = _record(last.get("execution"))
    used = _record(execution.get("used"))
    limits = _record(execution.get("limits"))
    return {
        "workflow": start.get("workflow"),
        "program_hash": start.get("program_hash"),
        "inputs": start.get("inputs"),
        "steps": steps,
        "finished": finished,
        "result": last.get("result") if finished else None,
        "error": None
        if finished
        else _show(last.get("message") or last.get("error"), 200),
        "replays": replays,
        "replay_problem": problem,
        "seconds": execution.get("elapsed_seconds", _seconds(start, last)),
        "used": used,
        "limits": limits,
        "tokens": sum(step.get("tokens", 0) for step in steps),
    }


def to_text(story: dict[str, Any]) -> str:
    lines = [f"Run of {story['workflow']}"]
    for name, value in (story.get("inputs") or {}).items():
        lines.append(f"  given {name}: {_show(value)}")
    lines.append("")
    for number, step in enumerate(story["steps"], 1):
        took = f"  [{step['seconds']} s]" if step.get("seconds") else ""
        lines.append(f"{number}. {step['name']}: {step['did']}{took}")
        if "fallback" in step:
            lines.append(f"     the first attempt failed: {step['fallback']}")
            lines.append("     so the fallback ran instead")
        for reason in step.get("rejected", ()):
            lines.append(f"     rejected, then asked again: {reason}")
        if "vote" in step:
            lines.append(f"     vote: {step['vote']}")
        if "answer" in step:
            lines.append(f"     -> {step['answer']}")
        for quote in step.get("evidence", ()):
            lines.append(f"     backed by {quote}")
        if step.get("unfinished"):
            lines.append("     this step did not finish")
    lines.append("")
    if story["finished"]:
        lines.append(f"Result: {_show(story['result'], 400)}")
    else:
        lines.append(
            f"The run stopped before finishing: {story['error'] or 'no reason recorded'}"
        )
    used, limits = story["used"], story["limits"]
    parts = []
    if "model_calls" in used:
        cap = limits.get("max_model_calls")
        parts.append(
            f"{used['model_calls']} model call(s)"
            + (f" of {cap} allowed" if cap else "")
        )
    if used.get("tool_calls"):
        parts.append(f"{used['tool_calls']} tool or memory call(s)")
    if story["tokens"]:
        parts.append(f"{story['tokens']} tokens")
    if story["seconds"] is not None:
        parts.append(f"{round(float(story['seconds']), 1)} seconds")
    if parts:
        lines.append("Cost: " + ", ".join(parts))
    lines.append(
        "This trace replays: the story above is exactly what was recorded."
        if story["replays"]
        else f"Careful: this trace does not replay ({story['replay_problem']})."
    )
    return "\n".join(lines)
