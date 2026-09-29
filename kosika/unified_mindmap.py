"""Deterministic graph combining Kosika and external source maps."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .mindmap import build_mindmap
from .modules import load_program
from .source_map import build_source_map

UNIFIED_SCHEMA = "kosika.unified-mindmap/v1"
MAX_NODES = 20_000
MAX_EDGES = 80_000


class UnifiedMindmapError(ValueError):
    """Raised when a combined graph exceeds its explicit bounds."""


def build_unified_mindmap(
    source: str | Path,
    *,
    project: str | Path | None = None,
) -> dict[str, Any]:
    """Build one graph without guessing links between Kosika and source code."""

    source_path = Path(source)
    program = load_program(source_path)
    kosika = build_mindmap(program)
    project_root = Path(project).resolve().as_posix() if project is not None else None
    external = build_source_map(project) if project is not None else None
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    nodes.append(
        {
            "id": "kosika:root",
            "kind": "project",
            "label": "Kosika",
            "module": "<kosika>",
            "language": "kosika",
            "file": source_path.as_posix(),
            "attributes": {"source": source_path.as_posix()},
        }
    )
    for node in kosika["nodes"]:
        identifier = f"kosika:{node['id']}"
        nodes.append(
            {
                "id": identifier,
                "kind": node["kind"],
                "label": node["label"],
                "module": node["module"],
                "language": "kosika",
                "file": source_path.as_posix(),
                "attributes": dict(node.get("attributes", {})),
            }
        )
    for edge in kosika["edges"]:
        edges.append(
            {
                "source": f"kosika:{edge['source']}",
                "target": f"kosika:{edge['target']}",
                "relation": edge["relation"],
                "label": edge["label"],
            }
        )
    for node in kosika["nodes"]:
        if node["kind"] == "module":
            edges.append(
                {
                    "source": "kosika:root",
                    "target": f"kosika:{node['id']}",
                    "relation": "contains",
                    "label": "Kosika module",
                }
            )

    external_summary: dict[str, Any] | None = None
    if external is not None:
        external_summary = external["summary"]
        nodes.append(
            {
                "id": "source:root",
                "kind": "project",
                "label": external["project"]["name"],
                "module": "<external>",
                "language": "external",
                "file": project_root,
                "attributes": {"root": project_root},
            }
        )
        for node in external["nodes"]:
            identifier = f"source:{node['id']}"
            attributes = {
                key: value
                for key, value in node.items()
                if key not in {"id", "kind", "label", "module", "language", "file"}
            }
            nodes.append(
                {
                    "id": identifier,
                    "kind": node["kind"],
                    "label": node["label"],
                    "module": node.get("module", ""),
                    "language": node.get("language", "external"),
                    "file": node.get("file", ""),
                    "attributes": attributes,
                }
            )
        for edge in external["edges"]:
            edges.append(
                {
                    "source": f"source:{edge['source']}",
                    "target": f"source:{edge['target']}",
                    "relation": edge["relation"],
                    "label": edge["label"],
                }
            )
        for node in external["nodes"]:
            if node["kind"] == "module":
                edges.append(
                    {
                        "source": "source:root",
                        "target": f"source:{node['id']}",
                        "relation": "contains",
                        "label": "external module",
                    }
                )

    if len(nodes) > MAX_NODES or len(edges) > MAX_EDGES:
        raise UnifiedMindmapError(
            f"Unified mindmap exceeds {MAX_NODES} nodes or {MAX_EDGES} edges"
        )
    nodes.sort(key=lambda node: (node["language"], node["id"]))
    edges = sorted(
        edges,
        key=lambda edge: (
            edge["source"],
            edge["target"],
            edge["relation"],
            edge["label"],
        ),
    )
    return {
        "schema": UNIFIED_SCHEMA,
        "source": source_path.as_posix(),
        "project": external["project"] if external is not None else None,
        "kosika": kosika,
        "source_map": external,
        "nodes": nodes,
        "edges": edges,
        "summary": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "kosika_node_count": len(kosika["nodes"]),
            "external_node_count": len(external["nodes"]) if external else 0,
            "external_summary": external_summary,
        },
        "notice": (
            "Kosika and external source graphs are combined without inferred "
            "cross-language links. Unresolved relationships remain explicit."
        ),
    }


def to_mermaid(report: dict[str, Any]) -> str:
    """Render a unified graph as deterministic Mermaid flowchart text."""

    def node_id(identifier: str) -> str:
        return f"n_{hashlib.sha1(identifier.encode('utf-8')).hexdigest()[:12]}"

    def text(value: object) -> str:
        return str(value).replace('"', "'").replace("\n", " ")

    lines = ["flowchart LR"]
    for node in report["nodes"]:
        label = f"{node['label']}<br/>{node['kind']} ({node['language']})"
        lines.append(f'    {node_id(node["id"])}["{text(label)}"]')
    for edge in report["edges"]:
        lines.append(
            f"    {node_id(edge['source'])} -->|{text(edge['label'])}| "
            f"{node_id(edge['target'])}"
        )
    return "\n".join(lines) + "\n"


def to_text(report: dict[str, Any]) -> str:
    """Render a compact unified graph summary."""

    lines = [f"Unified mindmap: {report['source']}", ""]
    for node in report["nodes"]:
        if node["kind"] == "project":
            continue
        lines.append(
            f"- [{node['language']}] {node['kind']} {node['label']} ({node['file']})"
        )
    lines.extend(
        [
            "",
            f"Nodes: {report['summary']['node_count']}",
            f"Edges: {report['summary']['edge_count']}",
            report["notice"],
        ]
    )
    return "\n".join(lines) + "\n"


__all__ = [
    "UNIFIED_SCHEMA",
    "UnifiedMindmapError",
    "build_unified_mindmap",
    "to_mermaid",
    "to_text",
]
