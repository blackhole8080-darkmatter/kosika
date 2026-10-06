"""Offline outcome replay with ordered event and record validation."""

import json
from pathlib import Path
from typing import Any

from .agent_steps import validate_agent_transcript
from .agent_trace import replay_agent_events
from .evidence import EvidenceError
from .evidence import split as split_evidence
from .evidence import verify as verify_evidence
from .model_steps import fill_absent, strict_json
from .schema import validate


def _same_json(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(
        right, sort_keys=True, allow_nan=False
    )


def replay_trace(trace_path: str | Path) -> Any:
    events = [
        strict_json(line)
        for line in Path(trace_path).read_text(encoding="utf-8").splitlines()
    ]
    if (
        events
        and isinstance(events[0], dict)
        and events[0].get("event") == "agent_run_start"
    ):
        return replay_agent_events(events)
    return replay_events(events)


def _stated(quotes: Any) -> Any:
    """Evidence without its empty entries: a missing quote and a null one
    are the same statement."""

    if isinstance(quotes, dict):
        return {name: quote for name, quote in quotes.items() if quote is not None}
    return quotes


def _validate_function_events(events: list[Any], *, version: int, depth: int) -> None:
    if events and depth >= 128:
        raise ValueError("Nested function replay exceeds depth limit")
    if len(events) % 2 or any(not isinstance(event, dict) for event in events):
        raise ValueError("Function expression trace is incomplete")
    for offset in range(0, len(events), 2):
        call, returned = events[offset : offset + 2]
        if (
            call.get("event") != "function_call"
            or returned.get("event") != "function_return"
            or not isinstance(call.get("function"), str)
            or call.get("function") != returned.get("function")
            or not isinstance(call.get("arguments"), dict)
            or not isinstance(call.get("input_schema"), dict)
            or not isinstance(call.get("output_schema"), dict)
        ):
            raise ValueError("Function expression trace is mismatched")
        result = returned.get("result")
        validate(call["arguments"], call["input_schema"])
        validate(result, call["output_schema"])
        if version >= 5:
            argument_events = call.get("expression_events")
            body_events = returned.get("events")
            if not isinstance(argument_events, list) or not isinstance(
                body_events, list
            ):
                raise ValueError("Function trace requires nested event lists")
            _validate_function_events(argument_events, version=version, depth=depth)
            _validate_function_events(body_events, version=version, depth=depth + 1)


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
    if type(version) is not int or version not in {1, 2, 3, 4, 5}:
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
            "function_call": "function",
        }.get(kind, "tool")
        if (
            step.get("event") != "step_start"
            or kind
            not in {
                "model_call",
                "tool_call",
                "agent_call",
                "workflow_call",
                "function_call",
            }
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
        if (key != "function" and not isinstance(result, dict)) or not _same_json(
            result, completed.get("result")
        ):
            raise ValueError("Trace step outcome mismatch")
        if version >= 5:
            expression_events = call.get("expression_events")
            if not isinstance(expression_events, list):
                raise ValueError("Trace call requires function expression events")
            _validate_function_events(expression_events, version=version, depth=0)
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
            # A field the model left out reads as null where null is allowed,
            # exactly as it did when the run was made.
            answered = fill_absent(
                strict_json(returned["content"]), call["output_schema"]
            )
            if "evidence" in call:
                # The model answered with the record plus its quotes. Replay
                # proves the quotes again from the recorded input.
                if not isinstance(answered, dict) or not isinstance(result, dict):
                    raise ValueError("Evidence trace requires a record answer")
                answered, quotes = split_evidence(answered)
                if not _same_json(_stated(quotes), _stated(returned.get("evidence"))):
                    raise ValueError("Model response and recorded evidence differ")
                try:
                    verify_evidence(call["evidence"], call["arguments"], result, quotes)
                except EvidenceError as exc:
                    raise ValueError(f"Trace evidence does not hold: {exc}") from exc
            if not _same_json(answered, result):
                raise ValueError("Model response and recorded outcome differ")
            if key == "agent":
                validate_agent_transcript(call, returned)
        if key == "tool" and version >= 3:
            if not isinstance(call.get("input_schema"), dict) or not isinstance(
                call.get("output_schema"), dict
            ):
                raise ValueError("Tool trace requires input and output schemas")
            validate(call["arguments"], call["input_schema"])
            validate(result, call["output_schema"])
        if key == "function":
            if version < 4:
                raise ValueError("Function traces require trace version 4")
            if not isinstance(call.get("input_schema"), dict) or not isinstance(
                call.get("output_schema"), dict
            ):
                raise ValueError("Function trace requires input and output schemas")
            validate(call["arguments"], call["input_schema"])
            validate(result, call["output_schema"])
            if version >= 5:
                body_events = returned.get("events")
                if not isinstance(body_events, list):
                    raise ValueError("Function trace requires body events")
                _validate_function_events(body_events, version=version, depth=1)
    if not _same_json(result, finish.get("result")):
        raise ValueError("Trace final outcome mismatch")
    if version >= 2:
        if not isinstance(start.get("output_schema"), dict):
            raise ValueError("Trace requires its workflow output schema")
        validate(result, start["output_schema"])
    return result
