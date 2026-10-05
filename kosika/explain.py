"""Deterministic explanations built from Kosika's checked mindmap and traces."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, deque
from pathlib import Path
from typing import Any, Iterable

from .ast import Program, program_payload
from .extensions import ExtensionManifest
from .mindmap import build_mindmap, to_json, to_mermaid
from .model_steps import strict_json
from .trace_replay import replay_events

EXPLAIN_VERSION = 1


class ExplainError(ValueError):
    """Raised when a deterministic explanation cannot be produced safely."""


def _program_hash(program: Program) -> str:
    payload = json.dumps(program_payload(program), sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_names(values: Iterable[str], label: str) -> tuple[str, ...]:
    names = tuple(values)
    if any(not isinstance(value, str) or not value.strip() for value in names):
        raise ExplainError(f"{label} selections must be nonempty names")
    if len(set(names)) != len(names):
        raise ExplainError(f"Duplicate {label} selection")
    return tuple(sorted(names))


def _subset_mindmap(
    mindmap: dict[str, Any],
    workflow_names: tuple[str, ...],
    record_names: tuple[str, ...],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    nodes = mindmap.get("nodes")
    edges = mindmap.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ExplainError("Mindmap has invalid node or edge collections")
    by_id: dict[str, dict[str, Any]] = {
        node_id: node
        for node in nodes
        if isinstance(node, dict) and isinstance(node_id := node.get("id"), str)
    }
    roots: list[str] = []
    for name in workflow_names:
        node_id = f"workflow:{name}"
        node = by_id.get(node_id)
        if node is None or node.get("kind") != "workflow":
            raise ExplainError(f"Unknown workflow {name!r}")
        roots.append(node_id)
    for name in record_names:
        node_id = f"record:{name}"
        node = by_id.get(node_id)
        if node is None or node.get("kind") != "record":
            raise ExplainError(f"Unknown record {name!r}")
        roots.append(node_id)

    selected_ids: set[str]
    if not roots:
        selected_ids = set(by_id)
    else:
        neighbors: dict[str, set[str]] = {}
        for edge in edges:
            if not isinstance(edge, dict):
                raise ExplainError("Mindmap has an invalid edge")
            source = edge.get("source")
            target = edge.get("target")
            if not isinstance(source, str) or not isinstance(target, str):
                raise ExplainError("Mindmap edge endpoints must be text")
            source_node = by_id.get(source, {})
            target_node = by_id.get(target, {})
            if (
                source_node.get("kind") == "module"
                or target_node.get("kind") == "module"
            ):
                continue
            neighbors.setdefault(source, set()).add(target)
            neighbors.setdefault(target, set()).add(source)
        selected_ids = set(roots)
        queue = deque(roots)
        while queue:
            source = queue.popleft()
            for target in sorted(neighbors.get(source, ())):
                if target not in selected_ids:
                    selected_ids.add(target)
                    queue.append(target)
        selected_module_names: set[str] = {
            module
            for node_id in selected_ids
            if isinstance((node := by_id.get(node_id)), dict)
            and isinstance(module := node.get("module"), str)
        }
        selected_ids.update(
            node_id
            for node_id, node in by_id.items()
            if node.get("kind") == "module"
            and node.get("label") in selected_module_names
        )
        selected_ids.update(
            endpoint
            for edge in edges
            if isinstance(edge, dict)
            and edge.get("relation") == "imports"
            and (
                edge.get("source") in selected_ids or edge.get("target") in selected_ids
            )
            for endpoint in (edge.get("source"), edge.get("target"))
            if isinstance(endpoint, str)
        )
        selected_ids.update(
            node["id"]
            for node in nodes
            if isinstance(node, dict)
            and isinstance(node.get("id"), str)
            and (
                str(node.get("kind", "")).startswith("extension")
                or (
                    node.get("kind") == "module"
                    and str(node.get("module", "")).startswith("extension:")
                )
            )
        )

    selected_nodes = [node for node in nodes if node.get("id") in selected_ids]
    selected_edges = [
        edge
        for edge in edges
        if edge.get("source") in selected_ids and edge.get("target") in selected_ids
    ]
    selected_nodes.sort(key=lambda node: node["id"])
    selected_edges.sort(
        key=lambda edge: (
            edge["source"],
            edge["target"],
            edge.get("relation", ""),
            edge.get("label", ""),
        )
    )
    selected_module_paths: set[str] = {
        label
        for node in selected_nodes
        if node.get("kind") == "module" and isinstance(label := node.get("label"), str)
    }
    selected_module_records: list[dict[str, Any]] = [
        module
        for module in mindmap.get("modules", [])
        if isinstance(module, dict) and module.get("path") in selected_module_paths
    ]
    selected_module_records.sort(key=lambda module: module["path"])
    kinds = Counter(node.get("kind") for node in selected_nodes)
    subset = {
        **mindmap,
        "files": [module["path"] for module in selected_module_records],
        "modules": selected_module_records,
        "nodes": selected_nodes,
        "edges": selected_edges,
        "summary": {
            "node_count": len(selected_nodes),
            "edge_count": len(selected_edges),
            "kinds": dict(sorted(kinds.items())),
            "workflow_count": sum(
                node.get("kind") == "workflow" for node in selected_nodes
            ),
        },
        "notice": (
            "Selected from the checked Kosika AST/module mindmap. "
            "Selection is structural and does not execute code."
        ),
    }
    return subset, tuple(sorted(selected_ids))


def _read_trace(path: str | Path) -> list[dict[str, Any]]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ExplainError(f"Unable to read trace: {exc}") from exc
    try:
        events = [strict_json(line) for line in lines]
    except (ValueError, TypeError) as exc:
        raise ExplainError(f"Trace contains invalid JSON: {exc}") from exc
    if any(not isinstance(event, dict) for event in events):
        raise ExplainError("Trace events must be JSON objects")
    return events


def _trace_steps(
    events: list[dict[str, Any]], prefix: str = ""
) -> list[dict[str, Any]]:
    def function_steps(
        items: list[dict[str, Any]], function_prefix: str
    ) -> list[dict[str, Any]]:
        nested_steps: list[dict[str, Any]] = []
        for function_offset in range(0, len(items), 2):
            call, returned = items[function_offset : function_offset + 2]
            name = str(call.get("function"))
            nested_steps.append(
                {
                    "step": function_prefix + name,
                    "kind": "function_call",
                    "target": name,
                    "result_event": returned.get("event"),
                }
            )
            arguments = call.get("expression_events")
            if isinstance(arguments, list):
                nested_steps.extend(
                    function_steps(
                        [item for item in arguments if isinstance(item, dict)],
                        function_prefix + name + "/argument/",
                    )
                )
            body = returned.get("events")
            if isinstance(body, list):
                nested_steps.extend(
                    function_steps(
                        [item for item in body if isinstance(item, dict)],
                        function_prefix + name + "/",
                    )
                )
        return nested_steps

    middle = events[1:-1]
    steps: list[dict[str, Any]] = []
    for offset in range(0, len(middle), 4):
        step, call, returned, _completed = middle[offset : offset + 4]
        kind_value = call.get("event")
        kind = kind_value if isinstance(kind_value, str) else "unknown"
        target_key = {
            "model_call": "model",
            "agent_call": "agent",
            "workflow_call": "workflow",
            "function_call": "function",
        }.get(kind, "tool")
        step_name = prefix + str(step.get("step"))
        steps.append(
            {
                "step": step_name,
                "kind": kind,
                "target": call.get(target_key),
                "result_event": returned.get("event"),
            }
        )
        expression_events = call.get("expression_events")
        if isinstance(expression_events, list):
            steps.extend(
                function_steps(
                    [item for item in expression_events if isinstance(item, dict)],
                    step_name + "/argument/",
                )
            )
        if kind == "function_call" and isinstance(returned.get("events"), list):
            steps.extend(
                function_steps(
                    [item for item in returned["events"] if isinstance(item, dict)],
                    step_name + "/",
                )
            )
        if kind == "workflow_call" and isinstance(returned.get("events"), list):
            nested = [item for item in returned["events"] if isinstance(item, dict)]
            steps.extend(_trace_steps(nested, step_name + "/"))
    return steps


def summarize_trace(
    path: str | Path, *, expected_program_hash: str | None = None
) -> dict[str, Any]:
    """Validate a trace offline and return a stable structural summary."""

    events = _read_trace(path)
    try:
        result = replay_events(events)
    except (ValueError, TypeError, KeyError) as exc:
        raise ExplainError(f"Trace rejected: {exc}") from exc
    start = events[0]
    counts = Counter(
        event.get("event") for event in events if isinstance(event.get("event"), str)
    )
    observed_hash = start.get("program_hash")
    hash_match: bool | None = None
    if expected_program_hash is not None and isinstance(observed_hash, str):
        hash_match = observed_hash == expected_program_hash
    return {
        "path": Path(path).as_posix(),
        "workflow": start.get("workflow"),
        "trace_version": start.get("trace_version", 1),
        "program_hash": observed_hash,
        "program_hash_match": hash_match,
        "event_count": len(events),
        "event_counts": dict(sorted(counts.items())),
        "steps": _trace_steps(events),
        "result": result,
        "mode": "validated_offline",
        "notice": (
            "Trace structure and recorded outcomes were validated offline; "
            "no workflow, tool, or model was executed."
        ),
    }


def build_explanation(
    program: Program,
    *,
    workflows: Iterable[str] = (),
    records: Iterable[str] = (),
    diagnostics: Iterable[str] = (),
    trace_path: str | Path | None = None,
    extension_manifests: Iterable[ExtensionManifest] = (),
) -> dict[str, Any]:
    """Build a deterministic explanation for selected code and optional trace data."""

    workflow_names = _validate_names(workflows, "workflow")
    record_names = _validate_names(records, "record")
    diagnostic_values = tuple(diagnostics)
    if any(not isinstance(item, str) for item in diagnostic_values):
        raise ExplainError("Diagnostics must be text")
    extensions = tuple(sorted(extension_manifests, key=lambda item: item.id))
    mindmap = build_mindmap(program, extension_manifests=extensions)
    selected, selected_ids = _subset_mindmap(mindmap, workflow_names, record_names)
    expected_hash = _program_hash(program)
    trace = (
        summarize_trace(trace_path, expected_program_hash=expected_hash)
        if trace_path is not None
        else None
    )
    return {
        "version": EXPLAIN_VERSION,
        "selection": {
            "workflows": list(workflow_names),
            "records": list(record_names),
            "extensions": [extension.id for extension in extensions],
            "node_ids": list(selected_ids),
        },
        "diagnostics": list(diagnostic_values),
        "mindmap": selected,
        "trace": trace,
        "notice": (
            "Explanation is derived from checked syntax and optional offline trace "
            "validation. It does not infer intent or grant execution permission."
        ),
    }


def to_explanation_json(explanation: dict[str, Any]) -> str:
    return to_json(explanation)


def to_explanation_mermaid(explanation: dict[str, Any]) -> str:
    mindmap = explanation.get("mindmap")
    if not isinstance(mindmap, dict):
        raise ExplainError("Explanation has no mindmap")
    return to_mermaid(mindmap)


def _plain_words(value: str) -> str:
    """Turn a CamelCase/snake_case identifier into plain lowercase words."""

    parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value).replace("_", " ")
    return " ".join(parts.split()).lower()


def _node_lookup(mindmap: dict[str, Any]) -> dict[str, dict[str, Any]]:
    nodes = mindmap.get("nodes")
    if not isinstance(nodes, list):
        raise ExplainError("Explanation has no mindmap")
    lookup: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if isinstance(node, dict) and isinstance(node.get("id"), str):
            lookup[node["id"]] = node
    return lookup


def _linked(
    mindmap: dict[str, Any], node_id: str, *, direction: str, relation: str
) -> list[dict[str, Any]]:
    edges = mindmap.get("edges")
    if not isinstance(edges, list):
        raise ExplainError("Explanation has no mindmap")
    lookup = _node_lookup(mindmap)
    found: list[dict[str, Any]] = []
    for edge in edges:
        if not isinstance(edge, dict) or edge.get("relation") != relation:
            continue
        other = edge.get("target") if direction == "out" else edge.get("source")
        here = edge.get("source") if direction == "out" else edge.get("target")
        if here != node_id or not isinstance(other, str):
            continue
        target = lookup.get(other)
        if target is not None:
            found.append(target)
    found.sort(key=lambda node: str(node.get("label", "")))
    return found


def to_explanation_outsider(explanation: dict[str, Any]) -> str:
    """Render a plain-language story of the same checked graph.

    Same nodes, same edges, no new relationships. Type names are hidden
    in favour of what goes in, what happens, and what comes out.
    Business purpose still comes from human diagnostics; nothing is
    inferred by a model.
    """

    mindmap = explanation.get("mindmap")
    if not isinstance(mindmap, dict):
        raise ExplainError("Explanation has no mindmap")
    nodes = mindmap.get("nodes")
    if not isinstance(nodes, list):
        raise ExplainError("Explanation has no mindmap")
    workflows = sorted(
        (
            node
            for node in nodes
            if isinstance(node, dict) and node.get("kind") == "workflow"
        ),
        key=lambda node: str(node.get("label", "")),
    )
    if not workflows:
        return (
            "This program declares shared building blocks but no runnable "
            "workflow yet. Add a workflow to describe what should happen, "
            "in order.\n"
        )
    lines = ["What this program does (plain language):", ""]
    for workflow in workflows:
        name = str(workflow.get("label", "workflow"))
        lines.append(f"- {name}: {_plain_words(name)}.")
        steps = _linked(
            mindmap, str(workflow.get("id")), direction="out", relation="step"
        )
        for position, step in enumerate(steps, 1):
            label = str(step.get("label", f"step {position}"))
            attributes = step.get("attributes", {})
            binding = "a result"
            if isinstance(attributes, dict) and attributes.get("binding"):
                binding = str(attributes.get("binding"))
            actions: list[dict[str, Any]] = []
            for relation in ("agent_call", "model_call", "tool_call"):
                actions.extend(
                    _linked(
                        mindmap, str(step.get("id")), direction="out", relation=relation
                    )
                )
            actions.extend(
                _linked(
                    mindmap,
                    str(step.get("id")),
                    direction="out",
                    relation="workflow_call",
                )
            )
            actions.extend(
                _linked(
                    mindmap,
                    str(step.get("id")),
                    direction="out",
                    relation="function_call",
                )
            )
            if actions:
                what = ", ".join(
                    f"{_plain_words(str(item.get('kind', 'step')))} "
                    f"{str(item.get('label', '')).split(':')[-1]}"
                    for item in actions
                )
                lines.append(
                    f"  Step {position} ({label}) uses {what} to make {binding}."
                )
            else:
                lines.append(f"  Step {position} ({label}) makes {binding}.")
        lines.append("")
    groups = sorted(
        (
            node
            for node in nodes
            if isinstance(node, dict) and node.get("kind") == "swarm"
        ),
        key=lambda node: str(node.get("label", "")),
    )
    if groups:
        lines.append("How the agents work together:")
        for group in groups:
            attributes = group.get("attributes", {})
            members: list[str] = []
            rounds: object = 1
            setup: object = None
            approvals = False
            if isinstance(attributes, dict):
                if isinstance(attributes.get("agents"), list):
                    members = sorted(str(item) for item in attributes["agents"])
                rounds = attributes.get("max_rounds", 1)
                setup = attributes.get("workflow")
                approvals = bool(attributes.get("approval_required"))
            lines.append(
                f"- {group.get('label')} brings together "
                f"{', '.join(members) or 'no agents'} to run {setup}; "
                f"at most {rounds} review round(s)."
            )
            if approvals:
                lines.append("  A person must approve this group before it starts.")
        lines.append("")
    agents = sorted(
        (
            node
            for node in nodes
            if isinstance(node, dict) and node.get("kind") == "agent"
        ),
        key=lambda node: str(node.get("label", "")),
    )
    if agents:
        lines.append("Who is allowed to do what:")
        for agent in agents:
            attributes = agent.get("attributes", {})
            allowed: list[str] = []
            denied: list[str] = []
            if isinstance(attributes, dict):
                if isinstance(attributes.get("can"), list):
                    allowed = sorted(str(item) for item in attributes["can"])
                if isinstance(attributes.get("cannot"), list):
                    denied = sorted(str(item) for item in attributes["cannot"])
            sentence = (
                f"- {agent.get('label')} may use {', '.join(allowed) or 'nothing yet'}"
            )
            if denied:
                sentence += f"; it must never use {', '.join(denied)}"
            lines.append(sentence + ".")
        lines.append("")
    diagnostics = explanation.get("diagnostics", [])
    if isinstance(diagnostics, list) and diagnostics:
        lines.append("Human notes about purpose:")
        lines.extend(f"- {item}" for item in diagnostics if isinstance(item, str))
        lines.append("")
    trace = explanation.get("trace")
    if isinstance(trace, dict):
        lines.append(
            f"A previous run made {trace.get('event_count', 0)} recorded events "
            f"across {len(trace.get('steps', []))} step(s); checked offline, "
            "nothing was re-run."
        )
        if trace.get("program_hash_match") is False:
            lines.append(
                "Warning: that recording came from a different program version."
            )
        lines.append("")
    lines.append(
        "This story comes from the checked program structure, not a model guess. "
        "Purpose comes from the human notes above."
    )
    return "\n".join(lines) + "\n"


def to_explanation_text(explanation: dict[str, Any]) -> str:
    mindmap = explanation.get("mindmap")
    if not isinstance(mindmap, dict):
        raise ExplainError("Explanation has no mindmap")
    selection = explanation.get("selection", {})
    workflows = selection.get("workflows", []) if isinstance(selection, dict) else []
    records = selection.get("records", []) if isinstance(selection, dict) else []
    summary = mindmap.get("summary", {})
    lines = [
        "Kosika explanation",
        "Focus: "
        + (
            ", ".join(
                [
                    *(f"workflow {name}" for name in workflows),
                    *(f"record {name}" for name in records),
                ]
            )
            or "entire program"
        ),
        f"Nodes: {summary.get('node_count', 0)}; edges: {summary.get('edge_count', 0)}",
    ]
    for module in mindmap.get("modules", []):
        if isinstance(module, dict):
            declarations = ", ".join(module.get("declarations", [])) or "none"
            lines.append(f"Module {module.get('path')}: {declarations}")
    for node in mindmap.get("nodes", []):
        if not isinstance(node, dict):
            continue
        kind = node.get("kind")
        attributes = node.get("attributes", {})
        if kind == "workflow":
            lines.append(
                f"Workflow {node['label']} returns {attributes.get('output')}; "
                f"approval required: {attributes.get('approval_required', False)}"
            )
        elif kind == "step":
            lines.append(f"  Step {node['label']} binds {attributes.get('binding')}")
        elif kind == "swarm":
            lines.append(
                f"Swarm {node['label']} declares {attributes.get('workflow')} "
                f"with {', '.join(sorted(attributes.get('agents', [])))}; "
                f"max_rounds={attributes.get('max_rounds')}; "
                f"approval required: {attributes.get('approval_required', False)}"
            )
            if not attributes.get("executable", False):
                lines.append(
                    "  Declared only: a swarm is not a workflow entry point. It runs "
                    "only through `kosika swarm` under one approval."
                )
        elif kind == "record":
            fields = attributes.get("fields", [])
            description = ", ".join(
                f"{field.get('name')}: {field.get('type')}"
                for field in fields
                if isinstance(field, dict)
            )
            lines.append(f"Record {node['label']}: {description or 'empty'}")
        elif kind == "function":
            parameters = attributes.get("parameters", [])
            signature = ", ".join(
                f"{field.get('name')}: {field.get('type')}"
                for field in parameters
                if isinstance(field, dict)
            )
            lines.append(
                f"Function {node['label']}({signature}) -> "
                f"{attributes.get('output')} (pure)"
            )
            for binding in attributes.get("bindings", []):
                lines.append(
                    f"  Local {binding['name']}: {binding['type']} (immutable)"
                )
        elif kind in {"model", "agent", "tool", "capability"}:
            details = ", ".join(
                f"{key}={value}"
                for key, value in sorted(attributes.items())
                if key in {"input", "output", "model", "access", "operation", "status"}
            )
            lines.append(
                f"{kind.title()} {node['label']}" + (f" ({details})" if details else "")
            )
    for extension in mindmap.get("extensions", []):
        if not isinstance(extension, dict):
            continue
        lines.append(
            f"Extension {extension.get('id')} {extension.get('version')} "
            f"digest={str(extension.get('digest', ''))[:12]}"
        )
        contributions = extension.get("contributions", [])
        if isinstance(contributions, list):
            lines.append(
                "  Contributions: "
                + ", ".join(
                    f"{item.get('kind')}:{item.get('name')}"
                    for item in contributions
                    if isinstance(item, dict)
                )
            )
        permissions = extension.get("permissions", [])
        if isinstance(permissions, list):
            lines.append(
                "  Permissions: "
                + (", ".join(str(item) for item in permissions) or "none")
            )
        dependencies = extension.get("dependencies", [])
        if isinstance(dependencies, list):
            lines.append(
                "  Dependencies: "
                + (
                    ", ".join(
                        f"{item.get('id')} ({item.get('constraint')})"
                        for item in dependencies
                        if isinstance(item, dict)
                    )
                    or "none"
                )
            )
        lifecycle = extension.get("lifecycle", {})
        if isinstance(lifecycle, dict):
            lines.append(
                "  Lifecycle: "
                + (
                    ", ".join(
                        f"{name}={entrypoint}"
                        for name, entrypoint in sorted(lifecycle.items())
                    )
                    or "none"
                )
            )
        health_checks = extension.get("healthChecks", [])
        if isinstance(health_checks, list):
            lines.append(
                "  Health checks: "
                + (
                    ", ".join(
                        f"{item.get('name')} <= {item.get('timeoutMs')}ms"
                        for item in health_checks
                        if isinstance(item, dict)
                    )
                    or "none"
                )
            )
        rollback = extension.get("rollback", {})
        if isinstance(rollback, dict):
            lines.append(
                "  Rollback: "
                + str(rollback.get("strategy", "unknown"))
                + (
                    f" via {rollback.get('entrypoint')}"
                    if rollback.get("entrypoint")
                    else ""
                )
            )
    diagnostics = explanation.get("diagnostics", [])
    if diagnostics:
        lines.append("Diagnostics:")
        lines.extend(f"- {diagnostic}" for diagnostic in diagnostics)
    trace = explanation.get("trace")
    if isinstance(trace, dict):
        lines.append(
            f"Trace {trace.get('path')}: {trace.get('event_count', 0)} events, "
            f"{len(trace.get('steps', []))} step(s), offline validation passed"
        )
        if trace.get("program_hash_match") is False:
            lines.append("Warning: trace program hash differs from the selected source")
    lines.append(
        "Static explanation only: no code, model, tool, or external resource was executed."
    )
    return "\n".join(lines) + "\n"
