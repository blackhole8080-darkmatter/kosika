"""Safe, normalized telemetry for connecting Kosika to a Synapse event bus.

The bridge deliberately does not forward raw prompts, arguments, results, model
content, or schemas.  It emits metadata and one-way summaries so a UI can show
workflow activity without becoming a data exfiltration channel.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .ast import Program
from .checker import check
from .events import (
    EVENT_BRIDGE_VERSION,
    EVENT_SCHEMA,
    read_jsonl,
    validate_event,
    validate_events,
)
from .runtime import run
from .trace_replay import replay_events

BRIDGE_VERSION = EVENT_BRIDGE_VERSION
SOURCE = "kosika"
REGION = "kosika"

_EVENT_NAMES = {
    "run_start": "kosika_workflow_start",
    "step_start": "kosika_step_start",
    "model_call": "kosika_model_call",
    "model_return": "kosika_model_return",
    "agent_call": "kosika_agent_call",
    "agent_return": "kosika_agent_return",
    "tool_call": "kosika_tool_call",
    "tool_return": "kosika_tool_return",
    "workflow_call": "kosika_workflow_call",
    "workflow_return": "kosika_workflow_return",
    "function_call": "kosika_function_call",
    "function_return": "kosika_function_return",
    "step_complete": "kosika_step_complete",
    "run_complete": "kosika_workflow_complete",
}

_DEMO_SOURCE = """
record DiskRequest {
  mount: Text
}

record DiskStatus {
  mount: Text
  used_percent: Number
}

tool check_disk {
  source mcp "local_mock"
  input: DiskRequest
  operation: "get_disk_info"
  access: observe
  output: DiskStatus
}

agent monitor {
  can: [check_disk]
  cannot: []
}

