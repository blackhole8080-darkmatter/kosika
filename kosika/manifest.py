from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .mcp_stdio import MCPProtocolError, MCPStdioClient


class ManifestError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def schema_hash(input_schema: dict[str, Any], output_schema: dict[str, Any]) -> str:
    payload = {"inputSchema": input_schema, "outputSchema": output_schema}
    return hashlib.sha256(_canonical(payload)).hexdigest()


@dataclass(frozen=True)
class PinnedTool:
    name: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    schema_hash: str


@dataclass(frozen=True)
class MCPManifest:
    source: str
    protocol_version: str
    server_name: str
    server_version: str
    tools: dict[str, PinnedTool]
    manifest_version: int = 1


def manifest_from_client(client: MCPStdioClient, source: str) -> MCPManifest:
    """Create a deterministic manifest from an already initialized MCP client."""
    if not source:
        raise ManifestError("Manifest source cannot be empty")
    if client.protocol_version_negotiated is None:
        raise ManifestError("MCP client has not completed initialization")
    server_name = client.server_info.get("name")
    server_version = client.server_info.get("version")
    if not isinstance(server_name, str) or not isinstance(server_version, str):
        raise ManifestError(
            "MCP initialize response did not provide server name/version"
        )

    tools: dict[str, PinnedTool] = {}
    for tool in sorted(client.list_tools(), key=lambda item: item.name):
        if not isinstance(tool.input_schema, dict) or not isinstance(
            tool.output_schema, dict
        ):
            raise ManifestError(
                f"MCP tool {tool.name!r} did not provide object schemas"
            )
        if tool.name in tools:
            raise ManifestError(f"MCP server advertised duplicate tool {tool.name!r}")
        tools[tool.name] = PinnedTool(
            name=tool.name,
            input_schema=tool.input_schema,
            output_schema=tool.output_schema,
            schema_hash=schema_hash(tool.input_schema, tool.output_schema),
        )
    return MCPManifest(
        source=source,
        protocol_version=client.protocol_version_negotiated,
        server_name=server_name,
        server_version=server_version,
        tools=tools,
    )


def manifest_to_dict(manifest: MCPManifest) -> dict[str, Any]:
    return {
        "manifestVersion": manifest.manifest_version,
        "server": {
            "source": manifest.source,
            "protocolVersion": manifest.protocol_version,
            "name": manifest.server_name,
            "version": manifest.server_version,
        },
        "tools": {
            name: {
                "inputSchema": tool.input_schema,
                "outputSchema": tool.output_schema,
                "schemaHash": tool.schema_hash,
            }
            for name, tool in sorted(manifest.tools.items())
        },
    }


