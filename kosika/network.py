from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


class NetworkError(ValueError):
    """Raised when a Kosika network manifest is invalid or unsafe."""


_ALLOWED_ACCESS = {"observe", "propose", "execute"}
_ALLOWED_NODE_KINDS = {
    "kosika",
    "runtime",
    "model",
    "device",
    "memory_graph",
    "virtual_space",
    "service",
}
_ALLOWED_MODULE_KINDS = {
    "connector",
    "device",
    "memory",
    "model_gateway",
    "runtime",
    "telemetry",
    "virtual_space",
}
_ALLOWED_TAB_KINDS = {
    "dashboard",
    "mindmap",
    "network",
    "timeline",
    "memory_graph",
    "model",
    "workspace",
}
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
_SECRET_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise NetworkError(f"{label} must be an object")
    return value


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NetworkError(f"{label} must be a non-empty string")
    return value


def _require_id(value: Any, label: str) -> str:
    identifier = _require_string(value, label)
    if not _ID_RE.fullmatch(identifier):
        raise NetworkError(
            f"{label} must contain only letters, numbers, '.', '_', ':', or '-': "
            f"{identifier!r}"
        )
    return identifier


def _validate_no_secrets(value: Any, location: str = "manifest") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).replace("-", "_").lower()
            if normalized in _SECRET_KEYS:
                raise NetworkError(
                    f"{location} cannot contain credential field {key!r}; "
                    "use an external secret reference"
                )
            _validate_no_secrets(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_no_secrets(child, f"{location}[{index}]")


def _validate_access(value: Any, label: str) -> str:
    access = _require_string(value, label)
    if access not in _ALLOWED_ACCESS:
        raise NetworkError(
            f"{label} must be one of {sorted(_ALLOWED_ACCESS)!r}, got {access!r}"
        )
    return access


def _validate_records(
    records: Any,
    label: str,
    *,
    allowed_kinds: set[str] | None = None,
) -> tuple[dict[str, Any], ...]:
    if records is None:
        return ()
    if not isinstance(records, list):
        raise NetworkError(f"{label} must be an array")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(records):
        record = _require_object(item, f"{label}[{index}]")
        identifier = _require_id(record.get("id"), f"{label}[{index}].id")
        if identifier in seen:
            raise NetworkError(f"{label} contains duplicate id {identifier!r}")
        seen.add(identifier)
        if allowed_kinds is not None:
            kind = _require_string(record.get("kind"), f"{label}[{index}].kind")
            if kind not in allowed_kinds:
                raise NetworkError(
                    f"{label}[{index}].kind must be one of "
                    f"{sorted(allowed_kinds)!r}, got {kind!r}"
                )
        result.append(dict(record))
    return tuple(result)


def _validate_memory_graphs(value: Any) -> tuple[dict[str, Any], ...]:
    graphs = _validate_records(value, "memoryGraphs")
    validated: list[dict[str, Any]] = []
    for index, graph in enumerate(graphs):
        raw_nodes = graph.get("nodes", [])
        raw_edges = graph.get("edges", [])
        if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
            raise NetworkError(f"memoryGraphs[{index}] requires nodes and edges arrays")
        node_ids: set[str] = set()
        nodes: list[dict[str, Any]] = []
        for node_index, raw_node in enumerate(raw_nodes):
            node = _require_object(
                raw_node, f"memoryGraphs[{index}].nodes[{node_index}]"
            )
            node_id = _require_id(
                node.get("id"),
                f"memoryGraphs[{index}].nodes[{node_index}].id",
            )
            if node_id in node_ids:
                raise NetworkError(
                    f"memory graph {graph['id']!r} contains duplicate node {node_id!r}"
                )
            node_ids.add(node_id)
            nodes.append(dict(node))
        edges: list[dict[str, Any]] = []
        edge_ids: set[str] = set()
        for edge_index, raw_edge in enumerate(raw_edges):
            edge = _require_object(
                raw_edge, f"memoryGraphs[{index}].edges[{edge_index}]"
            )
            edge_id = _require_id(
                edge.get("id"),
                f"memoryGraphs[{index}].edges[{edge_index}].id",
            )
            if edge_id in edge_ids:
                raise NetworkError(
                    f"memory graph {graph['id']!r} contains duplicate edge {edge_id!r}"
                )
            source = _require_id(
                edge.get("source"),
                f"memoryGraphs[{index}].edges[{edge_index}].source",
            )
            target = _require_id(
                edge.get("target"),
                f"memoryGraphs[{index}].edges[{edge_index}].target",
            )
            if source not in node_ids or target not in node_ids:
                raise NetworkError(
                    f"memory graph {graph['id']!r} edge {edge_id!r} references "
                    "an unknown memory node"
                )
            edge_ids.add(edge_id)
            edges.append(dict(edge))
        normalized = dict(graph)
        normalized["nodes"] = nodes
        normalized["edges"] = edges
        validated.append(normalized)
    return tuple(validated)


@dataclass(frozen=True)
class NetworkManifest:
    """A validated, declarative Kosika network description."""

    network: dict[str, Any]
    developers: tuple[dict[str, Any], ...]
    modules: tuple[dict[str, Any], ...]
    nodes: tuple[dict[str, Any], ...]
    tabs: tuple[dict[str, Any], ...]
    links: tuple[dict[str, Any], ...]
    memory_graphs: tuple[dict[str, Any], ...]
    manifest_version: int = 1

    @property
    def network_id(self) -> str:
        return str(self.network["id"])

    @property
    def name(self) -> str:
        return str(self.network["name"])

    @property
    def digest(self) -> str:
        return _digest(network_to_dict(self))


def validate_network(data: dict[str, Any]) -> NetworkManifest:
    """Validate a network manifest without connecting to any declared target."""
    _validate_no_secrets(data)
    version = data.get("networkVersion", 1)
    if version != 1:
        raise NetworkError(f"Unsupported networkVersion {version!r}; expected 1")
    network = _require_object(data.get("network"), "network")
    network_id = _require_id(network.get("id"), "network.id")
    name = _require_string(network.get("name"), "network.name")
    normalized_network = dict(network)
    normalized_network["id"] = network_id
    normalized_network["name"] = name
    raw_policy = network.get("policy", {})
    policy = _require_object(raw_policy, "network.policy")
    default_access = policy.get("defaultAccess", "observe")
    _validate_access(default_access, "network.policy.defaultAccess")
    normalized_policy = dict(policy)
    normalized_policy["defaultAccess"] = default_access
    normalized_network["policy"] = normalized_policy

    developers = _validate_records(data.get("developers", []), "developers")
    modules = _validate_records(
        data.get("modules", []),
        "modules",
        allowed_kinds=_ALLOWED_MODULE_KINDS,
    )
    nodes = _validate_records(
        data.get("nodes", []),
        "nodes",
        allowed_kinds=_ALLOWED_NODE_KINDS,
    )
    tabs = _validate_records(
        data.get("tabs", []),
        "tabs",
        allowed_kinds=_ALLOWED_TAB_KINDS,
    )
    memory_graphs = _validate_memory_graphs(data.get("memoryGraphs", []))

    developer_ids = {item["id"] for item in developers}
    module_ids = {item["id"] for item in modules}
    node_ids = {item["id"] for item in nodes}
    tab_ids = {item["id"] for item in tabs}
    graph_ids = {item["id"] for item in memory_graphs}
    if network_id in developer_ids | module_ids | node_ids | tab_ids | graph_ids:
        raise NetworkError("network.id must be unique across manifest entities")

    for label, records, owners in (
        ("developers", developers, developer_ids),
        ("modules", modules, developer_ids),
        ("nodes", nodes, developer_ids),
        ("tabs", tabs, developer_ids),
        ("memoryGraphs", memory_graphs, developer_ids),
    ):
        for record in records:
            owner = record.get("owner")
            if owner is not None and owner not in owners:
                raise NetworkError(
                    f"{label} entry {record['id']!r} references unknown owner {owner!r}"
                )

    for module in modules:
        owner = module.get("owner")
        if owner is not None and owner not in developer_ids:
            raise NetworkError(
                f"module {module['id']!r} references unknown owner {owner!r}"
            )
    for node in nodes:
        module_ref = node.get("module")
        if module_ref is not None and module_ref not in module_ids:
            raise NetworkError(
                f"node {node['id']!r} references unknown module {module_ref!r}"
            )
        graph_ref = node.get("memoryGraph")
        if graph_ref is not None and graph_ref not in graph_ids:
            raise NetworkError(
                f"node {node['id']!r} references unknown memory graph {graph_ref!r}"
            )
        if node["kind"] == "memory_graph" and graph_ref is None:
            raise NetworkError(
                f"memory_graph node {node['id']!r} must reference memoryGraph"
            )
        if "access" in node:
            _validate_access(node["access"], f"node {node['id']!r}.access")
    for tab in tabs:
        target = _require_id(tab.get("target"), f"tab {tab['id']!r}.target")
        if target not in node_ids:
            raise NetworkError(
                f"tab {tab['id']!r} references unknown target {target!r}"
            )

    links = _validate_records(data.get("links", []), "links")
    for link in links:
        source = _require_id(link.get("source"), f"link {link['id']!r}.source")
        target = _require_id(link.get("target"), f"link {link['id']!r}.target")
        if source not in node_ids or target not in node_ids:
            raise NetworkError(
                f"link {link['id']!r} must reference known node source/target"
            )
        access = link.get("access", default_access)
        _validate_access(access, f"link {link['id']!r}.access")
        if access == "execute" and link.get("approvalRequired") is not True:
            raise NetworkError(
                f"execute link {link['id']!r} must set approvalRequired to true"
            )

    return NetworkManifest(
        network=normalized_network,
        developers=developers,
        modules=modules,
        nodes=nodes,
        tabs=tabs,
        links=links,
        memory_graphs=memory_graphs,
        manifest_version=int(data.get("manifestVersion", 1)),
    )


def network_to_dict(manifest: NetworkManifest) -> dict[str, Any]:
    return {
        "manifestVersion": manifest.manifest_version,
        "networkVersion": 1,
        "network": manifest.network,
        "developers": list(manifest.developers),
        "modules": list(manifest.modules),
        "nodes": list(manifest.nodes),
        "tabs": list(manifest.tabs),
        "links": list(manifest.links),
        "memoryGraphs": list(manifest.memory_graphs),
    }


def load_network_manifest(path: str | Path) -> NetworkManifest:
    manifest_path = Path(path)
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NetworkError(
            f"Could not read network manifest {manifest_path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise NetworkError("Network manifest must be a JSON object")
    return validate_network(data)


def write_network_manifest(manifest: NetworkManifest, path: str | Path) -> None:
    manifest_path = Path(path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(network_to_dict(manifest), indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def new_network_manifest(network_id: str, name: str) -> NetworkManifest:
    data = {
        "manifestVersion": 1,
        "networkVersion": 1,
        "network": {
            "id": network_id,
            "name": name,
            "description": "A Kosika network workspace.",
            "policy": {
                "defaultAccess": "observe",
                "allowRemote": False,
                "requireApprovalFor": ["propose", "execute"],
            },
        },
        "developers": [],
        "modules": [],
        "nodes": [],
        "tabs": [],
        "links": [],
        "memoryGraphs": [],
    }
    return validate_network(data)


@dataclass(frozen=True)
class NetworkIndexEntry:
    network_id: str
    name: str
    path: str
    digest: str
    node_count: int
    link_count: int
    module_count: int
    memory_graph_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.network_id,
            "name": self.name,
            "path": self.path,
            "digest": self.digest,
            "nodeCount": self.node_count,
            "linkCount": self.link_count,
            "moduleCount": self.module_count,
            "memoryGraphCount": self.memory_graph_count,
        }


@dataclass(frozen=True)
class NetworkDirectory:
    """A deterministic index over a directory of network manifests."""

    entries: tuple[NetworkIndexEntry, ...]

    @classmethod
    def load(
        cls, directory: str | Path, *, recursive: bool = False
    ) -> "NetworkDirectory":
        root = Path(directory)
        if not root.is_dir():
            raise NetworkError(f"Network directory does not exist: {root}")
        pattern = "**/*.json" if recursive else "*.json"
        paths = sorted(root.glob(pattern), key=lambda item: item.as_posix())
        entries: list[NetworkIndexEntry] = []
        seen_ids: set[str] = set()
        for path in paths:
            manifest = load_network_manifest(path)
            if manifest.network_id in seen_ids:
                raise NetworkError(
                    f"Network directory contains duplicate network id {manifest.network_id!r}"
                )
            seen_ids.add(manifest.network_id)
            entries.append(
                NetworkIndexEntry(
                    network_id=manifest.network_id,
                    name=manifest.name,
                    path=path.as_posix(),
                    digest=manifest.digest,
                    node_count=len(manifest.nodes),
                    link_count=len(manifest.links),
                    module_count=len(manifest.modules),
                    memory_graph_count=len(manifest.memory_graphs),
                )
            )
        return cls(tuple(entries))

    def to_dict(self) -> dict[str, Any]:
        return {
            "networks": [entry.to_dict() for entry in self.entries],
            "summary": {"networkCount": len(self.entries)},
        }


def _label(record: dict[str, Any]) -> str:
    return str(record.get("name", record["id"]))


def render_network_text(manifest: NetworkManifest) -> str:
    network = manifest.network
    policy = network["policy"]
    lines = [
        f"Network: {_label(network)} ({manifest.network_id})",
        f"Digest: {manifest.digest}",
        f"Policy: default={policy['defaultAccess']}, remote={policy.get('allowRemote', False)}",
        "",
        f"Developers ({len(manifest.developers)}):",
    ]
    for developer in manifest.developers:
        lines.append(f"  - {developer['id']}: {_label(developer)}")
    lines.append(f"Modules ({len(manifest.modules)}):")
    for module in manifest.modules:
        lines.append(f"  - {module['id']} [{module['kind']}]: {_label(module)}")
    lines.append(f"Nodes ({len(manifest.nodes)}):")
    for node in manifest.nodes:
        details = [node["kind"]]
        if node.get("module"):
            details.append(f"module={node['module']}")
        if node.get("memoryGraph"):
            details.append(f"memoryGraph={node['memoryGraph']}")
        lines.append(f"  - {node['id']} [{', '.join(details)}]: {_label(node)}")
    lines.append(f"Tabs ({len(manifest.tabs)}):")
    for tab in manifest.tabs:
        lines.append(f"  - {tab['id']} [{tab['kind']}] -> {tab['target']}")
    lines.append(f"Links ({len(manifest.links)}):")
    for link in manifest.links:
        access = link.get("access", policy["defaultAccess"])
        approval = " approval-required" if link.get("approvalRequired") else ""
        lines.append(
            f"  - {link['source']} -[{link.get('kind', 'connect')}, {access}{approval}]-> "
            f"{link['target']}"
        )
    lines.append(f"Memory graphs ({len(manifest.memory_graphs)}):")
    for graph in manifest.memory_graphs:
        lines.append(
            f"  - {graph['id']}: {len(graph['nodes'])} node(s), "
            f"{len(graph['edges'])} edge(s)"
        )
    return "\n".join(lines)


def _mermaid_id(prefix: str, identifier: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]", "_", identifier)
    return f"{prefix}_{safe}"


def render_network_mermaid(manifest: NetworkManifest) -> str:
    lines = ["flowchart LR"]
    for node in manifest.nodes:
        node_id = _mermaid_id("node", node["id"])
        lines.append(f'  {node_id}["{_label(node)}\\n({node["kind"]})"]')
    for link in manifest.links:
        source = _mermaid_id("node", link["source"])
        target = _mermaid_id("node", link["target"])
        kind = str(link.get("kind", "connect")).replace('"', "'")
        lines.append(f"  {source} -->|{kind}| {target}")
    for tab in manifest.tabs:
        tab_id = _mermaid_id("tab", tab["id"])
        target = _mermaid_id("node", tab["target"])
        lines.append(f'  {tab_id}{{"tab: {_label(tab)}"}} -.-> {target}')
    return "\n".join(lines)


def render_network_index_text(directory: NetworkDirectory) -> str:
    lines = [f"Networks ({len(directory.entries)}):"]
    for entry in directory.entries:
        lines.append(
            f"  - {entry.network_id}: {entry.name} | "
            f"nodes={entry.node_count}, links={entry.link_count}, "
            f"modules={entry.module_count}, memoryGraphs={entry.memory_graph_count} | "
            f"{entry.path}"
        )
    return "\n".join(lines)


def render_network(manifest: NetworkManifest, output_format: str = "text") -> str:
    if output_format == "text":
        return render_network_text(manifest)
    if output_format == "json":
        return json.dumps(network_to_dict(manifest), indent=2, sort_keys=True)
    if output_format == "mermaid":
        return render_network_mermaid(manifest)
    raise NetworkError(f"Unsupported network output format {output_format!r}")


def render_directory(directory: NetworkDirectory, output_format: str = "text") -> str:
    if output_format == "text":
        return render_network_index_text(directory)
    if output_format == "json":
        return json.dumps(directory.to_dict(), indent=2, sort_keys=True)
    raise NetworkError(
        f"Unsupported network directory output format {output_format!r}; "
        "use text or json"
    )


def iter_network_paths(
    directory: str | Path, *, recursive: bool = False
) -> Iterable[Path]:
    root = Path(directory)
    pattern = "**/*.json" if recursive else "*.json"
    return sorted(root.glob(pattern), key=lambda item: item.as_posix())
