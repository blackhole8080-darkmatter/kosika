from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ast import (
    Program,
)
from .mindmap import build_mindmap
from .network import NetworkManifest


class DesignError(ValueError):
    """Raised when a Kosika design manifest is invalid."""


# node kinds that receive spatial layout positions in the design view
_LAYOUT_KINDS = {
    "module",
    "record",
    "tool",
    "agent",
    "swarm",
    "model",
    "function",
    "workflow",
    "stage",
    "step",
    "approval",
}

# network node kinds that share the network column layout
_NETWORK_KINDS = {
    "network",
    "network_node",
}


def _node_by_id(nodes: list[dict[str, Any]], node_id: str) -> dict[str, Any] | None:
    """Return the first mindmap node whose ``id`` matches, or None."""
    for node in nodes:
        if node.get("id") == node_id:
            return node
    return None


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
# Node ids are ``kind:key``. A key may be a lexical-lowered name, which carries
# '/' by design (see kosika.lexical), or the "<memory>" sentinel that marks a
# program with no backing file, so both are part of the id grammar.
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/<>-]*$")


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


def _layout_node_position(
    kind: str,
    label: str,
    sorted_groups: dict[str, list[str]],
    workflow_stage_positions: dict[str, tuple[float, float]],
    node_id: str,
) -> tuple[float, float, float]:
    """Return (x, y, width) for a design node derived from a mindmap node kind and label."""
    if kind == "module":
        index = sorted_groups["module"].index(label)
        return (1120.0, 80.0 + index * 96.0, 220.0)
    if kind == "record":
        index = sorted_groups["record"].index(label)
        return (80.0, 80.0 + index * 96.0, 160.0)
    if kind == "tool":
        index = sorted_groups["tool"].index(label)
        return (340.0, 80.0 + index * 96.0, 160.0)
    if kind == "agent":
        index = sorted_groups["agent"].index(label)
        return (600.0, 80.0 + index * 96.0, 160.0)
    if kind == "swarm":
        index = sorted_groups["swarm"].index(label)
        return (600.0, 80.0 + index * 96.0, 160.0)
    if kind == "model":
        index = sorted_groups["model"].index(label)
        return (860.0, 80.0 + index * 96.0, 160.0)
    if kind == "function":
        index = sorted_groups["function"].index(label)
        return (1080.0, 80.0 + index * 96.0, 160.0)
    if kind == "workflow":
        index = sorted_groups["workflow"].index(label)
        return (80.0, 480.0 + index * 180.0, 160.0)
    if kind == "stage":
        if node_id not in workflow_stage_positions:
            return (80.0, 480.0, 160.0)
        return (*workflow_stage_positions[node_id], 160.0)
    if kind == "approval":
        # placed adjacent to the workflow or swarm that owns it
        if label.startswith("workflow:"):
            index = sorted_groups["workflow"].index(label.split(":", 1)[1])
            y = 480.0 + index * 180.0 + 90.0
            return (80.0, y, 160.0)
        if label.startswith("swarm:"):
            index = sorted_groups["swarm"].index(label.split(":", 1)[1])
            y = 80.0 + index * 96.0 + 90.0
            return (600.0, y, 160.0)
        return (80.0, 80.0, 160.0)
    if kind in _NETWORK_KINDS:
        return (980.0, 80.0, 160.0)
    return (80.0, 80.0, 160.0)


def _edge_map(mindmap_relation: str) -> str | None:
    """Map a mindmap edge *relation* to a design edge kind, or None to drop.

    Keyed strictly by ``MindmapEdge.relation``. Edge labels are prose and are
    never passed here; an earlier version mixed both and silently dropped
    every edge whose relation had no label twin.
    """
    return {
        "imports": "imports",
        "contains": "contains",
        "has_field": None,  # not drawn in design view
        "field_type": None,  # not drawn in design view
        "input": "inputs",
        "output": "outputs",
        "parameter": None,  # not drawn in design view
        "binding_type": None,  # not drawn in design view
        "can": "uses",
        "cannot": None,  # rendered as prohibition walls on the agent node
        "model": "uses",
        "coordinates": "uses",
        "runs": "invokes",
        "calls": "calls",
        "computes": "calls",
        "asks": "calls",
        "executes": "calls",
        "step": "contains",
        "workflow_call": "calls",
        "agent_call": "calls",
        "tool_call": "calls",
        "model_call": "calls",
        "function_call": "calls",
        "requires_approval": "requires_approval",
        "network_context": "network_context",
        "connects": "connects",
    }.get(mindmap_relation)


