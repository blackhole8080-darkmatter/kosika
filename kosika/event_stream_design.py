"""Bounded stream-to-design adapter: deterministic timelines from event streams.

The adapter reads validated normalized ``synapse.kosika.event/v1`` events and
produces a design manifest consumed by :mod:`kosika.design`. It is intentionally
read-only: a timeline never grants capabilities, executes a workflow, or turns
observed telemetry into authority. Malformed input fails closed; the adapter
never invents structure that the validated stream does not contain.
"""

from __future__ import annotations

import hashlib
import html
import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .ast import Program
from .design import DesignError, DesignManifest, validate_design
from .events import (
    EVENT_BRIDGE_VERSION as BRIDGE_VERSION,
)
from .events import (
    EVENT_SCHEMA,
    EventValidationError,
    validate_event,
    validate_events,
)

SOURCE = "kosika"
TIMELINE_NODE_KIND = "note"
TIMELINE_EDGE_KIND = "connects"
DESIGN_VERSION = 1
MAX_NODES = 512

_EVENT_LABELS = {
    "kosika_workflow_start": "workflow start",
    "kosika_workflow_complete": "workflow complete",
    "kosika_step_start": "step start",
    "kosika_step_complete": "step complete",
    "kosika_check": "check",
    "kosika_check_failed": "check failed",
    "kosika_approval_pending": "approval pending",
    "kosika_approval_granted": "approval granted",
    "kosika_error": "error",
    "kosika_replay": "replay",
}

