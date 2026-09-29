"""Normalized Kosika runtime events and strict JSONL validation.

The event envelope is the boundary shared by runtime telemetry, the Synapse
bridge, replay summaries, and future live SSE/WebSocket delivery. Raw runtime
trace events remain an internal replay format; normalized events are validated
before they leave the runtime boundary.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from .model_steps import strict_json

EVENT_SCHEMA = "synapse.kosika.event/v1"
EVENT_BRIDGE_VERSION = 1
_ALLOWED_STATUSES = {
    "started",
    "completed",
    "observed",
    "pending",
    "granted",
    "denied",
    "passed",
    "failed",
    "cancelled",
    "error",
}
_EVENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
_EVENT_TYPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]*$")
_MAX_PAYLOAD_BYTES = 8 * 1024
_MAX_PAYLOAD_DEPTH = 6
_MAX_PAYLOAD_ITEMS = 128
_MAX_PAYLOAD_TEXT = 512


class EventValidationError(ValueError):
    """Raised when a normalized event or event JSONL stream is invalid."""


@dataclass(frozen=True)
class EventEnvelope:
    """Validated normalized event with forward-compatible metadata fields."""

    schema: str
    bridge_version: int
    event_id: str
    sequence: int
    timestamp: str
    source: str
    type: str
    status: str
    replay: bool
    payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, event: Mapping[str, Any]) -> "EventEnvelope":
        data = _validate_event_mapping(event)
        known = {
            "schema",
            "bridge_version",
            "event_id",
            "sequence",
            "timestamp",
            "source",
            "type",
            "status",
            "replay",
            "payload",
        }
        payload = data.get("payload", {})
        return cls(
            schema=data["schema"],
            bridge_version=data["bridge_version"],
            event_id=data["event_id"],
            sequence=data["sequence"],
            timestamp=data["timestamp"],
            source=data["source"],
            type=data["type"],
            status=data["status"],
            replay=data["replay"],
            payload=dict(payload),
            metadata={key: value for key, value in data.items() if key not in known},
        )

    def to_dict(self) -> dict[str, Any]:
        event: dict[str, Any] = {
            "schema": self.schema,
            "bridge_version": self.bridge_version,
            "event_id": self.event_id,
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "source": self.source,
            "type": self.type,
            "status": self.status,
            "replay": self.replay,
        }
        if self.payload:
            event["payload"] = dict(self.payload)
        event.update(self.metadata)
        return event


def _require_text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise EventValidationError(f"Event field {key!r} must be non-empty text")
    return value


def _validate_timestamp(value: str) -> None:
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise EventValidationError("Event timestamp must be ISO-8601 text") from exc
    if parsed.tzinfo is None:
        raise EventValidationError("Event timestamp must include a timezone")


def _validate_payload_value(value: Any, depth: int = 0) -> None:
    if depth > _MAX_PAYLOAD_DEPTH:
        raise EventValidationError("Event payload is nested too deeply")
    if isinstance(value, dict):
        if len(value) > _MAX_PAYLOAD_ITEMS:
            raise EventValidationError("Event payload contains too many object fields")
        for key, child in value.items():
            if not isinstance(key, str) or not key or len(key) > _MAX_PAYLOAD_TEXT:
                raise EventValidationError(
                    "Event payload contains an invalid field name"
                )
            _validate_payload_value(child, depth + 1)
        return
    if isinstance(value, list):
        if len(value) > _MAX_PAYLOAD_ITEMS:
            raise EventValidationError("Event payload contains too many array items")
        for child in value:
            _validate_payload_value(child, depth + 1)
        return
    if isinstance(value, str):
        if len(value) > _MAX_PAYLOAD_TEXT:
            raise EventValidationError("Event payload contains oversized text")
        return
    if value is None or type(value) in (bool, int, float):
        return
    raise EventValidationError("Event payload contains an unsupported value")


def _validate_event_mapping(event: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(event, Mapping):
        raise EventValidationError("Event must be a JSON object")
    data = dict(event)
    if data.get("schema") != EVENT_SCHEMA:
        raise EventValidationError(f"Unsupported event schema {data.get('schema')!r}")
    bridge_version = data.get("bridge_version")
    if type(bridge_version) is not int or bridge_version != EVENT_BRIDGE_VERSION:
        raise EventValidationError("Unsupported event bridge version")
    event_id = _require_text(data, "event_id")
    if not _EVENT_ID_RE.fullmatch(event_id):
        raise EventValidationError("Event id has invalid characters")
    sequence = data.get("sequence")
    if type(sequence) is not int or sequence < 1:
        raise EventValidationError("Event sequence must be a positive integer")
    timestamp = _require_text(data, "timestamp")
    _validate_timestamp(timestamp)
    _require_text(data, "bus")
    _require_text(data, "source")
    _require_text(data, "region")
    event_type = _require_text(data, "type")
    if not _EVENT_TYPE_RE.fullmatch(event_type):
        raise EventValidationError("Event type has invalid characters")
    status = _require_text(data, "status")
    if status not in _ALLOWED_STATUSES:
        raise EventValidationError(f"Unknown event status {status!r}")
    if type(data.get("replay")) is not bool:
        raise EventValidationError("Event replay flag must be boolean")
    payload = data.get("payload", {})
    if not isinstance(payload, dict):
        raise EventValidationError("Event payload must be an object")
    _validate_payload_value(payload)
    try:
        payload_size = len(
            json.dumps(payload, sort_keys=True, allow_nan=False).encode("utf-8")
        )
    except (TypeError, ValueError) as exc:
        raise EventValidationError("Event payload is not strict JSON") from exc
    if payload_size > _MAX_PAYLOAD_BYTES:
        raise EventValidationError("Event payload exceeds the size limit")
    for key in (
        "bus",
        "region",
        "run_id",
        "parent_event_id",
        "workflow",
        "step",
        "agent",
        "model",
        "tool",
        "operation",
        "transport",
        "access",
        "binding",
    ):
        if key in data and (not isinstance(data[key], str) or not data[key].strip()):
            raise EventValidationError(f"Event field {key!r} must be non-empty text")
    try:
        json.dumps(data, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise EventValidationError(
            "Event contains values that are not strict JSON"
        ) from exc
    return data


def validate_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return one normalized event as a JSON-compatible mapping."""

    return EventEnvelope.from_dict(event).to_dict()