def _agent_prohibitions(
    mindmap_nodes: list[dict[str, Any]], mindmap_edges: list[dict[str, str]]
) -> dict[str, list[str]]:
    """Map agent labels to their sorted prohibition lists from `cannot` edges."""
    prohibited: dict[str, list[str]] = {}
    for edge in mindmap_edges:
        if edge["relation"] != "cannot":
            continue
        source_node = _node_by_id(mindmap_nodes, edge["source"])
        if source_node is None or source_node["kind"] != "agent":
            continue
        target_node = _node_by_id(mindmap_nodes, edge["target"])
        prohibited_name = (
            target_node["label"] if target_node is not None else edge["target"]
        )
        prohibited.setdefault(source_node["label"], []).append(prohibited_name)
    return {agent: sorted(items) for agent, items in sorted(prohibited.items())}


def _design_kind(mindmap_kind: str) -> str:
    """Map a mindmap node kind into the design vocabulary.

    The mindmap calls a workflow step a "step"; the design lab presents the
    same node as a "stage".
    """
    return "stage" if mindmap_kind == "step" else mindmap_kind


def _design_node_id(mindmap_kind: str, mindmap_id: str) -> str:
    """Re-key a mindmap id into the design vocabulary.

    The mindmap addresses declarations as ``kind:key``; a design manifest
    addresses them as ``kind.key`` so node ids compose with edge ids
    (``source.kind.target``) and with ``network.<id>`` roots. The kind prefix
    is rewritten too, so a mindmap ``step`` becomes a design ``stage``.
    """
    key = mindmap_id[len(mindmap_kind) :].lstrip(":").replace(":", ".")
    return f"{_design_kind(mindmap_kind)}.{key}"


