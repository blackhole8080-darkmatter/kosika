"""Deterministic code mindmaps derived from the checked Kosika AST.

This module deliberately does not ask a model to infer program structure.  The
checker remains authoritative; the mindmap is a read-only explanation of the
same declarations, contracts, permissions, and call graph that Kosika executes.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

from .ast import (
    AgentCall,
    FunctionCall,
    ModelCall,
    Program,
    ToolCall,
    WorkflowCall,
    static_function_references,
)
from .checker import check
from .extensions import (
    ExtensionManifest,
    extension_manifest_digest,
    extension_manifest_to_dict,
)
from .function_types import base_type

MINDMAP_VERSION = 2
MAX_NODES = 512


@dataclass(frozen=True)
class MindmapNode:
    id: str
    kind: str
    label: str
    module: str
    attributes: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "module": self.module,
            "attributes": self.attributes,
        }


@dataclass(frozen=True)
class MindmapEdge:
    source: str
    target: str
    relation: str
    label: str

    def to_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "target": self.target,
            "relation": self.relation,
            "label": self.label,
        }


class MindmapError(ValueError):
    """Raised when a checked program cannot be represented safely."""


def _module_names(program: Program) -> tuple[str, ...]:
    if program.module_hashes:
        return tuple(sorted(program.module_hashes))
    return ("<memory>",)


def _owner_map(program: Program, modules: tuple[str, ...]) -> dict[str, str]:
    owners: dict[str, str] = {}
    for module, declarations in sorted(program.module_declarations.items()):
        for declaration in declarations:
            owners[declaration] = module
    fallback = modules[0]
    all_declarations = (
        [item.name for item in program.records]
        + [item.name for item in program.tools]
        + [item.name for item in program.agents]
        + [item.name for item in program.swarms]
        + [item.name for item in program.models]
        + [item.name for item in program.functions]
        + [item.name for item in program.workflows]
    )
    for declaration in all_declarations:
        owners.setdefault(declaration, fallback)
    return owners


def _digest(value: Any) -> str:
    """Deterministic digest for mindmap structure (nodes + edges)."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _base_type(type_name: str) -> str:
    return base_type(type_name)


def _node_id(kind: str, key: str) -> str:
    return f"{kind}:{key}"


def _sorted_edges(edges: Iterable[MindmapEdge]) -> list[MindmapEdge]:
    return sorted(
        edges,
        key=lambda edge: (edge.source, edge.target, edge.relation, edge.label),
    )


