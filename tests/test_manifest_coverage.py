import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from kosika.manifest import (
    ManifestError,
    PinnedTool,
    diff_manifests,
    format_manifest_diff,
    load_manifest,
    manifest_from_client,
    manifest_to_dict,
    schema_hash,
    verify_server,
    write_manifest,
)
from kosika.mcp_stdio import MCPProtocolError, MCPStdioClient, MCPTool

ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / "examples" / "mcp_manifest.json"


class FakeClient:
    def __init__(
        self,
        tools: list[MCPTool],
        *,
        protocol: str | None = "2025-11-25",
        name: object = "kosika-test-mcp",
        version: object = "0.1.0",
    ) -> None:
        self.protocol_version_negotiated = protocol
        self.server_info: dict[str, object] = {"name": name, "version": version}
        self._tools = tools

    def list_tools(self) -> list[MCPTool]:
        return self._tools


def tool(
    name: str = "get_disk_info",
    *,
    input_schema: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
) -> MCPTool:
    return MCPTool(
        name=name,
        description="test tool",
        input_schema=input_schema or {"type": "object"},
        output_schema=output_schema or {"type": "object"},
    )


class ManifestCoverageTests(unittest.TestCase):
    def test_manifest_from_client_validates_client_state_and_sorts_tools(self) -> None:
        client = FakeClient([tool("z_tool"), tool("a_tool")])
        manifest = manifest_from_client(cast(MCPStdioClient, client), "test-source")
        self.assertEqual(list(manifest.tools), ["a_tool", "z_tool"])
        self.assertEqual(
            manifest.tools["a_tool"].schema_hash,
            schema_hash({"type": "object"}, {"type": "object"}),
        )

        with self.assertRaisesRegex(ManifestError, "source cannot be empty"):
            manifest_from_client(cast(MCPStdioClient, client), "")
        with self.assertRaisesRegex(ManifestError, "has not completed"):
            manifest_from_client(
                cast(MCPStdioClient, FakeClient([], protocol=None)), "source"
            )
        bad_server = FakeClient([], name=None)
        with self.assertRaisesRegex(ManifestError, "server name/version"):
            manifest_from_client(cast(MCPStdioClient, bad_server), "source")
        bad_schema = FakeClient(
            [
                MCPTool(
                    "bad",
                    "",
                    cast(dict[str, Any], []),
                    {"type": "object"},
                )
            ]
        )
        with self.assertRaisesRegex(ManifestError, "object schemas"):
            manifest_from_client(cast(MCPStdioClient, bad_schema), "source")
        duplicate = FakeClient([tool("same"), tool("same")])
        with self.assertRaisesRegex(ManifestError, "duplicate tool"):
            manifest_from_client(cast(MCPStdioClient, duplicate), "source")

    def test_manifest_round_trip_and_missing_file_errors(self) -> None:
        manifest = load_manifest(MANIFEST)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "manifest.json"
            write_manifest(manifest, path)
            self.assertEqual(load_manifest(path), manifest)
            self.assertEqual(manifest_to_dict(manifest), json.loads(path.read_text()))
            with self.assertRaises(ManifestError):
                load_manifest(Path(directory) / "does-not-exist.json")

            invalid_json = Path(directory) / "invalid.json"
            invalid_json.write_text("not json", encoding="utf-8")
            with self.assertRaises(ManifestError):
                load_manifest(invalid_json)

            non_object = Path(directory) / "list.json"
            non_object.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ManifestError, "JSON object"):
                load_manifest(non_object)

    def test_manifest_load_rejects_invalid_shapes(self) -> None:
        valid = json.loads(MANIFEST.read_text(encoding="utf-8"))
        cases: list[dict[str, Any]] = []
        cases.append({"tools": {}})
        cases.append({"server": {"source": "x"}, "tools": {}})
        cases.append({"server": valid["server"], "tools": {"bad": "text"}})
        cases.append(
            {
                "server": valid["server"],
                "tools": {"bad": {"inputSchema": {}, "outputSchema": {}}},
            }
        )
        for index, payload in enumerate(cases):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "invalid.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(ManifestError):
                    load_manifest(path)

    def test_diff_and_format_cover_server_and_tool_changes(self) -> None:
        base = load_manifest(MANIFEST)
        pinned = base.tools["get_disk_info"]
        changed_input = {"type": "object", "properties": {"mount": {"type": "string"}}}
        changed_output = {"type": "object", "properties": {"free": {"type": "number"}}}
        changed = PinnedTool(
            name="get_disk_info",
            input_schema=changed_input,
            output_schema=changed_output,
            schema_hash=schema_hash(changed_input, changed_output),
        )
        removed = PinnedTool(
            name="removed",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            schema_hash=schema_hash({"type": "object"}, {"type": "object"}),
        )
        expected = replace(base, tools={pinned.name: pinned, "removed": removed})
        observed = replace(
            base,
            protocol_version="different-protocol",
            server_name="different-name",
            server_version="different-version",
            tools={"get_disk_info": changed, "added": changed},
        )
        diff = diff_manifests(expected, observed)
        self.assertTrue(diff["drift"])
        self.assertEqual(diff["tools"]["added"], ["added"])
        self.assertEqual(diff["tools"]["removed"], ["removed"])
        self.assertIn("get_disk_info", diff["tools"]["changed"])
        rendered = format_manifest_diff(diff)
        self.assertIn("protocolVersion", rendered)
        self.assertIn("tools added: added", rendered)
        self.assertIn("tools removed: removed", rendered)
        self.assertIn("input schema changed", rendered)
        self.assertIn("output schema changed", rendered)
        self.assertIn("schema hash:", rendered)
        self.assertEqual(
            format_manifest_diff(diff_manifests(base, base)),
            "No drift: live MCP server matches the pinned manifest.",
        )

    def test_verify_server_rejects_each_kind_of_drift(self) -> None:
        base = load_manifest(MANIFEST)
        good_tool = tool(
            input_schema=base.tools["get_disk_info"].input_schema,
            output_schema=base.tools["get_disk_info"].output_schema,
        )
        self.assertEqual(
            verify_server(cast(MCPStdioClient, FakeClient([good_tool])), base),
            base.tools,
        )
        cases = [
            (FakeClient([good_tool], protocol="wrong"), "protocol mismatch"),
            (FakeClient([good_tool], name="wrong"), "identity mismatch"),
            (FakeClient([good_tool], version="wrong"), "version mismatch"),
            (FakeClient([good_tool, tool("extra")]), "unpinned tools"),
            (FakeClient([]), "not advertised"),
            (
                FakeClient(
                    [tool(input_schema={"type": "object", "properties": {"x": {}}})]
                ),
                "Input schema mismatch",
            ),
            (
                FakeClient(
                    [
                        tool(
                            input_schema=base.tools["get_disk_info"].input_schema,
                            output_schema={
                                "type": "object",
                                "properties": {"x": {}},
                            },
                        )
                    ]
                ),
                "Output schema mismatch",
            ),
        ]
        for client, message in cases:
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(MCPProtocolError, message),
            ):
                verify_server(cast(MCPStdioClient, client), base)


if __name__ == "__main__":
    unittest.main()