def mindmap_to_design_graph(
    mindmap: dict[str, Any],
    *,
    network: NetworkManifest | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[dict[str, Any]]]:
    """Lay out a mindmap as a design graph with spatial positions and rendering attrs.

    The mindmap is the canonical structure; this function only assigns positions,
    renames edge kinds for the design vocabulary, and extracts prohibition lists
    for the renderer. No AST is traversed here.
    """
    mm_nodes = mindmap["nodes"]
    mm_edges = mindmap["edges"]

    # group labels by kind for stable layout ordering
    sorted_groups: dict[str, list[str]] = {}
    for node in mm_nodes:
        kind = _design_kind(node["kind"])
        if kind in _LAYOUT_KINDS:
            sorted_groups.setdefault(kind, []).append(node["label"])
    for labels in sorted_groups.values():
        labels.sort()

    # workflow stage positions depend on their parent workflow y-coordinate.
    # Keyed by mindmap node id, because a step label ("1. respond") repeats
    # across workflows.
    workflow_stage_positions: dict[str, tuple[float, float]] = {}

    def _owning_workflow(node: dict[str, Any]) -> str:
        return str(node.get("attributes", {}).get("workflow", ""))

    for node in mm_nodes:
        if _design_kind(node["kind"]) != "stage":
            continue
        workflow_name = _owning_workflow(node)
        if workflow_name not in sorted_groups.get("workflow", []):
            continue
        workflow_index = sorted_groups["workflow"].index(workflow_name)
        workflow_y = 480.0 + workflow_index * 180.0
        # find step index among stages belonging to this workflow
        step_index = 0
        for other in mm_nodes:
            if _design_kind(other["kind"]) != "stage":
                continue
            if _owning_workflow(other) != workflow_name:
                continue
            if other["id"] == node["id"]:
                break
            step_index += 1
        workflow_stage_positions[node["id"]] = (340.0 + step_index * 210.0, workflow_y)

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    # mindmap id -> design id, so edges can be re-keyed the same way
    design_ids: dict[str, str] = {}
    seen_edge_ids: set[str] = set()

    for node in mm_nodes:
        kind = _design_kind(node["kind"])
        if kind not in _LAYOUT_KINDS:
            continue
        x, y, width = _layout_node_position(
            kind, node["label"], sorted_groups, workflow_stage_positions, node["id"]
        )
        design_id = _design_node_id(node["kind"], node["id"])
        design_node: dict[str, Any] = {
            "id": design_id,
            "label": node["label"],
            "kind": kind,
            "position": {"x": x, "y": y},
            "size": {"width": width, "height": 64.0},
        }
        if kind == "agent":
            design_node["can"] = sorted(node["attributes"].get("can", []))
            design_node["cannot"] = sorted(node["attributes"].get("cannot", []))
            design_node["max_steps"] = int(node["attributes"].get("max_steps", 8))
        if kind == "record":
            design_node["width"] = width
        if kind == "module":
            design_node["width"] = width
            design_node["height"] = 64.0
        if "attributes" in node:
            design_node.setdefault("attributes", {})
            design_node["attributes"] = dict(node["attributes"])
        nodes.append(design_node)
        design_ids[node["id"]] = design_id

    # An approval gate is a property of the design view, so it is synthesized
    # here: the mindmap records "this swarm requires approval" as an attribute
    # rather than as its own node.
    approval_owners: list[tuple[str, str]] = []
    for node in mm_nodes:
        if not node.get("attributes", {}).get("approval_required"):
            continue
        owner_id = design_ids.get(node["id"])
        if owner_id is None:
            continue
        approval_id = f"approval.{owner_id}"
        label = f"{node['kind']}:{node['label']}"
        x, y, width = _layout_node_position(
            "approval", label, sorted_groups, workflow_stage_positions, approval_id
        )
        nodes.append(
            {
                "id": approval_id,
                "label": label,
                "kind": "approval",
                "position": {"x": x, "y": y},
                "size": {"width": width, "height": 64.0},
            }
        )
        design_ids[approval_id] = approval_id
        approval_owners.append((owner_id, approval_id))

    edge_index: dict[str, int] = {}
    for owner_id, approval_id in approval_owners:
        edges.append(
            {
                "id": f"{owner_id}.requires_approval.{approval_id}",
                "source": owner_id,
                "target": approval_id,
                "kind": "requires_approval",
            }
        )
        seen_edge_ids.add(f"{owner_id}.requires_approval.{approval_id}")

    for edge in mm_edges:
        design_kind = _edge_map(edge["relation"])
        if design_kind is None:
            continue
        source_id = design_ids.get(edge["source"])
        target_id = design_ids.get(edge["target"])
        if source_id is None or target_id is None:
            continue
        if design_kind == "inputs":
            # The mindmap records a declaration ("this model accepts Question"),
            # so it points model -> record. The design view draws data flow, so
            # the input record feeds the consumer instead.
            source_id, target_id = target_id, source_id
        edge_id = f"{source_id}.{design_kind}.{target_id}"
        count = edge_index.get(edge_id, 0)
        edge_index[edge_id] = count + 1
        if count > 0:
            edge_id = f"{edge_id}_{count}"
        edges.append(
            {
                "id": edge_id,
                "source": source_id,
                "target": target_id,
                "kind": design_kind,
            }
        )
        seen_edge_ids.add(edge_id)

    network_nodes: dict[str, dict[str, Any]] = {}
    network_node_ids: set[str] = set()
    if network is not None:
        network_root_id = f"network.{network.network_id}"
        network_nodes[network_root_id] = {
            "id": network_root_id,
            "label": network.name,
            "kind": "network",
            "position": {"x": 980.0, "y": 700.0},
            "size": {"width": 220.0, "height": 64.0},
        }
        for index, nn in enumerate(
            sorted(network.nodes, key=lambda item: str(item["id"]))
        ):
            nn_id = f"network_node.{nn['id']}"
            network_nodes[nn_id] = {
                "id": nn_id,
                "label": str(nn.get("name", nn["id"])),
                "kind": "network_node",
                "position": {"x": 980.0, "y": 80.0 + index * 96.0},
                "size": {"width": 160.0, "height": 64.0},
            }
            network_node_ids.add(nn["id"])
        for link in sorted(network.links, key=lambda item: str(item["id"])):
            if (
                link["source"] in network_node_ids
                and link["target"] in network_node_ids
            ):
                edges.append(
                    {
                        "id": f"network.{link['id']}",
                        "source": f"network_node.{link['source']}",
                        "target": f"network_node.{link['target']}",
                        "kind": "connects",
                    }
                )
        # The network is ambient context for everything that executes, so the
        # workflow view links it to each agent rather than to a single root.
        for node in mm_nodes:
            if node["kind"] != "agent":
                continue
            agent_id = design_ids.get(node["id"])
            if agent_id is None:
                continue
            edges.append(
                {
                    "id": f"{network_root_id}.network_context.{agent_id}",
                    "source": network_root_id,
                    "target": agent_id,
                    "kind": "network_context",
                }
            )
        views = [
            {"id": "workflow-view", "name": "Agent workflows", "kind": "workflow"},
            {"id": "network-view", "name": "Network topology", "kind": "network"},
            {"id": "mindmap-view", "name": "Structure mindmap", "kind": "memory_graph"},
        ]
        nodes.extend(network_nodes.values())
    else:
        views = [
            {"id": "workflow-view", "name": "Agent workflows", "kind": "workflow"},
            {"id": "mindmap-view", "name": "Structure mindmap", "kind": "memory_graph"},
        ]

    return nodes, edges, views