def build_mindmap(
    program: Program,
    *,
    extension_manifests: Iterable[ExtensionManifest] = (),
) -> dict[str, Any]:
    """Build a stable JSON-compatible mindmap from checked code and extensions.

    The output is deterministic for an equivalent AST and supplied manifest set.
    It contains module boundaries, type contracts, permissions, workflow call
    relationships, and extension lifecycle metadata. It never executes code,
    imports an extension, invokes a provider, or reads source files.
    """

    check(program)
    extensions = tuple(sorted(extension_manifests, key=lambda item: item.id))
    extension_ids = [manifest.id for manifest in extensions]
    if len(set(extension_ids)) != len(extension_ids):
        raise MindmapError("Duplicate extension manifest id")
    modules = _module_names(program)
    owners = _owner_map(program, modules)
    record_names = {record.name for record in program.records}
    nodes: dict[str, MindmapNode] = {}
    edges: list[MindmapEdge] = []

    def add_node(
        kind: str,
        key: str,
        label: str,
        module: str,
        **attributes: Any,
    ) -> str:
        identifier = _node_id(kind, key)
        if identifier not in nodes and len(nodes) >= MAX_NODES:
            raise MindmapError(
                f"Mindmap exceeds {MAX_NODES} nodes; split the program into modules"
            )
        existing = nodes.get(identifier)
        candidate = MindmapNode(identifier, kind, label, module, attributes)
        if existing is not None and existing != candidate:
            raise MindmapError(f"Conflicting mindmap node {identifier!r}")
        nodes[identifier] = candidate
        return identifier

    def connect(source: str, target: str, relation: str, label: str) -> None:
        edge = MindmapEdge(source, target, relation, label)
        if edge not in edges:
            edges.append(edge)

    for module in modules:
        attributes: dict[str, Any] = {}
        if module in program.module_hashes:
            attributes["sha256"] = program.module_hashes[module]
        add_node("module", module, module, module, **attributes)

    for module, imports in sorted(program.module_imports.items()):
        for imported in sorted(imports):
            if imported in program.module_hashes:
                connect(
                    _node_id("module", module),
                    _node_id("module", imported),
                    "imports",
                    "imports",
                )

    def module_for(name: str) -> str:
        return owners.get(name, modules[0])

    for record in sorted(program.records, key=lambda item: item.name):
        record_id = add_node(
            "record",
            record.name,
            record.name,
            module_for(record.name),
            fields=[
                {"name": field.name, "type": field.type_name} for field in record.fields
            ],
        )
        connect(
            _node_id("module", module_for(record.name)),
            record_id,
            "contains",
            "declares",
        )
        for field in record.fields:
            field_id = add_node(
                "field",
                f"{record.name}.{field.name}",
                field.name,
                module_for(record.name),
                record=record.name,
                type=field.type_name,
            )
            connect(record_id, field_id, "has_field", field.type_name)
            base_type = _base_type(field.type_name)
            if base_type in record_names:
                connect(
                    field_id, _node_id("record", base_type), "field_type", "typed as"
                )

    for tool in sorted(program.tools, key=lambda item: item.name):
        tool_id = add_node(
            "tool",
            tool.name,
            tool.name,
            module_for(tool.name),
            source=tool.source,
            operation=tool.operation,
            access=tool.access,
            input=tool.input,
            output=tool.output,
        )
        connect(
            _node_id("module", module_for(tool.name)),
            tool_id,
            "contains",
            "declares",
        )
        if tool.input:
            target = _node_id("record", tool.input)
            connect(tool_id, target, "input", "accepts")
        target = _node_id("record", tool.output)
        connect(tool_id, target, "output", "returns")

    for model in sorted(program.models, key=lambda item: item.name):
        model_id = add_node(
            "model",
            model.name,
            model.name,
            module_for(model.name),
            input=model.input,
            output=model.output,
        )
        connect(
            _node_id("module", module_for(model.name)),
            model_id,
            "contains",
            "declares",
        )
        for relation, record_name, label in (
            ("input", model.input, "accepts"),
            ("output", model.output, "returns"),
        ):
            target = _node_id("record", record_name)
            connect(model_id, target, relation, label)

    for agent in sorted(program.agents, key=lambda item: item.name):
        agent_id = add_node(
            "agent",
            agent.name,
            agent.name,
            module_for(agent.name),
            model=agent.model,
            max_steps=agent.max_steps,
            can=list(agent.can),
            cannot=list(agent.cannot),
        )
        connect(
            _node_id("module", module_for(agent.name)),
            agent_id,
            "contains",
            "declares",
        )
        if agent.model:
            target = _node_id("model", agent.model)
            connect(agent_id, target, "model", "uses model")
        for tool_name in agent.can:
            target = _node_id("tool", tool_name)
            connect(agent_id, target, "can", "can use")
        for tool_name in agent.cannot:
            target = _node_id("tool", tool_name)
            if target not in nodes:
                target = add_node(
                    "capability",
                    f"{agent.name}:{tool_name}",
                    tool_name,
                    module_for(agent.name),
                    status="forbidden",
                    declared=False,
                )
            connect(agent_id, target, "cannot", "cannot use")

    record_names = {record.name for record in program.records}
    for swarm in sorted(program.swarms, key=lambda item: item.name):
        swarm_id = add_node(
            "swarm",
            swarm.name,
            swarm.name,
            module_for(swarm.name),
            agents=list(swarm.agents),
            workflow=swarm.workflow,
            max_rounds=swarm.max_rounds,
            approval_required=swarm.approval_required,
            executable=False,
        )
        connect(
            _node_id("module", module_for(swarm.name)),
            swarm_id,
            "contains",
            "declares",
        )
        for agent_name in swarm.agents:
            connect(
                swarm_id, _node_id("agent", agent_name), "coordinates", "works with"
            )
        connect(
            swarm_id,
            _node_id("workflow", swarm.workflow),
            "would_run",
            "would run together",
        )

    for function in sorted(program.functions, key=lambda item: item.name):
        function_id = add_node(
            "function",
            function.name,
            function.name,
            module_for(function.name),
            output=function.output_type,
            pure=True,
            recursion_limit=function.recursion_limit,
            bindings=[
                {"name": item.name, "type": item.type_name}
                for item in function.bindings
            ],
            parameters=[
                {"name": field.name, "type": field.type_name}
                for field in function.parameters
            ],
        )
        connect(
            _node_id("module", module_for(function.name)),
            function_id,
            "contains",
            "declares",
        )
        if _base_type(function.output_type) in record_names:
            connect(
                function_id,
                _node_id("record", _base_type(function.output_type)),
                "output",
                "returns",
            )
        for parameter in function.parameters:
            if _base_type(parameter.type_name) in record_names:
                connect(
                    function_id,
                    _node_id("record", _base_type(parameter.type_name)),
                    "parameter",
                    parameter.name,
                )
        for function_call in static_function_references(function):
            connect(
                function_id,
                _node_id("function", function_call.function_name),
                "function_call",
                "calls",
            )

    for workflow in sorted(program.workflows, key=lambda item: item.name):
        workflow_id = add_node(
            "workflow",
            workflow.name,
            workflow.name,
            module_for(workflow.name),
            output=workflow.output_type,
            approval_required=workflow.approval_required,
            **(
                {"budget": [list(item) for item in workflow.budget]}
                if workflow.budget
                else {}
            ),
            parameters=[
                {"name": field.name, "type": field.type_name}
                for field in workflow.parameters
            ],
        )
        connect(
            _node_id("module", module_for(workflow.name)),
            workflow_id,
            "contains",
            "declares",
        )
        output_target = _node_id("record", workflow.output_type)
        connect(workflow_id, output_target, "output", "returns")
        for parameter in workflow.parameters:
            parameter_target = _node_id("record", parameter.type_name)
            connect(workflow_id, parameter_target, "parameter", parameter.name)
        binding_steps: dict[str, str] = {}
        for index, step in enumerate(workflow.steps, 1):
            step_key = f"{workflow.name}:{step.name}"
            step_id = add_node(
                "step",
                step_key,
                f"{index}. {step.name}",
                module_for(workflow.name),
                workflow=workflow.name,
                binding=step.binding_name,
                binding_type=step.binding_type,
                return_name=step.return_name,
            )
            connect(workflow_id, step_id, "step", "runs")
            if step.binding_type in record_names:
                binding_target = _node_id("record", step.binding_type)
                connect(step_id, binding_target, "binding_type", "binds")
            # The fallback keeps the relation of an ordinary call, so every
            # view draws it, and is told apart by its "otherwise" label.
            for call, prefix in ((step.call, ""), (step.fallback, "otherwise: ")):
                if call is None:
                    continue
                if isinstance(call, ToolCall):
                    agent_target = _node_id("agent", call.agent_name)
                    tool_target = _node_id("tool", call.tool_name)
                    connect(
                        step_id, agent_target, "agent_call", prefix + "through agent"
                    )
                    connect(step_id, tool_target, "tool_call", prefix + "executes")
                elif isinstance(call, ModelCall):
                    target = _node_id("model", call.model_name)
                    connect(step_id, target, "model_call", prefix + "asks")
                elif isinstance(call, AgentCall):
                    target = _node_id("agent", call.agent_name)
                    connect(step_id, target, "agent_call", prefix + "asks")
                elif isinstance(call, WorkflowCall):
                    target = _node_id("workflow", call.workflow_name)
                    connect(step_id, target, "workflow_call", prefix + "calls")
                elif isinstance(call, FunctionCall):
                    target = binding_steps.get(
                        call.function_name, _node_id("function", call.function_name)
                    )
                    connect(step_id, target, "function_call", prefix + "computes")
                else:
                    raise MindmapError("Unsupported workflow call in mindmap")
            binding_steps[step.binding_name] = step_id

    extension_payloads: list[dict[str, Any]] = []
    for extension in extensions:
        extension_module = f"extension:{extension.id}"
        extension_id = add_node(
            "extension",
            extension.id,
            extension.id,
            extension_module,
            version=extension.version,
            author=extension.author,
            license=extension.license,
            digest=extension_manifest_digest(extension),
            compatibility={
                "minVersion": extension.compatibility.min_version,
                "maxVersion": extension.compatibility.max_version,
            },
        )
        extension_module_id = add_node(
            "module",
            extension_module,
            extension.id,
            extension_module,
            extension=extension.id,
            module_kind="extension_manifest",
        )
        connect(extension_module_id, extension_id, "contains", "declares")

        for permission in sorted(extension.permissions):
            permission_id = add_node(
                "extension_permission",
                f"{extension.id}:{permission}",
                permission,
                extension_module,
                extension=extension.id,
                declared=True,
            )
            connect(extension_id, permission_id, "permission", "declares")

        for contribution in sorted(
            extension.contributions, key=lambda item: (item.kind, item.name)
        ):
            contribution_id = add_node(
                "extension_contribution",
                f"{extension.id}:{contribution.kind}:{contribution.name}",
                contribution.name,
                extension_module,
                extension=extension.id,
                contribution_kind=contribution.kind,
                entrypoint=contribution.entrypoint,
                permissions=list(contribution.permissions),
            )
            connect(extension_id, contribution_id, "contribution", contribution.kind)
            for permission in sorted(contribution.permissions):
                permission_id = _node_id(
                    "extension_permission", f"{extension.id}:{permission}"
                )
                connect(contribution_id, permission_id, "requires", "requires")

        for dependency in sorted(extension.dependencies, key=lambda item: item.id):
            dependency_id = add_node(
                "extension_dependency",
                f"{extension.id}:{dependency.id}",
                dependency.id,
                extension_module,
                extension=extension.id,
                constraint=dependency.constraint,
                optional=dependency.optional,
            )
            connect(
                extension_id,
                dependency_id,
                "dependency",
                "optionally depends on" if dependency.optional else "depends on",
            )
            if dependency.id in extension_ids:
                connect(
                    dependency_id,
                    _node_id("extension", dependency.id),
                    "resolves_to",
                    "resolves to",
                )

        for hook_name, entrypoint in extension.lifecycle:
            hook_id = add_node(
                "extension_lifecycle",
                f"{extension.id}:{hook_name}",
                hook_name,
                extension_module,
                extension=extension.id,
                entrypoint=entrypoint,
            )
            connect(extension_id, hook_id, "lifecycle", "lifecycle hook")

        for health_check in extension.health_checks:
            health_id = add_node(
                "extension_health",
                f"{extension.id}:{health_check.name}",
                health_check.name,
                extension_module,
                extension=extension.id,
                entrypoint=health_check.entrypoint,
                timeout_ms=health_check.timeout_ms,
                required=health_check.required,
            )
            connect(extension_id, health_id, "health_check", "checks health")

        rollback_id = add_node(
            "extension_rollback",
            extension.id,
            extension.rollback.strategy,
            extension_module,
            extension=extension.id,
            strategy=extension.rollback.strategy,
            entrypoint=extension.rollback.entrypoint,
        )
        connect(extension_id, rollback_id, "rollback", "rolls back with")

        payload = extension_manifest_to_dict(extension)
        payload["digest"] = extension_manifest_digest(extension)
        extension_payloads.append(payload)

    for edge in edges:
        if edge.source not in nodes or edge.target not in nodes:
            raise MindmapError(
                f"Mindmap edge references an unknown node: {edge.source!r} -> {edge.target!r}"
            )

    module_ids = {_node_id("module", module) for module in modules}
    module_ids.update(
        _node_id("module", f"extension:{extension.id}") for extension in extensions
    )
    for node in nodes.values():
        if node.kind != "module" and _node_id("module", node.module) not in module_ids:
            raise MindmapError(f"Node {node.id!r} has no module boundary")

    ordered_nodes = [nodes[key].to_dict() for key in sorted(nodes)]
    ordered_edges = [edge.to_dict() for edge in _sorted_edges(edges)]
    kinds = Counter(node["kind"] for node in ordered_nodes)
    return {
        "version": MINDMAP_VERSION,
        "files": list(modules),
        "extensions": extension_payloads,
        "modules": [
            {
                "path": module,
                "sha256": program.module_hashes.get(module),
                "imports": list(sorted(program.module_imports.get(module, ()))),
                "declarations": list(
                    sorted(program.module_declarations.get(module, ()))
                ),
            }
            for module in modules
        ],
        "nodes": ordered_nodes,
        "edges": ordered_edges,
        "summary": {
            "node_count": len(ordered_nodes),
            "edge_count": len(ordered_edges),
            "kinds": dict(sorted(kinds.items())),
            "workflow_count": len(program.workflows),
            "function_count": len(program.functions),
            "swarm_count": len(program.swarms),
            "extension_count": len(extensions),
        },
        "digest": _digest(ordered_nodes + ordered_edges),
        "notice": (
            "Derived from the checked Kosika AST and imported module graph. "
            "This is a static explanation, not an execution trace or a model guess."
        ),
    }


