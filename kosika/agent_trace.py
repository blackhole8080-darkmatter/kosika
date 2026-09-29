"""Durable JSONL traces and offline replay for direct agent runs."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .model_steps import strict_json
from .schema import validate


def write_agent_trace(path: str | Path, events: list[dict[str, Any]]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        "\n".join(
            json.dumps(event, sort_keys=True, allow_nan=False) for event in events
        )
        + "\n"
    )
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=".kosika-agent-trace-",
            delete=False,
        ) as stream:
            temporary = stream.name
            stream.write(payload)
        os.replace(temporary, destination)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def replay_agent_trace(path: str | Path) -> Any:
    events = [
        strict_json(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
    ]
    return replay_agent_events(events)


def _same_json(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(
        right, sort_keys=True, allow_nan=False
    )


def _tool_message(call: dict[str, Any], result: Any) -> dict[str, Any]:
    return {
        "role": "tool",
        "name": call["name"],
        "tool_call_id": call["id"],
        "content": json.dumps(result, sort_keys=True, allow_nan=False),
        "tool_calls": [],
    }


def replay_agent_events(events: list[Any]) -> Any:
    """Validate a completed direct-agent trace without invoking providers/tools."""
    if len(events) < 3 or any(not isinstance(event, dict) for event in events):
        raise ValueError("Agent trace is incomplete")
    start, complete = events[0], events[-1]
    if (
        start.get("event") != "agent_run_start"
        or complete.get("event") != "agent_run_complete"
    ):
        raise ValueError("Agent trace must start and finish with one completed run")
    if start.get("trace_version") != 1:
        raise ValueError("Unsupported direct agent trace version")
    if not isinstance(start.get("agent"), str) or start["agent"] != complete.get(
        "agent"
    ):
        raise ValueError("Agent trace identity mismatch")
    if start.get("plan_hash") != complete.get("plan_hash"):
        raise ValueError("Agent trace plan hash mismatch")

    input_schema = start.get("input_schema")
    output_schema = start.get("output_schema")
    if input_schema is not None:
        if not isinstance(input_schema, dict) or not isinstance(
            start.get("input"), dict
        ):
            raise ValueError(
                "Structured agent trace requires an input object and schema"
            )
        validate(start["input"], input_schema)
    if output_schema is not None and not isinstance(output_schema, dict):
        raise ValueError("Agent trace output schema must be an object")

    tools = start.get("tools", {})
    if not isinstance(tools, dict):
        raise ValueError("Agent trace tools must be an object")
    for name, schemas in tools.items():
        if not isinstance(name, str) or not isinstance(schemas, dict):
            raise ValueError("Agent trace contains invalid tool schemas")
        if not isinstance(schemas.get("input_schema"), dict) or not isinstance(
            schemas.get("output_schema"), dict
        ):
            raise ValueError("Agent trace tool schemas are incomplete")

    expected_messages: list[dict[str, Any]] | None = None
    index = 1
    model_steps = 0
    finished = False
    while index < len(events) - 1:
        request = events[index]
        response = events[index + 1] if index + 1 < len(events) - 1 else None
        if (
            not isinstance(request, dict)
            or request.get("event") != "agent_model_request"
            or not isinstance(response, dict)
            or response.get("event") != "agent_model_response"
            or request.get("step") != response.get("step")
            or not isinstance(request.get("messages"), list)
        ):
            raise ValueError("Agent trace contains mismatched model events")
        if expected_messages is None:
            expected_messages = request["messages"]
        elif request["messages"] != expected_messages:
            raise ValueError("Agent trace request messages are inconsistent")
        model_steps += 1
        tool_calls = response.get("tool_calls")
        if not isinstance(tool_calls, list):
            raise ValueError("Agent trace response tool calls must be a list")
        assistant = {
            "role": "assistant",
            "content": response.get("content") or "",
            "tool_calls": tool_calls,
            "tool_call_id": None,
            "name": None,
        }
        expected_messages = [*expected_messages, assistant]
        index += 2
        if not tool_calls:
            if response.get("finish_reason") != "stop":
                raise ValueError("Agent trace final response is incomplete")
            finished = True
            break
        for call in tool_calls:
            if (
                not isinstance(call, dict)
                or not isinstance(call.get("id"), str)
                or not isinstance(call.get("name"), str)
                or not isinstance(call.get("arguments"), dict)
            ):
                raise ValueError("Agent trace contains an invalid tool call")
            if index >= len(events) - 1:
                raise ValueError("Agent trace is missing a tool result")
            tool_event = events[index]
            index += 1
            schemas = tools.get(call["name"])
            if (
                not isinstance(tool_event, dict)
                or tool_event.get("event") != "agent_tool_call"
                or tool_event.get("step") != request["step"]
                or tool_event.get("id") != call["id"]
                or tool_event.get("name") != call["name"]
                or not _same_json(tool_event.get("arguments"), call["arguments"])
                or not isinstance(schemas, dict)
            ):
                raise ValueError("Agent trace tool event mismatch")
            validate(call["arguments"], schemas["input_schema"])
            validate(tool_event.get("result"), schemas["output_schema"])
            expected_messages.append(_tool_message(call, tool_event["result"]))

    if not finished or index != len(events) - 1:
        raise ValueError("Agent trace has no final response")
    if complete.get("model_steps") != model_steps:
        raise ValueError("Agent trace model step count mismatch")
    if complete.get("messages") != expected_messages:
        raise ValueError("Agent trace final messages mismatch")
    content = complete.get("content")
    if not isinstance(content, str):
        raise ValueError("Agent trace final content must be text")
    structured_output = complete.get("structured_output")
    if output_schema is not None:
        parsed = strict_json(content)
        validate(parsed, output_schema)
        if not isinstance(parsed, dict) or not _same_json(parsed, structured_output):
            raise ValueError("Agent trace structured output mismatch")
        return parsed
    if structured_output is not None:
        raise ValueError("Unstructured agent trace cannot contain structured output")
    return content
