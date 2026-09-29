from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
from .network import NetworkManifest


class DesignError(ValueError):
    """Raised when a Kosika design manifest is invalid."""


_ALLOWED_NODE_KINDS = {
    "agent",
    "approval",
    "device",
    "function",
    "memory",
    "model",
    "module",
    "network",
    "network_node",
    "note",
    "record",
    "space",
    "stage",
    "swarm",
    "tool",
    "workflow",
}
_ALLOWED_EDGE_KINDS = {
    "calls",
    "connects",
    "contains",
    "emits",
    "imports",
    "inputs",
    "invokes",
    "network_context",
    "outputs",
    "reads",
    "requires_approval",
    "uses",
    "writes",
}
_ALLOWED_VIEW_KINDS = {"canvas", "memory_graph", "network", "space", "workflow"}
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DesignError(f"{label} must be an object")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DesignError(f"{label} must be a non-empty string")
    return value


def _identifier(value: Any, label: str) -> str:
    identifier = _string(value, label)
    if not _ID_RE.fullmatch(identifier):
        raise DesignError(f"{label} has invalid identifier {identifier!r}")
    return identifier


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DesignError(f"{label} must be a number")
    return float(value)


def _positive(value: Any, label: str) -> float:
    number = _number(value, label)
    if number <= 0:
        raise DesignError(f"{label} must be greater than zero")
    return number