workflow main() -> DiskStatus {
  step inspect {
    let result = execute monitor.check_disk(mount: "/")
    return result
  }
}
"""


@dataclass(frozen=True)
class BridgeResult:
    """A JSON-safe result from a bridge execution or offline replay."""

    result: dict[str, Any] | None
    events: tuple[dict[str, Any], ...]
    raw_events: tuple[dict[str, Any], ...]
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.result is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "result": self.result,
            "events": list(self.events),
            "error": self.error,
        }


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return repr(value)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _summary(value: Any) -> dict[str, Any]:
    """Return metadata for a value without including its content."""

    if value is None:
        value_type = "null"
        size = 0
    elif isinstance(value, bool):
        value_type = "bool"
        size = 1
    elif isinstance(value, (int, float)):
        value_type = "number"
        size = 1
    elif isinstance(value, str):
        value_type = "text"
        size = len(value)
    elif isinstance(value, dict):
        value_type = "object"
        size = len(value)
    elif isinstance(value, list):
        value_type = "array"
        size = len(value)
    else:
        value_type = type(value).__name__
        size = 1
    return {"type": value_type, "size": size, "sha256": _hash(value)}


def _safe_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = re.sub(r"[^a-zA-Z0-9_.:-]", "_", value)
    return candidate[:96] or None


def _safe_error(error: BaseException) -> str:
    message = str(error).replace("\r", " ").replace("\n", " ").strip()
    return message[:240] or type(error).__name__


def _status_for(raw_event: str) -> str:
    if raw_event.endswith("_start") or raw_event.endswith("_call"):
        return "started"
    if raw_event.endswith("_complete") or raw_event.endswith("_return"):
        return "completed"
    return "observed"


def _event(
    event_type: str,
    sequence: int,
    *,
    raw: Mapping[str, Any] | None = None,
    status: str = "observed",
    workflow: str | None = None,
    step: str | None = None,
    replay: bool = False,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source_event = dict(raw or {})
    workflow_name = workflow or _safe_name(source_event.get("workflow"))
    step_name = step or _safe_name(source_event.get("step"))
    identity = {
        "sequence": sequence,
        "type": event_type,
        "workflow": workflow_name,
        "step": step_name,
        "replay": replay,
    }
    event_id = f"kosika-{sequence:04d}-{_hash(identity)[:12]}"
    timestamp = source_event.get("timestamp")
    event: dict[str, Any] = {
        "schema": EVENT_SCHEMA,
        "bridge_version": BRIDGE_VERSION,
        "event_id": event_id,
        "sequence": sequence,
        "timestamp": timestamp if isinstance(timestamp, str) else _timestamp(),
        "bus": "synapse",
        "source": SOURCE,
        "region": REGION,
        "type": event_type,
        "status": status,
        "replay": replay,
    }
    if workflow_name is not None:
        event["workflow"] = workflow_name
    if step_name is not None:
        event["step"] = step_name
    for key in (
        "agent",
        "function",
        "model",
        "tool",
        "operation",
        "transport",
        "access",
        "binding",
    ):
        value = _safe_name(source_event.get(key))
        if value is not None:
            event[key] = value
    for key in ("trace_version", "program_hash", "max_steps"):
        value = source_event.get(key)
        if isinstance(value, (int, str)):
            event[key] = value
    if payload:
        event["payload"] = dict(payload)
    return validate_event(event)


def normalize_event(
    raw_event: Mapping[str, Any],
    sequence: int,
    *,
    replay: bool = False,
) -> dict[str, Any]:
    """Normalize one raw Kosika runtime event to the Synapse contract."""

    raw_name = raw_event.get("event")
    if not isinstance(raw_name, str):
        raw_name = "unknown"
    event_type = _EVENT_NAMES.get(
        raw_name, f"kosika_{_safe_name(raw_name) or 'unknown'}"
    )
    payload: dict[str, Any] = {}
    for key in ("arguments", "result", "inputs", "content", "output_schema"):
        if key in raw_event:
            payload[key] = _summary(raw_event[key])
    for key in ("agent", "tool"):
        identity = _safe_name(raw_event.get(key))
        if identity is not None:
            payload[key] = identity
    if "events" in raw_event and isinstance(raw_event["events"], list):
        payload["child_event_count"] = len(raw_event["events"])
    return _event(
        event_type,
        sequence,
        raw=raw_event,
        status=_status_for(raw_name),
        payload=payload,
        replay=replay,
    )


def normalize_events(
    raw_events: Sequence[Mapping[str, Any]],
    *,
    replay: bool = False,
    sequence_start: int = 1,
) -> tuple[dict[str, Any], ...]:
    """Normalize a complete raw trace while preserving event order."""

    normalized = tuple(
        normalize_event(event, sequence_start + offset, replay=replay)
        for offset, event in enumerate(raw_events)
    )
    return validate_events(normalized, sequence_start=sequence_start)


def _check_event(
    sequence: int,
    workflow_name: str,
    program: Program,
    *,
    status: str,
    error: BaseException | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "program_hash": _hash(asdict(program)),
        "workflow": workflow_name,
    }
    if error is not None:
        payload["error_type"] = type(error).__name__
        payload["message"] = _safe_error(error)
    return _event(
        "kosika_check",
        sequence,
        status=status,
        workflow=workflow_name,
        payload=payload,
    )


def execute(
    program: Program,
    workflow_name: str,
    trace_path: str | Path | None = None,
    *,
    inputs: dict[str, Any] | None = None,
    model_bindings: Mapping[str, Any] | None = None,
    tool_bindings: Mapping[str, Any] | None = None,
) -> BridgeResult:
    """Check and execute a Kosika workflow, returning normalized telemetry.

    Runtime exceptions are captured in the result and represented as a
    ``kosika_error`` event.  The bridge never turns a failed execution into a
    successful UI event.
    """

    events: list[dict[str, Any]] = []
    try:
        check(program)
        events.append(_check_event(1, workflow_name, program, status="passed"))
    except Exception as error:
        events.append(
            _check_event(1, workflow_name, program, status="failed", error=error)
        )
        events.append(
            _event(
                "kosika_error",
                2,
                workflow=workflow_name,
                status="error",
                payload={
                    "error_type": type(error).__name__,
                    "message": _safe_error(error),
                },
            )
        )
        return BridgeResult(
            None,
            validate_events(events),
            (),
            _safe_error(error),
        )

    raw_events: list[dict[str, Any]] = []
    try:
        result = run(
            program,
            workflow_name,
            trace_path,
            inputs=inputs,
            model_bindings=model_bindings,
            tool_bindings=tool_bindings,
            _events_out=raw_events,
        )
    except Exception as error:
        events.extend(normalize_events(raw_events, sequence_start=len(events) + 1))
        events.append(
            _event(
                "kosika_error",
                len(events) + 1,
                workflow=workflow_name,
                status="error",
                payload={
                    "error_type": type(error).__name__,
                    "message": _safe_error(error),
                },
            )
        )
        return BridgeResult(
            None,
            validate_events(events),
            tuple(raw_events),
            _safe_error(error),
        )
    events.extend(normalize_events(raw_events, sequence_start=len(events) + 1))
    return BridgeResult(result, validate_events(events), tuple(raw_events))


def replay_bridge(trace_path: str | Path) -> BridgeResult:
    """Validate and summarize a trace without invoking models or tools."""

    path = Path(trace_path)
    try:
        raw_events = tuple(read_jsonl(path))
        result = replay_events(list(raw_events))
        workflow = _safe_name(raw_events[0].get("workflow")) if raw_events else None
        replay_event = _event(
            "kosika_replay",
            1,
            workflow=workflow,
            status="completed",
            replay=True,
            payload={
                "trace_event_count": len(raw_events),
                "result": _summary(result),
            },
        )
        normalized = normalize_events(raw_events, replay=True, sequence_start=2)
        replay_events_out = validate_events((replay_event, *normalized))
        return BridgeResult(result, replay_events_out, raw_events)
    except Exception as error:
        return BridgeResult(
            None,
            (
                _event(
                    "kosika_replay",
                    1,
                    status="failed",
                    replay=True,
                    payload={
                        "error_type": type(error).__name__,
                        "message": _safe_error(error),
                    },
                ),
            ),
            (),
            _safe_error(error),
        )


def demo_program() -> Program:
    """Return the dependency-free fixture used by the wheel prototype."""

    from .parser import parse

    return parse(_DEMO_SOURCE)


def demo_run(trace_path: str | Path | None = None) -> BridgeResult:
    """Run the local-only demo used by the accessible Synapse prototype."""

    return execute(demo_program(), "main", trace_path)


__all__ = [
    "BRIDGE_VERSION",
    "EVENT_SCHEMA",
    "BridgeResult",
    "demo_program",
    "demo_run",
    "execute",
    "normalize_event",
    "normalize_events",
    "replay_bridge",
]