def write_manifest(manifest: MCPManifest, path: str | Path) -> None:
    manifest_path = Path(path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest_to_dict(manifest), indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def diff_manifests(expected: MCPManifest, observed: MCPManifest) -> dict[str, Any]:
    server_changes: dict[str, dict[str, str]] = {}
    for label, expected_value, observed_value in (
        ("protocolVersion", expected.protocol_version, observed.protocol_version),
        ("name", expected.server_name, observed.server_name),
        ("version", expected.server_version, observed.server_version),
    ):
        if expected_value != observed_value:
            server_changes[label] = {
                "expected": expected_value,
                "actual": observed_value,
            }

    expected_names = set(expected.tools)
    observed_names = set(observed.tools)
    added = sorted(observed_names - expected_names)
    removed = sorted(expected_names - observed_names)
    changed: dict[str, dict[str, Any]] = {}
    for name in sorted(expected_names & observed_names):
        pinned = expected.tools[name]
        actual = observed.tools[name]
        entry: dict[str, Any] = {}
        if pinned.input_schema != actual.input_schema:
            entry["inputSchema"] = {
                "expected": pinned.input_schema,
                "actual": actual.input_schema,
            }
        if pinned.output_schema != actual.output_schema:
            entry["outputSchema"] = {
                "expected": pinned.output_schema,
                "actual": actual.output_schema,
            }
        if pinned.schema_hash != actual.schema_hash:
            entry["schemaHash"] = {
                "expected": pinned.schema_hash,
                "actual": actual.schema_hash,
            }
        if entry:
            changed[name] = entry

    return {
        "drift": bool(server_changes or added or removed or changed),
        "server": server_changes,
        "tools": {
            "added": added,
            "removed": removed,
            "changed": changed,
        },
    }


def format_manifest_diff(diff: dict[str, Any]) -> str:
    if not diff["drift"]:
        return "No drift: live MCP server matches the pinned manifest."

    lines = ["MCP manifest drift detected:"]
    for label, values in diff["server"].items():
        lines.append(
            f"  server {label}: {values['expected']!r} -> {values['actual']!r}"
        )
    tools = diff["tools"]
    if tools["added"]:
        lines.append(f"  tools added: {', '.join(tools['added'])}")
    if tools["removed"]:
        lines.append(f"  tools removed: {', '.join(tools['removed'])}")
    for name, changes in tools["changed"].items():
        lines.append(f"  tool changed: {name}")
        if "inputSchema" in changes:
            lines.append("    input schema changed")
        if "outputSchema" in changes:
            lines.append("    output schema changed")
        if "schemaHash" in changes:
            lines.append(
                "    schema hash: "
                f"{changes['schemaHash']['expected']} -> {changes['schemaHash']['actual']}"
            )
    return "\n".join(lines)


def load_manifest(path: str | Path) -> MCPManifest:
    manifest_path = Path(path)
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(
            f"Could not read MCP manifest {manifest_path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise ManifestError("MCP manifest must be a JSON object")
    server = data.get("server")
    raw_tools = data.get("tools")
    if not isinstance(server, dict) or not isinstance(raw_tools, dict):
        raise ManifestError("MCP manifest requires server and tools objects")
    required_server = {"source", "protocolVersion", "name", "version"}
    missing = required_server - server.keys()
    if missing:
        raise ManifestError(f"MCP manifest server is missing {sorted(missing)!r}")
    tools: dict[str, PinnedTool] = {}
    for name, raw in raw_tools.items():
        if not isinstance(raw, dict):
            raise ManifestError(f"Manifest tool {name!r} must be an object")
        input_schema = raw.get("inputSchema")
        output_schema = raw.get("outputSchema")
        pinned_hash = raw.get("schemaHash")
        if (
            not isinstance(input_schema, dict)
            or not isinstance(output_schema, dict)
            or not isinstance(pinned_hash, str)
        ):
            raise ManifestError(
                f"Manifest tool {name!r} requires inputSchema, outputSchema, and schemaHash"
            )
        actual_hash = schema_hash(input_schema, output_schema)
        if pinned_hash != actual_hash:
            raise ManifestError(f"Manifest tool {name!r} has an invalid schemaHash")
        tools[name] = PinnedTool(name, input_schema, output_schema, pinned_hash)
    return MCPManifest(
        source=str(server["source"]),
        protocol_version=str(server["protocolVersion"]),
        server_name=str(server["name"]),
        server_version=str(server["version"]),
        tools=tools,
        manifest_version=int(data.get("manifestVersion", 1)),
    )


def verify_server(
    client: MCPStdioClient, manifest: MCPManifest
) -> dict[str, PinnedTool]:
    if client.protocol_version_negotiated != manifest.protocol_version:
        raise MCPProtocolError(
            f"MCP protocol mismatch: manifest pins {manifest.protocol_version!r}, "
            f"server negotiated {client.protocol_version_negotiated!r}"
        )
    if client.server_info.get("name") != manifest.server_name:
        raise MCPProtocolError(
            f"MCP server identity mismatch: expected {manifest.server_name!r}, "
            f"got {client.server_info.get('name')!r}"
        )
    if client.server_info.get("version") != manifest.server_version:
        raise MCPProtocolError(
            f"MCP server version mismatch: expected {manifest.server_version!r}, "
            f"got {client.server_info.get('version')!r}"
        )

    advertised = {tool.name: tool for tool in client.list_tools()}
    unexpected = set(advertised) - set(manifest.tools)
    if unexpected:
        raise MCPProtocolError(
            f"MCP server advertised unpinned tools: {sorted(unexpected)!r}"
        )
    for name, pinned in manifest.tools.items():
        actual = advertised.get(name)
        if actual is None:
            raise MCPProtocolError(
                f"Pinned MCP tool {name!r} is not advertised by the server"
            )
        if actual.input_schema != pinned.input_schema:
            raise MCPProtocolError(
                f"Input schema mismatch for pinned MCP tool {name!r}"
            )
        if actual.output_schema and actual.output_schema != pinned.output_schema:
            raise MCPProtocolError(
                f"Output schema mismatch for pinned MCP tool {name!r}"
            )
    return manifest.tools