def _mermaid_id(node_id: str) -> str:
    digest = hashlib.sha1(node_id.encode("utf-8")).hexdigest()[:12]
    return f"n_{digest}"


def _mermaid_text(value: str) -> str:
    return re.sub(r"[\r\n]+", " ", value).replace('"', "&quot;")


def to_mermaid(mindmap: dict[str, Any]) -> str:
    """Render a mindmap JSON object as deterministic Mermaid flowchart text."""

    nodes = mindmap.get("nodes")
    edges = mindmap.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise MindmapError("Mindmap must contain nodes and edges lists")
    lines = ["flowchart TD"]
    for node in nodes:
        if not isinstance(node, dict) or not all(
            isinstance(node.get(key), str) for key in ("id", "kind", "label", "module")
        ):
            raise MindmapError("Mindmap contains an invalid node")
        title = f"{node['kind']}: {node['label']}\\n[{node['module']}]"
        lines.append(f'    {_mermaid_id(node["id"])}["{_mermaid_text(title)}"]')
    for edge in edges:
        if not isinstance(edge, dict) or not all(
            isinstance(edge.get(key), str) for key in ("source", "target", "label")
        ):
            raise MindmapError("Mindmap contains an invalid edge")
        lines.append(
            f"    {_mermaid_id(edge['source'])} -->|{_mermaid_text(edge['label'])}| "
            f"{_mermaid_id(edge['target'])}"
        )
    return "\n".join(lines) + "\n"