def _records(value: Any, label: str) -> tuple[dict[str, Any], ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise DesignError(f"{label} must be an array")
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        record = _object(item, f"{label}[{index}]")
        identifier = _identifier(record.get("id"), f"{label}[{index}].id")
        if identifier in seen:
            raise DesignError(f"{label} contains duplicate id {identifier!r}")
        seen.add(identifier)
        result.append(dict(record))
    return tuple(result)


def _position(value: Any, label: str) -> dict[str, float]:
    position = _object(value, label)
    return {
        "x": _number(position.get("x"), f"{label}.x"),
        "y": _number(position.get("y"), f"{label}.y"),
    }


def _size(value: Any, label: str) -> dict[str, float]:
    size = _object(value, label)
    return {
        "width": _positive(size.get("width"), f"{label}.width"),
        "height": _positive(size.get("height"), f"{label}.height"),
    }


@dataclass(frozen=True)
class DesignManifest:
    design: dict[str, Any]
    nodes: tuple[dict[str, Any], ...]
    edges: tuple[dict[str, Any], ...]
    views: tuple[dict[str, Any], ...]
    manifest_version: int = 1

    @property
    def design_id(self) -> str:
        return str(self.design["id"])

    @property
    def name(self) -> str:
        return str(self.design["name"])

    @property
    def digest(self) -> str:
        return _digest(design_to_dict(self))


def validate_design(data: dict[str, Any]) -> DesignManifest:
    version = data.get("designVersion", 1)
    if version != 1:
        raise DesignError(f"Unsupported designVersion {version!r}; expected 1")
    design = _object(data.get("design"), "design")
    design_id = _identifier(design.get("id"), "design.id")
    name = _string(design.get("name"), "design.name")
    canvas = _object(design.get("canvas", {}), "design.canvas")
    width = _positive(canvas.get("width", 1200), "design.canvas.width")
    height = _positive(canvas.get("height", 800), "design.canvas.height")
    normalized_canvas = dict(canvas)
    normalized_canvas.update({"width": width, "height": height})
    normalized_design = dict(design)
    normalized_design.update(
        {"id": design_id, "name": name, "canvas": normalized_canvas}
    )

    raw_nodes = _records(data.get("nodes", []), "nodes")
    nodes: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    for index, raw_node in enumerate(raw_nodes):
        kind = _string(raw_node.get("kind"), f"nodes[{index}].kind")
        if kind not in _ALLOWED_NODE_KINDS:
            raise DesignError(
                f"nodes[{index}].kind must be one of {sorted(_ALLOWED_NODE_KINDS)!r}"
            )
        node = dict(raw_node)
        node["label"] = _string(node.get("label", node["id"]), f"nodes[{index}].label")
        node["position"] = _position(
            node.get("position", {}), f"nodes[{index}].position"
        )
        node["size"] = _size(
            node.get("size", {"width": 160, "height": 64}),
            f"nodes[{index}].size",
        )
        style = node.get("style", {})
        node["style"] = _object(style, f"nodes[{index}].style")
        node_ids.add(node["id"])
        nodes.append(node)

    raw_edges = _records(data.get("edges", []), "edges")
    edges: list[dict[str, Any]] = []
    for index, raw_edge in enumerate(raw_edges):
        kind = _string(raw_edge.get("kind", "connects"), f"edges[{index}].kind")
        if kind not in _ALLOWED_EDGE_KINDS:
            raise DesignError(
                f"edges[{index}].kind must be one of {sorted(_ALLOWED_EDGE_KINDS)!r}"
            )
        source = _identifier(raw_edge.get("source"), f"edges[{index}].source")
        target = _identifier(raw_edge.get("target"), f"edges[{index}].target")
        if source not in node_ids or target not in node_ids:
            raise DesignError(
                f"edges[{index}] references unknown source or target: {source!r}, {target!r}"
            )
        edge = dict(raw_edge)
        edge["kind"] = kind
        edge["source"] = source
        edge["target"] = target
        edges.append(edge)

    raw_views = _records(data.get("views", []), "views")
    views: list[dict[str, Any]] = []
    view_ids: set[str] = set()
    for index, raw_view in enumerate(raw_views):
        kind = _string(raw_view.get("kind", "canvas"), f"views[{index}].kind")
        if kind not in _ALLOWED_VIEW_KINDS:
            raise DesignError(
                f"views[{index}].kind must be one of {sorted(_ALLOWED_VIEW_KINDS)!r}"
            )
        view = dict(raw_view)
        view["kind"] = kind
        root = view.get("root")
        if root is not None and root not in node_ids:
            raise DesignError(f"views[{index}] references unknown root node {root!r}")
        view_ids.add(view["id"])
        views.append(view)

    if design_id in node_ids | view_ids:
        raise DesignError("design.id must be unique across nodes and views")
    return DesignManifest(
        design=normalized_design,
        nodes=tuple(nodes),
        edges=tuple(edges),
        views=tuple(views),
        manifest_version=int(data.get("manifestVersion", 1)),
    )


def design_to_dict(manifest: DesignManifest) -> dict[str, Any]:
    return {
        "manifestVersion": manifest.manifest_version,
        "designVersion": 1,
        "design": manifest.design,
        "nodes": list(manifest.nodes),
        "edges": list(manifest.edges),
        "views": list(manifest.views),
    }


def load_design_manifest(path: str | Path) -> DesignManifest:
    design_path = Path(path)
    try:
        data = json.loads(design_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DesignError(
            f"Could not read design manifest {design_path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise DesignError("Design manifest must be a JSON object")
    return validate_design(data)


def write_design_manifest(manifest: DesignManifest, path: str | Path) -> None:
    design_path = Path(path)
    design_path.parent.mkdir(parents=True, exist_ok=True)
    design_path.write_text(
        json.dumps(design_to_dict(manifest), indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def _node(
    identifier: str,
    label: str,
    kind: str,
    x: float,
    y: float,
    *,
    width: float = 160,
    height: float = 64,
) -> dict[str, Any]:
    return {
        "id": identifier,
        "label": label,
        "kind": kind,
        "position": {"x": x, "y": y},
        "size": {"width": width, "height": height},
    }


def _edge(identifier: str, source: str, target: str, kind: str) -> dict[str, Any]:
    return {"id": identifier, "source": source, "target": target, "kind": kind}


def build_design_from_program(
    program: Program,
    design_id: str,
    name: str,
    *,
    network: NetworkManifest | None = None,
) -> DesignManifest:
    """Build a deterministic vector design from checked Kosika declarations."""
    check(program)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    module_names = tuple(
        sorted(
            set(program.module_hashes)
            | set(program.module_declarations)
            | set(program.module_imports)
        )
    )

    def module_node_id(module: str) -> str:
        return f"module.{module.replace('/', '::')}"

    module_ids = {module_node_id(module) for module in module_names}
    module_owners: dict[str, str] = {}
    for module, declarations in sorted(program.module_declarations.items()):
        for declaration in declarations:
            module_owners[declaration] = module
    for index, module in enumerate(module_names):
        nodes.append(
            _node(
                module_node_id(module),
                module,
                "module",
                1120,
                80 + index * 96,
                width=220,
            )
        )
    for module in module_names:
        for imported in sorted(program.module_imports.get(module, ())):
            if module_node_id(imported) in module_ids:
                edges.append(
                    _edge(
                        f"{module_node_id(module)}.imports.{imported}",
                        module_node_id(module),
                        module_node_id(imported),
                        "imports",
                    )
                )

    def attach_module(declaration: str, node_id: str) -> None:
        owner = module_owners.get(declaration)
        if owner is not None and module_node_id(owner) in module_ids:
            edges.append(
                _edge(
                    f"{module_node_id(owner)}.contains.{node_id}",
                    module_node_id(owner),
                    node_id,
                    "contains",
                )
            )

    for index, record in enumerate(sorted(program.records, key=lambda item: item.name)):
        nodes.append(
            _node(f"record.{record.name}", record.name, "record", 80, 80 + index * 96)
        )
        attach_module(record.name, f"record.{record.name}")
    for index, tool in enumerate(sorted(program.tools, key=lambda item: item.name)):
        nodes.append(
            _node(f"tool.{tool.name}", tool.name, "tool", 340, 80 + index * 96)
        )
        attach_module(tool.name, f"tool.{tool.name}")
        edges.append(
            _edge(
                f"tool.{tool.name}.outputs",
                f"tool.{tool.name}",
                f"record.{tool.output}",
                "outputs",
            )
        )
    for index, agent in enumerate(sorted(program.agents, key=lambda item: item.name)):
        nodes.append(
            _node(f"agent.{agent.name}", agent.name, "agent", 600, 80 + index * 96)
        )
        attach_module(agent.name, f"agent.{agent.name}")
        for tool_name in sorted(agent.can):
            if any(tool.name == tool_name for tool in program.tools):
                edges.append(
                    _edge(
                        f"agent.{agent.name}.uses.{tool_name}",
                        f"agent.{agent.name}",
                        f"tool.{tool_name}",
                        "uses",
                    )
                )
        if agent.model is not None and any(
            model.name == agent.model for model in program.models
        ):
            edges.append(
                _edge(
                    f"agent.{agent.name}.uses.{agent.model}",
                    f"agent.{agent.name}",
                    f"model.{agent.model}",
                    "uses",
                )
            )

    for index, swarm in enumerate(sorted(program.swarms, key=lambda item: item.name)):
        swarm_id = f"swarm.{swarm.name}"
        nodes.append(_node(swarm_id, swarm.name, "swarm", 600, 80 + index * 96))
        attach_module(swarm.name, swarm_id)
        for agent_name in sorted(swarm.agents):
            if any(agent.name == agent_name for agent in program.agents):
                edges.append(
                    _edge(
                        f"{swarm_id}.uses.{agent_name}",
                        swarm_id,
                        f"agent.{agent_name}",
                        "uses",
                    )
                )
        if any(workflow.name == swarm.workflow for workflow in program.workflows):
            edges.append(
                _edge(
                    f"{swarm_id}.invokes.{swarm.workflow}",
                    swarm_id,
                    f"workflow.{swarm.workflow}",
                    "invokes",
                )
            )
        if swarm.approval_required:
            swarm_approval_id = f"approval.swarm.{swarm.name}"
            nodes.append(
                _node(
                    swarm_approval_id,
                    "Approval",
                    "approval",
                    600,
                    80 + index * 96 + 90,
                )
            )
            edges.append(
                _edge(
                    f"{swarm_id}.approval",
                    swarm_id,
                    swarm_approval_id,
                    "requires_approval",
                )
            )

    for index, model in enumerate(sorted(program.models, key=lambda item: item.name)):
        nodes.append(
            _node(f"model.{model.name}", model.name, "model", 860, 80 + index * 96)
        )
        attach_module(model.name, f"model.{model.name}")
        if any(record.name == model.input for record in program.records):
            edges.append(
                _edge(
                    f"model.{model.name}.input",
                    f"record.{model.input}",
                    f"model.{model.name}",
                    "inputs",
                )
            )
        if any(record.name == model.output for record in program.records):
            edges.append(
                _edge(
                    f"model.{model.name}.output",
                    f"model.{model.name}",
                    f"record.{model.output}",
                    "outputs",
                )
            )

    for index, function in enumerate(
        sorted(program.functions, key=lambda item: item.name)
    ):
        function_id = f"function.{function.name.replace('/', '.')}"
        nodes.append(
            _node(function_id, function.name, "function", 1080, 80 + index * 96)
        )
        attach_module(function.name, function_id)
        if any(record.name == function.output_type for record in program.records):
            edges.append(
                _edge(
                    f"{function_id}.output",
                    function_id,
                    f"record.{function.output_type}",
                    "outputs",
                )
            )
        for call_index, call in enumerate(
            static_function_references(function)
        ):
            edges.append(
                _edge(
                    f"{function_id}.calls.{call_index}",
                    function_id,
                    f"function.{call.function_name.replace('/', '.')}",
                    "calls",
                )
            )

    for workflow_index, workflow in enumerate(
        sorted(program.workflows, key=lambda item: item.name)
    ):
        workflow_y = 480 + workflow_index * 180
        workflow_id = f"workflow.{workflow.name}"
        nodes.append(_node(workflow_id, workflow.name, "workflow", 80, workflow_y))
        attach_module(workflow.name, workflow_id)
        binding_stages: dict[str, str] = {}
        for step_index, step in enumerate(workflow.steps):
            stage_id = f"stage.{workflow.name}.{step.name}"
            stage_x = 340 + step_index * 210
            nodes.append(_node(stage_id, step.name, "stage", stage_x, workflow_y))
            edges.append(
                _edge(
                    f"{workflow_id}.contains.{step.name}",
                    workflow_id,
                    stage_id,
                    "contains",
                )
            )
            if isinstance(step.call, ToolCall):
                edges.append(
                    _edge(
                        f"{stage_id}.agent",
                        stage_id,
                        f"agent.{step.call.agent_name}",
                        "calls",
                    )
                )
                edges.append(
                    _edge(
                        f"{stage_id}.tool",
                        stage_id,
                        f"tool.{step.call.tool_name}",
                        "invokes",
                    )
                )
            elif isinstance(step.call, ModelCall):
                edges.append(
                    _edge(
                        f"{stage_id}.model",
                        stage_id,
                        f"model.{step.call.model_name}",
                        "calls",
                    )
                )
            elif isinstance(step.call, AgentCall):
                edges.append(
                    _edge(
                        f"{stage_id}.agent",
                        stage_id,
                        f"agent.{step.call.agent_name}",
                        "calls",
                    )
                )
            elif isinstance(step.call, WorkflowCall):
                edges.append(
                    _edge(
                        f"{stage_id}.workflow",
                        stage_id,
                        f"workflow.{step.call.workflow_name}",
                        "calls",
                    )
                )
            elif isinstance(step.call, FunctionCall):
                edges.append(
                    _edge(
                        f"{stage_id}.function",
                        stage_id,
                        binding_stages.get(step.call.function_name, f"function.{step.call.function_name}"),
                        "calls",
                    )
                )
            binding_stages[step.binding_name] = stage_id
        if workflow.approval_required:
            approval_id = f"approval.{workflow.name}"
            nodes.append(
                _node(approval_id, "Approval", "approval", 80, workflow_y + 90)
            )
            edges.append(
                _edge(
                    f"{workflow_id}.approval",
                    workflow_id,
                    approval_id,
                    "requires_approval",
                )
            )

    if network is not None:
        network_root = f"network.{network.network_id}"
        nodes.append(_node(network_root, network.name, "network", 980, 700, width=220))
        for index, network_node in enumerate(
            sorted(network.nodes, key=lambda item: str(item["id"]))
        ):
            node_id = f"network_node.{network_node['id']}"
            nodes.append(
                _node(
                    node_id,
                    str(network_node.get("name", network_node["id"])),
                    "network_node",
                    980,
                    80 + index * 96,
                )
            )
            edges.append(
                _edge(
                    f"{network_root}.contains.{network_node['id']}",
                    network_root,
                    node_id,
                    "contains",
                )
            )
        network_node_ids = {str(item["id"]) for item in network.nodes}
        for link in sorted(network.links, key=lambda item: str(item["id"])):
            if (
                link["source"] in network_node_ids
                and link["target"] in network_node_ids
            ):
                edges.append(
                    _edge(
                        f"network.{link['id']}",
                        f"network_node.{link['source']}",
                        f"network_node.{link['target']}",
                        "connects",
                    )
                )
        for workflow in program.workflows:
            edges.append(
                _edge(
                    f"workflow.{workflow.name}.network",
                    f"workflow.{workflow.name}",
                    network_root,
                    "network_context",
                )
            )

    data = {
        "manifestVersion": 1,
        "designVersion": 1,
        "design": {
            "id": design_id,
            "name": name,
            "kind": "agent_workflow_network",
            "canvas": {"width": 1400, "height": 1000, "unit": "px"},
        },
        "nodes": nodes,
        "edges": edges,
        "views": [
            {"id": "workflow-view", "name": "Agent workflows", "kind": "workflow"},
            {"id": "network-view", "name": "Network topology", "kind": "network"},
            {"id": "mindmap-view", "name": "Structure mindmap", "kind": "memory_graph"},
        ],
    }
    return validate_design(data)


def render_design_text(manifest: DesignManifest) -> str:
    lines = [
        f"Design: {manifest.name} ({manifest.design_id})",
        f"Digest: {manifest.digest}",
        f"Canvas: {manifest.design['canvas']['width']} x {manifest.design['canvas']['height']}",
        f"Nodes ({len(manifest.nodes)}):",
    ]
    for node in sorted(manifest.nodes, key=lambda item: str(item["id"])):
        position = node["position"]
        lines.append(
            f"  - {node['id']} [{node['kind']}] {node['label']} "
            f"@ ({position['x']}, {position['y']})"
        )
    lines.append(f"Edges ({len(manifest.edges)}):")
    for edge in sorted(manifest.edges, key=lambda item: str(item["id"])):
        lines.append(f"  - {edge['source']} -[{edge['kind']}]-> {edge['target']}")
    return "\n".join(lines)


def _mermaid_id(identifier: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", identifier)


def render_design_mermaid(manifest: DesignManifest) -> str:
    lines = ["flowchart LR"]
    for node in sorted(manifest.nodes, key=lambda item: str(item["id"])):
        node_id = _mermaid_id(str(node["id"]))
        label = str(node["label"]).replace('"', "'")
        kind = str(node["kind"])
        lines.append(f'  {node_id}["{label}\\n({kind})"]')
    for edge in sorted(manifest.edges, key=lambda item: str(item["id"])):
        source = _mermaid_id(str(edge["source"]))
        target = _mermaid_id(str(edge["target"]))
        lines.append(f"  {source} -->|{edge['kind']}| {target}")
    return "\n".join(lines)


# Presentation only. Node ids, node kinds, and typed edges stay authoritative
# (see design/DESIGN.md). A palette never changes a design digest, because the
# digest is computed over the manifest rather than over the rendered output.
_NODE_COLORS = {
    "agent": ("#ede9fe", "#6d28d9"),
    "approval": ("#fef3c7", "#b45309"),
    "device": ("#e0f2fe", "#0369a1"),
    "function": ("#ecfccb", "#4d7c0f"),
    "memory": ("#dcfce7", "#15803d"),
    "model": ("#dbeafe", "#1d4ed8"),
    "module": ("#fef9c3", "#a16207"),
    "network": ("#fce7f3", "#be185d"),
    "network_node": ("#f1f5f9", "#475569"),
    "record": ("#f1f5f9", "#334155"),
    "space": ("#cffafe", "#0e7490"),
    "stage": ("#ffedd5", "#c2410c"),
    "swarm": ("#fae8ff", "#86198f"),
    "tool": ("#e0e7ff", "#4338ca"),
    "workflow": ("#ccfbf1", "#0f766e"),
}

# The "lab" theme: near-black canvas, one hue per meaning.
#   cyan = structure · violet = inference · amber = authority
#   lime = verified · slate = inert
_LAB_NODE_COLORS = {
    "agent": ("#141029", "#A78BFA"),
    "approval": ("#191305", "#FBBF24"),
    "device": ("#08161F", "#38BDF8"),
    "function": ("#07170F", "#4ADE80"),
    "memory": ("#10122A", "#818CF8"),
    "model": ("#131029", "#A78BFA"),
    "module": ("#0A1720", "#22D3EE"),
    "network": ("#1A0B1C", "#F472B6"),
    "network_node": ("#0D131A", "#64748B"),
    "record": ("#0D131A", "#7C8FA3"),
    "space": ("#061A1D", "#2DD4BF"),
    "stage": ("#071A1E", "#22D3EE"),
    "swarm": ("#1A0B1C", "#F472B6"),
    "tool": ("#191305", "#FBBF24"),
    "workflow": ("#061A1D", "#22D3EE"),
}

_LAB_CHROME = {
    "background": ("#05080D", "#0A1017"),
    "glow": (("#22D3EE", "0.10"), ("#A78BFA", "0.09")),
    "title": "#DBE7F1",
    "muted": "#586A7D",
    "panel_fill": "#0C121A",
    "panel_opacity": "0.72",
    "panel_stroke": "#1B2836",
    "panel_alt_stroke": "#2A2340",
    "shadow": ("#000000", "0.5"),
    "arrow": "#4A627A",
    "edge": "#2C4356",
    "label_fill": "#0C121A",
    "label_text": "#8A9BAD",
    "legend_fill": "#0A0F16",
    "legend_stroke": "#1B2836",
    "legend_text": "#8A9BAD",
    "node_label": "#DBE7F1",
    "fallback": ("#0D131A", "#7C8FA3"),
}

_LIGHT_CHROME = {
    "background": ("#f8fbff", "#f5f3ff"),
    "glow": (("#dbeafe", "0.28"), ("#fce7f3", "0.24")),
    "title": "#0f172a",
    "muted": "#64748b",
    "panel_fill": "#ffffff",
    "panel_opacity": "0.58",
    "panel_stroke": "#dbeafe",
    "panel_alt_stroke": "#fbcfe8",
    "shadow": ("#334155", "0.14"),
    "arrow": "#64748b",
    "edge": "#94a3b8",
    "label_fill": "#ffffff",
    "label_text": "#64748b",
    "legend_fill": "#ffffff",
    "legend_stroke": "#e2e8f0",
    "legend_text": "#475569",
    "node_label": "#0f172a",
    "fallback": ("#f8fafc", "#475569"),
}

DESIGN_THEMES = ("lab", "light")


def _theme(name: str) -> tuple[dict[str, tuple[str, str]], dict[str, Any]]:
    if name == "lab":
        return _LAB_NODE_COLORS, _LAB_CHROME
    if name == "light":
        return _NODE_COLORS, _LIGHT_CHROME
    raise DesignError(f"Unsupported design theme {name!r}")


def _mindmap_positions(
    manifest: DesignManifest,
) -> dict[str, tuple[float, float, float, float]]:
    """Create a deterministic layered mindmap layout without changing the manifest."""
    if not manifest.nodes:
        return {}
    nodes = {str(node["id"]): node for node in manifest.nodes}
    adjacency: dict[str, set[str]] = {identifier: set() for identifier in nodes}
    for edge in manifest.edges:
        source = str(edge["source"])
        target = str(edge["target"])
        adjacency[source].add(target)
        adjacency[target].add(source)
    roots = sorted(
        nodes,
        key=lambda identifier: (
            0
            if nodes[identifier]["kind"] == "workflow"
            else 1
            if nodes[identifier]["kind"] == "network"
            else 2,
            identifier,
        ),
    )
    root = roots[0]
    levels: dict[str, int] = {root: 0}
    queue = [root]
    while queue:
        current = queue.pop(0)
        for neighbor in sorted(adjacency[current]):
            if neighbor not in levels:
                levels[neighbor] = levels[current] + 1
                queue.append(neighbor)
    for identifier in sorted(nodes):
        if identifier not in levels:
            levels[identifier] = max(levels.values(), default=0) + 1
    grouped: dict[int, list[str]] = {}
    for identifier, level in levels.items():
        grouped.setdefault(level, []).append(identifier)
    positions: dict[str, tuple[float, float, float, float]] = {}
    canvas = manifest.design["canvas"]
    width = float(canvas["width"])
    height = float(canvas["height"])
    max_level = max(grouped)
    level_gap = max(190.0, (width - 220.0) / max(1, max_level))
    for level, identifiers in sorted(grouped.items()):
        identifiers.sort()
        gap = height / (len(identifiers) + 1)
        x = min(80.0 + level * level_gap, width - 260.0)
        for index, identifier in enumerate(identifiers):
            node = nodes[identifier]
            node_width = float(node["size"]["width"])
            node_height = float(node["size"]["height"])
            positions[identifier] = (
                x,
                max(76.0, gap * (index + 1) - node_height / 2),
                node_width,
                node_height,
            )
    return positions


def _edge_curve(
    edge: dict[str, Any],
    positions: dict[str, tuple[float, float, float, float]],
) -> tuple[str, float, float]:
    source_x, source_y, source_w, source_h = positions[str(edge["source"])]
    target_x, target_y, target_w, target_h = positions[str(edge["target"])]
    source_center_x = source_x + source_w / 2
    source_center_y = source_y + source_h / 2
    target_center_x = target_x + target_w / 2
    target_center_y = target_y + target_h / 2
    if abs(target_center_x - source_center_x) < 1:
        offset = 42 + (sum(ord(char) for char in str(edge["id"])) % 38)
        x1 = source_x + source_w
        x2 = target_x + target_w
        control_x = max(x1, x2) + offset
        path = (
            f"M {x1:g} {source_center_y:g} "
            f"C {control_x:g} {source_center_y:g}, {control_x:g} {target_center_y:g}, "
            f"{x2:g} {target_center_y:g}"
        )
        return path, control_x, (source_center_y + target_center_y) / 2
    if target_center_x > source_center_x:
        x1 = source_x + source_w
        x2 = target_x
        bend = max(56.0, abs(x2 - x1) * 0.42)
        control_1_x = x1 + bend
        control_2_x = x2 - bend
    else:
        x1 = source_x
        x2 = target_x + target_w
        bend = max(56.0, abs(x1 - x2) * 0.42)
        control_1_x = x1 - bend
        control_2_x = x2 + bend
    path = (
        f"M {x1:g} {source_center_y:g} "
        f"C {control_1_x:g} {source_center_y:g}, {control_2_x:g} {target_center_y:g}, "
        f"{x2:g} {target_center_y:g}"
    )
    return path, (x1 + x2) / 2, (source_center_y + target_center_y) / 2 - 8


def _svg_legend(
    manifest: DesignManifest,
    node_colors: dict[str, tuple[str, str]],
    chrome: dict[str, Any],
) -> list[str]:
    kinds = sorted({str(node["kind"]) for node in manifest.nodes})
    lines = [
        '  <g transform="translate(1010 18)">',
        f'    <rect width="350" height="42" rx="14" fill="{chrome["legend_fill"]}" '
        f'fill-opacity="0.92" stroke="{chrome["legend_stroke"]}" />',
    ]
    for index, kind in enumerate(kinds[:6]):
        x = 12 + (index % 3) * 112
        y = 16 + (index // 3) * 18
        fill, stroke = node_colors.get(kind, chrome["fallback"])
        lines.append(
            f'    <rect x="{x}" y="{y - 9}" width="10" height="10" rx="3" fill="{fill}" stroke="{stroke}" />'
        )
        lines.append(
            f'    <text x="{x + 15}" y="{y}" font-family="sans-serif" font-size="10" '
            f'fill="{chrome["legend_text"]}">{html.escape(kind)}</text>'
        )
    lines.append("  </g>")
    return lines


def render_design_svg(
    manifest: DesignManifest, *, view: str = "workflow", theme: str = "lab"
) -> str:
    if view not in {"workflow", "mindmap", "network"}:
        raise DesignError(f"Unsupported design view {view!r}")
    node_colors, chrome = _theme(theme)
    canvas = manifest.design["canvas"]
    width = float(canvas["width"])
    height = float(canvas["height"])
    positions = (
        _mindmap_positions(manifest)
        if view == "mindmap"
        else {
            str(node["id"]): (
                float(node["position"]["x"]),
                float(node["position"]["y"]),
                float(node["size"]["width"]),
                float(node["size"]["height"]),
            )
            for node in manifest.nodes
        }
    )
    title = f"{manifest.name} · {view.title()}"
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:g}" height="{height:g}" '
        f'viewBox="0 0 {width:g} {height:g}" role="img" aria-label="{html.escape(title)}">',
        "  <defs>",
        '    <linearGradient id="background" x1="0" y1="0" x2="1" y2="1">',
        f'      <stop offset="0%" stop-color="{chrome["background"][0]}" />',
        f'      <stop offset="100%" stop-color="{chrome["background"][1]}" />',
        "    </linearGradient>",
        '    <filter id="shadow" x="-20%" y="-20%" width="140%" height="150%">',
        f'      <feDropShadow dx="0" dy="6" stdDeviation="7" flood-color="{chrome["shadow"][0]}" '
        f'flood-opacity="{chrome["shadow"][1]}" />',
        "    </filter>",
        '    <marker id="arrow" markerWidth="11" markerHeight="11" refX="9" refY="3.5" orient="auto" markerUnits="strokeWidth">',
        f'      <path d="M0,0 L0,7 L10,3.5 z" fill="{chrome["arrow"]}" />',
        "    </marker>",
        "  </defs>",
        '  <rect width="100%" height="100%" fill="url(#background)" />',
        f'  <circle cx="1280" cy="850" r="220" fill="{chrome["glow"][0][0]}" fill-opacity="{chrome["glow"][0][1]}" />',
        f'  <circle cx="140" cy="850" r="180" fill="{chrome["glow"][1][0]}" fill-opacity="{chrome["glow"][1][1]}" />',
        f'  <text x="32" y="34" font-family="sans-serif" font-size="23" font-weight="700" '
        f'fill="{chrome["title"]}">{html.escape(manifest.name)}</text>',
        f'  <text x="32" y="55" font-family="sans-serif" font-size="12" fill="{chrome["muted"]}">'
        f'{html.escape(view.title())} view · deterministic design graph</text>',
    ]
    if view == "workflow":
        lines.extend(
            [
                f'  <rect x="32" y="70" width="870" height="890" rx="26" fill="{chrome["panel_fill"]}" '
                f'fill-opacity="{chrome["panel_opacity"]}" stroke="{chrome["panel_stroke"]}" />',
                f'  <text x="58" y="96" font-family="sans-serif" font-size="11" font-weight="700" '
                f'letter-spacing="1.5" fill="{chrome["muted"]}">AGENT WORKFLOW</text>',
                f'  <rect x="930" y="70" width="430" height="890" rx="26" fill="{chrome["panel_fill"]}" '
                f'fill-opacity="{chrome["panel_opacity"]}" stroke="{chrome["panel_alt_stroke"]}" />',
                f'  <text x="956" y="96" font-family="sans-serif" font-size="11" font-weight="700" '
                f'letter-spacing="1.5" fill="{chrome["muted"]}">NETWORK CONTEXT</text>',
            ]
        )
    elif view == "mindmap":
        lines.append(
            f'  <text x="32" y="86" font-family="sans-serif" font-size="11" font-weight="700" '
            f'letter-spacing="1.5" fill="{chrome["muted"]}">STRUCTURE MINDMAP</text>'
        )
    else:
        lines.append(
            f'  <text x="32" y="86" font-family="sans-serif" font-size="11" font-weight="700" '
            f'letter-spacing="1.5" fill="{chrome["muted"]}">NETWORK TOPOLOGY</text>'
        )
    lines.extend(_svg_legend(manifest, node_colors, chrome))
    for edge in sorted(manifest.edges, key=lambda item: str(item["id"])):
        path, label_x, label_y = _edge_curve(edge, positions)
        lines.append(
            f'  <path d="{path}" fill="none" stroke="{chrome["edge"]}" stroke-width="2" stroke-linecap="round" '
            f'stroke-dasharray="{"7 5" if edge["kind"] in {"network_context", "connects"} else "none"}" '
            f'marker-end="url(#arrow)" data-edge-kind="{html.escape(str(edge["kind"]))}" />'
        )
        lines.append(
            f'  <g transform="translate({label_x:g} {label_y:g})"><rect x="-36" y="-10" width="72" height="18" rx="9" '
            f'fill="{chrome["label_fill"]}" fill-opacity="0.9" />'
            f'<text text-anchor="middle" y="3" font-family="sans-serif" font-size="10" '
            f'fill="{chrome["label_text"]}">{html.escape(str(edge["kind"]))}</text></g>'
        )
    for node in sorted(manifest.nodes, key=lambda item: str(item["id"])):
        x, y, node_width, node_height = positions[str(node["id"])]
        fill, stroke = node_colors.get(str(node["kind"]), chrome["fallback"])
        node_id = html.escape(str(node["id"]))
        label = html.escape(str(node["label"]))
        kind = html.escape(str(node["kind"]).replace("_", " ").title())
        lines.append(
            f'  <g id="{node_id}" filter="url(#shadow)" data-node-kind="{kind}">'
            f'<rect x="{x:g}" y="{y:g}" width="{node_width:g}" height="{node_height:g}" rx="16" fill="{fill}" stroke="{stroke}" stroke-width="2" />'
        )
        lines.append(
            f'    <text x="{x + 16:g}" y="{y + 23:g}" font-family="sans-serif" font-size="10" font-weight="700" letter-spacing="1" fill="{stroke}">{kind.upper()}</text>'
        )
        lines.append(
            f'    <text x="{x + 16:g}" y="{y + node_height / 2 + 13:g}" font-family="sans-serif" '
            f'font-size="15" font-weight="600" fill="{chrome["node_label"]}">{label}</text></g>'
        )
    lines.append("</svg>")
    return "\n".join(lines)


def render_design(
    manifest: DesignManifest,
    output_format: str = "text",
    view: str = "workflow",
    theme: str = "lab",
) -> str:
    if output_format == "text":
        return render_design_text(manifest)
    if output_format == "json":
        return json.dumps(design_to_dict(manifest), indent=2, sort_keys=True)
    if output_format == "mermaid":
        return render_design_mermaid(manifest)
    if output_format == "svg":
        return render_design_svg(manifest, view=view, theme=theme)
    raise DesignError(f"Unsupported design output format {output_format!r}")