_STATUS_LABELS = {
    "started": "started",
    "completed": "completed",
    "observed": "observed",
    "pending": "pending",
    "granted": "granted",
    "denied": "denied",
    "passed": "passed",
    "failed": "failed",
    "cancelled": "cancelled",
    "error": "error",
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def program_hash(program: Program) -> str:
    """Deterministic hash of a checked program for lifecycle telemetry."""

    return _digest(asdict(program))


def _safe_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = "".join(
        character if character.isalnum() or character in "_.:-" else "_"
        for character in value
    )
    return candidate[:96] or None


def _event_kind(event_type: str) -> str:
    """Group one normalized event type into a coarse, bounded kind.

    Known types map precisely. Unknown ``kosika_*`` types stay observable as
    ``execution`` so future producers remain renderable; anything else violates
    the bridge naming contract and fails closed.
    """

    if event_type in {"kosika_error"}:
        return "failure"
    if event_type in {"kosika_approval_pending", "kosika_approval_granted"}:
        return "approval"
    if event_type in {"kosika_check", "kosika_check_failed"}:
        return "check"
    if event_type in {"kosika_replay"}:
        return "replay"
    if event_type.startswith("kosika_"):
        return "execution"
    raise DesignError(f"Unsupported event type {event_type!r} for a timeline")


def _label_for(event_type: str, status: str) -> str:
    label = _EVENT_LABELS.get(event_type, event_type)
    suffix = _STATUS_LABELS.get(status, status)
    return f"{label} ({suffix})"


def build_timeline_design(
    events: Sequence[Mapping[str, Any]],
    design_id: str,
    name: str,
    *,
    max_nodes: int = MAX_NODES,
) -> DesignManifest:
    """Build one deterministic workflow timeline design from validated events."""

    if not isinstance(design_id, str) or not design_id.strip():
        raise DesignError("Timeline design id must be non-empty text")
    if not isinstance(name, str) or not name.strip():
        raise DesignError("Timeline design name must be non-empty text")
    try:
        validated = validate_events(events)
    except EventValidationError as exc:
        raise DesignError(f"Timeline source events are invalid: {exc}") from exc
    if not validated:
        raise DesignError("A timeline needs at least one normalized event")
    if len(validated) > max_nodes:
        raise DesignError(
            f"Timeline source exceeds the {max_nodes}-node bound; "
            "aggregate or filter the stream first"
        )

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for index, event in enumerate(validated):
        sequence = int(event["sequence"])
        event_type = str(event["type"])
        kind = _event_kind(event_type)
        workflow = _safe_name(event.get("workflow"))
        step = _safe_name(event.get("step"))
        node_id = f"event.{sequence}.{event['event_id']}"
        label = _label_for(event_type, str(event["status"]))
        nodes.append(
            {
                "id": node_id,
                "label": label,
                "kind": TIMELINE_NODE_KIND,
                "position": {"x": 80 + index * 190.0, "y": 120.0},
                "size": {"width": 160, "height": 64},
                "style": {
                    "stage": kind,
                    "sequence": sequence,
                    "timestamp": event["timestamp"],
                    "event_id": event["event_id"],
                    "workflow": workflow,
                    "step": step,
                },
            }
        )
        if index > 0:
            previous = validated[index - 1]
            edges.append(
                {
                    "id": f"edge.{sequence}",
                    "source": f"event.{int(previous['sequence'])}.{previous['event_id']}",
                    "target": node_id,
                    "kind": TIMELINE_EDGE_KIND,
                }
            )
    first_sequence = int(validated[0]["sequence"])
    first_type = str(validated[0]["type"])
    first_label = _label_for(first_type, str(validated[0]["status"]))
    first_event_id = str(validated[0]["event_id"])
    first_kind = _event_kind(first_type)
    workflow_name = _safe_name(validated[0].get("workflow")) or "timeline"
    design_digest = _digest(
        {
            "event_count": len(validated),
            "first_sequence": first_sequence,
            "first_event_id": first_event_id,
            "first_type": first_type,
            "first_label": first_label,
            "first_stage": first_kind,
            "workflow": workflow_name,
        }
    )
    manifest_data = {
        "manifestVersion": 1,
        "designVersion": DESIGN_VERSION,
        "design": {
            "id": design_id,
            "name": name,
            "kind": "event_timeline",
            "canvas": {"width": 1400, "height": 400, "unit": "px"},
            "source": SOURCE,
            "digest": design_digest,
            "first_event_id": first_event_id,
            "first_sequence": first_sequence,
            "stage": first_kind,
            "stage_label": first_label,
            "workflow": workflow_name,
        },
        "nodes": nodes,
        "edges": edges,
        "views": [
            {"id": "timeline-view", "name": "Execution timeline", "kind": "workflow"}
        ],
    }
    try:
        return validate_design(manifest_data)
    except DesignError as exc:
        raise DesignError(f"Derived timeline design is invalid: {exc}") from exc


def timeline_stage_summary(manifest: DesignManifest) -> dict[str, Any]:
    """Deterministic stage summary for one timeline design manifest."""

    stages: list[str] = []
    for node in manifest.nodes:
        raw_style = node.get("style")
        style = raw_style if isinstance(raw_style, dict) else {}
        stage = style.get("stage")
        if isinstance(stage, str):
            stages.append(stage)
    return {
        "stage": stages[0] if stages else "replay",
        "stages": stages,
        "first_stage": stages[0] if stages else "replay",
        "event_count": len(stages),
    }


def timeline_svg_caption(manifest: DesignManifest) -> str:
    """One deterministic HTML-escaped caption line for SVG timeline rendering."""

    digest = manifest.digest[:12]
    summary = timeline_stage_summary(manifest)
    stage = html.escape(str(summary["first_stage"]))
    return f"Timeline stage {stage} · digest {digest}"


class BridgePublicationError(EventValidationError):
    """Raised when a producer cannot publish one normalized lifecycle event."""


def _lifecycle_event(
    sequence: int,
    event_type: str,
    status: str,
    *,
    workflow: str | None = None,
    error: Exception | None = None,
) -> dict[str, Any]:
    """Build one validated normalized lifecycle event for producers.

    Shared by the CLI and workbench so checker, approval, and failure events
    keep one identity scheme. Payloads carry the error type and a bounded,
    single-line message; content stays inside the producer boundary.
    """

    event: dict[str, Any] = {
        "schema": EVENT_SCHEMA,
        "bridge_version": BRIDGE_VERSION,
        "event_id": f"kosika-{sequence:04d}-{event_type}",
        "sequence": sequence,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "bus": "synapse",
        "source": SOURCE,
        "region": SOURCE,
        "type": event_type,
        "status": status,
        "replay": False,
    }
    if workflow is not None:
        event["workflow"] = workflow
    if error is not None:
        message = str(error).replace("\r", " ").replace("\n", " ").strip()
        event["payload"] = {
            "error_type": type(error).__name__,
            "message": message[:256],
        }
    try:
        return validate_event(event)
    except EventValidationError as exc:
        raise BridgePublicationError(f"Lifecycle event is invalid: {exc}") from exc


def publish_check_event(
    sequence: int,
    program: Program | None,
    workflow: str,
    *,
    error: Exception | None = None,
) -> dict[str, Any]:
    """Publish one validated ``kosika_check`` event for a program check.

    ``program`` is ``None`` when loading itself failed; the event then omits
    the program hash instead of inventing one.
    """

    status = "failed" if error is not None else "passed"
    event = _lifecycle_event(
        sequence,
        "kosika_check" if error is None else "kosika_check_failed",
        status,
        workflow=workflow,
        error=error,
    )
    payload: dict[str, Any] = dict(event.get("payload", {}))
    if program is not None:
        payload["program_hash"] = program_hash(program)
    if payload:
        event["payload"] = payload
    return validate_event(event)


def publish_failure_event(
    sequence: int,
    workflow: str | None,
    error: Exception,
) -> dict[str, Any]:
    """Publish one validated ``kosika_error`` event for a producer failure."""

    return _lifecycle_event(
        sequence, "kosika_error", "error", workflow=workflow, error=error
    )


def publish_approval_event(sequence: int) -> dict[str, Any]:
    """Publish one validated ``kosika_approval_pending`` lifecycle event."""

    return _lifecycle_event(sequence, "kosika_approval_pending", "pending")


__all__ = [
    "BRIDGE_VERSION",
    "BridgePublicationError",
    "DESIGN_VERSION",
    "MAX_NODES",
    "SOURCE",
    "TIMELINE_EDGE_KIND",
    "TIMELINE_NODE_KIND",
    "build_timeline_design",
    "program_hash",
    "publish_approval_event",
    "publish_check_event",
    "publish_failure_event",
    "timeline_stage_summary",
    "timeline_svg_caption",
]