def to_json(mindmap: dict[str, Any]) -> str:
    """Serialize a mindmap in stable, human-readable JSON form."""

    return json.dumps(mindmap, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def to_text(mindmap: dict[str, Any]) -> str:
    """Render a compact text summary suitable for a terminal or assistant context."""

    summary = mindmap.get("summary", {})
    lines = [
        "Kosika code mindmap",
        f"Modules: {len(mindmap.get('modules', []))}",
        f"Nodes: {summary.get('node_count', 0)}; edges: {summary.get('edge_count', 0)}",
    ]
    for module in mindmap.get("modules", []):
        if isinstance(module, dict):
            declarations = ", ".join(module.get("declarations", [])) or "none"
            lines.append(f"- {module.get('path', '<unknown>')}: {declarations}")
    for node in mindmap.get("nodes", []):
        if isinstance(node, dict) and node.get("kind") in {
            "workflow",
            "model",
            "agent",
            "swarm",
            "tool",
        }:
            lines.append(f"  {node['kind']} {node['label']} [{node['module']}]")
    for extension in mindmap.get("extensions", []):
        if not isinstance(extension, dict):
            continue
        lines.append(
            f"Extension {extension.get('id')} {extension.get('version')} "
            f"[{extension.get('digest', '')[:12]}]"
        )
        contributions = extension.get("contributions", [])
        if isinstance(contributions, list):
            for contribution in contributions:
                if isinstance(contribution, dict):
                    lines.append(
                        f"  contributes {contribution.get('kind')} "
                        f"{contribution.get('name')}"
                    )
        permissions = extension.get("permissions", [])
        if isinstance(permissions, list):
            lines.append(
                f"  permissions: {', '.join(str(item) for item in permissions) or 'none'}"
            )
        dependencies = extension.get("dependencies", [])
        if isinstance(dependencies, list):
            for dependency in dependencies:
                if isinstance(dependency, dict):
                    qualifier = "optional " if dependency.get("optional") else ""
                    lines.append(
                        f"  {qualifier}dependency {dependency.get('id')} "
                        f"({dependency.get('constraint')})"
                    )
        lifecycle = extension.get("lifecycle", {})
        if isinstance(lifecycle, dict):
            lines.append(
                "  lifecycle: "
                + ", ".join(
                    f"{name}={entrypoint}"
                    for name, entrypoint in sorted(lifecycle.items())
                )
                if lifecycle
                else "  lifecycle: none"
            )
        health_checks = extension.get("healthChecks", [])
        if isinstance(health_checks, list):
            for health_check in health_checks:
                if isinstance(health_check, dict):
                    lines.append(
                        f"  health check {health_check.get('name')} "
                        f"timeout={health_check.get('timeoutMs')}ms"
                    )
        rollback = extension.get("rollback", {})
        if isinstance(rollback, dict):
            lines.append(
                f"  rollback: {rollback.get('strategy')}"
                + (
                    f" ({rollback.get('entrypoint')})"
                    if rollback.get("entrypoint")
                    else ""
                )
            )
    return "\n".join(lines) + "\n"