def validate_events(
    events: Sequence[Mapping[str, Any]], *, sequence_start: int = 1
) -> tuple[dict[str, Any], ...]:
    """Validate event shape, ordering, and unique IDs for one stream."""

    if sequence_start < 1:
        raise EventValidationError("Event sequence start must be positive")
    validated: list[dict[str, Any]] = []
    event_ids: set[str] = set()
    for offset, event in enumerate(events):
        item = validate_event(event)
        expected = sequence_start + offset
        if item["sequence"] != expected:
            raise EventValidationError(
                f"Event sequence gap: expected {expected}, got {item['sequence']}"
            )
        if item["event_id"] in event_ids:
            raise EventValidationError(f"Duplicate event id {item['event_id']!r}")
        event_ids.add(item["event_id"])
        validated.append(item)
    return tuple(validated)


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Read strict JSON objects from JSONL without silently skipping lines."""

    trace_path = Path(path)
    try:
        lines = trace_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise EventValidationError(f"Could not read JSONL {trace_path}: {exc}") from exc
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            raise EventValidationError(f"JSONL line {line_number} is blank")
        try:
            value = strict_json(line)
        except (TypeError, ValueError) as exc:
            raise EventValidationError(
                f"JSONL line {line_number} is invalid: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise EventValidationError(f"JSONL line {line_number} must be an object")
        events.append(value)
    return events


def read_event_jsonl(path: str | Path) -> tuple[dict[str, Any], ...]:
    """Read and validate a normalized event JSONL stream."""

    return validate_events(read_jsonl(path))


def write_event_jsonl(
    path: str | Path, events: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Any], ...]:
    """Validate and atomically replace a normalized event JSONL file."""

    validated = validate_events(events)
    event_path = Path(path)
    event_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=event_path.parent,
            prefix=f".{event_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = handle.name
            handle.write(
                "\n".join(
                    json.dumps(event, sort_keys=True, allow_nan=False)
                    for event in validated
                )
                + ("\n" if validated else "")
            )
        os.replace(temporary_path, event_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
    return validated


__all__ = [
    "EVENT_BRIDGE_VERSION",
    "EVENT_SCHEMA",
    "EventEnvelope",
    "EventValidationError",
    "read_event_jsonl",
    "read_jsonl",
    "validate_event",
    "validate_events",
    "write_event_jsonl",
]