def build_design_from_program(
    program: Program,
    design_id: str,
    name: str,
    *,
    network: NetworkManifest | None = None,
) -> DesignManifest:
    """Build a deterministic vector design from checked Kosika declarations.

    Structure is derived from the canonical mindmap; layout is a pure function
    of node kind, label, and stable sort order. The design shares the mindmap
    digest so the two views stay verifiably aligned.
    """
    mindmap = build_mindmap(program)
    nodes, edges, views = mindmap_to_design_graph(mindmap, network=network)
    data = {
        "manifestVersion": 1,
        "designVersion": 1,
        "design": {
            "id": design_id,
            "name": name,
            "kind": "agent_workflow_network",
            "canvas": {"width": 1400, "height": 1000, "unit": "px"},
            "mindmapDigest": mindmap.get("digest", ""),
        },
        "nodes": nodes,
        "edges": edges,
        "views": views,
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
    "road": "#2F7D4F",
    "label_fill": "#0C121A",
    "label_text": "#8A9BAD",
    "legend_fill": "#0A0F16",
    "legend_stroke": "#1B2836",
    "legend_text": "#8A9BAD",
    "node_label": "#DBE7F1",
    "wall_fill": "#1A1013",
    "wall_stroke": "#E0644F",
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
    "road": "#15803D",
    "label_fill": "#ffffff",
    "label_text": "#64748b",
    "legend_fill": "#ffffff",
    "legend_stroke": "#e2e8f0",
    "legend_text": "#475569",
    "node_label": "#0f172a",
    "wall_fill": "#FDF0EC",
    "wall_stroke": "#B4341F",
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
        f'    <rect width="350" height="{54 if any(_prohibitions(node) for node in manifest.nodes) else 42}" rx="14" fill="{chrome["legend_fill"]}" '
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
    if any(_prohibitions(node) for node in manifest.nodes):
        # The legend explains the hatch, otherwise the walls read as decoration.
        y = 16 + (len(kinds[:6]) // 3 + 1) * 18
        lines.append(
            f'    <rect x="12" y="{y - 9}" width="10" height="10" rx="3" '
            f'fill="url(#prohibition)" />'
        )
        lines.append(
            f'    <text x="27" y="{y}" font-family="sans-serif" font-size="10" '
            f'fill="{chrome["legend_text"]}">cannot</text>'
        )
    lines.append("  </g>")
    return lines


PROHIBITION_BAND = 9
PROHIBITION_LABEL = 26

# Edge kinds drawn as a road rather than a thin connection. A road means
# "these run in this order", which is only true of call edges, so a design
# that invents one will look like a street map that is not one.
ROAD_EDGE_KINDS = frozenset({"calls"})


def _prohibitions(node: dict[str, Any]) -> list[str]:
    """The capabilities a node declared it ``cannot`` use.

    Only agent nodes carry prohibitions, and an agent that declared none
    returns an empty list rather than being drawn with an empty wall.
    """
    raw = node.get("cannot")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str) and item.strip()]


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


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
        '    <pattern id="prohibition" width="8" height="8" patternUnits="userSpaceOnUse" '
        'patternTransform="rotate(45)">',
        f'      <rect width="8" height="8" fill="{chrome["wall_fill"]}" />',
        f'      <line x1="0" y1="0" x2="0" y2="8" stroke="{chrome["wall_stroke"]}" stroke-width="2.5" />',
        "    </pattern>",
        "  </defs>",
        '  <rect width="100%" height="100%" fill="url(#background)" />',
        f'  <circle cx="1280" cy="850" r="220" fill="{chrome["glow"][0][0]}" fill-opacity="{chrome["glow"][0][1]}" />',
        f'  <circle cx="140" cy="850" r="180" fill="{chrome["glow"][1][0]}" fill-opacity="{chrome["glow"][1][1]}" />',
        f'  <text x="32" y="34" font-family="sans-serif" font-size="23" font-weight="700" '
        f'fill="{chrome["title"]}">{html.escape(manifest.name)}</text>',
        f'  <text x="32" y="55" font-family="sans-serif" font-size="12" fill="{chrome["muted"]}">'
        f"{html.escape(view.title())} view · deterministic design graph</text>",
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
        road = str(edge["kind"]) in ROAD_EDGE_KINDS
        lines.append(
            f'  <path d="{path}" fill="none" stroke="{chrome["road"] if road else chrome["edge"]}" '
            f'stroke-width="{(7 if road else 2):g}" stroke-linecap="round" '
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
            f'font-size="15" font-weight="600" fill="{chrome["node_label"]}">{label}</text>'
        )
        walls = _prohibitions(node)
        if walls:
            wall_y = y + node_height
            lines.append(
                f'    <g data-prohibitions="{html.escape(", ".join(walls))}">'
                f'<rect x="{x:g}" y="{wall_y:g}" width="{node_width:g}" height="{PROHIBITION_BAND:g}" '
                f'fill="url(#prohibition)" />'
                f'<text x="{x + node_width / 2:g}" y="{wall_y + PROHIBITION_BAND + 13:g}" '
                f'font-family="sans-serif" font-size="10" text-anchor="middle" '
                f'fill="{chrome["wall_stroke"]}">walls: {html.escape(_shorten(", ".join(walls), PROHIBITION_LABEL))}</text>'
                "</g>"
            )
        lines.append("</g>")
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
