"""Offline outcome replay with ordered event and record validation."""

import json
from pathlib import Path
from typing import Any

from .agent_steps import validate_agent_transcript
from .model_steps import strict_json
from .schema import validate


def _same_json(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(
        right, sort_keys=True, allow_nan=False
    )


def replay_trace(trace_path: str | Path) -> dict[str, Any]:
    events = [
        strict_json(line)
        for line in Path(trace_path).read_text(encoding="utf-8").splitlines()
    ]
    return replay_events(events)


def replay_events(events: list[Any], *, depth: int = 0) -> dict[str, Any]:
    if depth > 32:
        raise ValueError("Nested replay exceeds workflow depth limit")
    if len(events) < 6 or any(not isinstance(event, dict) for event in events):
        raise ValueError("Trace does not contain a completed run")
    start, finish = events[0], events[-1]
    if start.get("event") != "run_start" or finish.get("event") != "run_complete":
        raise ValueError("Trace must start and finish with one completed run")
    if not isinstance(start.get("workflow"), str) or start.get(
        "workflow"
    ) != finish.get("workflow"):
        raise ValueError("Trace workflow mismatch")
    version = start.get("trace_version", 1)
    if type(version) is not int or version not in {1, 2}:
        raise ValueError("Unsupported trace version")
    middle = events[1:-1]
    if len(middle) % 4:
        raise ValueError("Trace contains an incomplete step")
    seen: set[str] = set()
    result: Any = None
    for offset in range(0, len(middle), 4):
        step, call, returned, completed = middle[offset : offset + 4]
        kind = call.get("event")
        if not isinstance(kind, str):
            raise ValueError("Trace event name must be text")
        key = {
            "model_call": "model",
            "agent_call": "agent",
            "workflow_call": "workflow",
        }.get(kind, "tool")
        if (
            step.get("event") != "step_start"
            or kind not in {"model_call", "tool_call", "agent_call", "workflow_call"}
            or returned.get("event") != f"{key}_return"
            or completed.get("event") != "step_complete"
            or step.get("step") != completed.get("step")
            or not isinstance(step.get("step"), str)
            or step["step"] in seen
            or not isinstance(call.get(key), str)
            or call[key] != returned.get(key)
            or not isinstance(call.get("arguments"), dict)
        ):
            raise ValueError("Trace contains mismatched or reordered step events")
        seen.add(step["step"])
        result = returned.get("result")
        if not isinstance(result, dict) or not _same_json(
            result, completed.get("result")
        ):
            raise ValueError("Trace step outcome mismatch")
        if key == "workflow":
            nested = returned.get("events")
            if not isinstance(nested, list):
                raise ValueError("Workflow trace requires child events")
            child_result = replay_events(nested, depth=depth + 1)
            if (
                nested[0].get("workflow") != call["workflow"]
                or not _same_json(nested[0].get("inputs"), call["arguments"])
                or not _same_json(
                    nested[0].get("program_hash"), start.get("program_hash")
                )
                or not _same_json(child_result, result)
            ):
                raise ValueError("Nested workflow trace mismatch")
        if key in {"model", "agent"}:
            if not isinstance(call.get("input_schema"), dict) or not isinstance(
                call.get("output_schema"), dict
            ):
                raise ValueError("Model trace requires input and output schemas")
            validate(call["arguments"], call["input_schema"])
            validate(result, call["output_schema"])
            if returned.get("finish_reason") != "stop" or not isinstance(
                returned.get("content"), str
            ):
                raise ValueError("Model trace contains an incomplete response")
            if not _same_json(strict_json(returned["content"]), result):
                raise ValueError("Model response and recorded outcome differ")
            if key == "agent":
                validate_agent_transcript(call, returned)
    if not _same_json(result, finish.get("result")):
        raise ValueError("Trace final outcome mismatch")
    if start.get("trace_version") == 2:
        if not isinstance(start.get("output_schema"), dict):
            raise ValueError("Trace requires its workflow output schema")
        validate(result, start["output_schema"])
    return result
