import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from kosika.manifest import ManifestError, load_manifest, schema_hash
from kosika.mcp_stdio import MCPProtocolError, MCPStdioClient
from kosika.parser import parse
from kosika.runtime import run
from kosika.schema import SchemaValidationError, schema_for_record, validate

ROOT = Path(__file__).parents[1]
SERVER = ROOT / "examples" / "mcp_host_server.py"
MANIFEST = ROOT / "examples" / "mcp_manifest.json"
LIVE_EXAMPLE = ROOT / "examples" / "test_host_live.kos"


class ManifestValidationTests(unittest.TestCase):
    def command(self) -> list[str]:
        return [sys.executable, str(SERVER)]

    def test_manifest_hash_is_self_consistent(self) -> None:
        manifest = load_manifest(MANIFEST)
        pinned = manifest.tools["get_disk_info"]
        self.assertEqual(
            pinned.schema_hash, schema_hash(pinned.input_schema, pinned.output_schema)
        )

    def test_record_schema_rejects_unknown_and_missing_fields(self) -> None:
        program = parse(LIVE_EXAMPLE.read_text(encoding="utf-8"))
        schema = schema_for_record(program, "DiskStatus")
        validate({"mount": "/", "used_percent": 42.0}, schema)
        with self.assertRaises(SchemaValidationError):
            validate({"mount": "/"}, schema)
        with self.assertRaises(SchemaValidationError):
            validate({"mount": "/", "used_percent": 42.0, "unexpected": True}, schema)

    def test_manifest_accepts_utf8_bom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bom.json"
            path.write_text(MANIFEST.read_text(encoding="utf-8"), encoding="utf-8-sig")
            self.assertEqual(load_manifest(path), load_manifest(MANIFEST))

    def test_manifest_rejects_tampered_schema_hash(self) -> None:
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        data["tools"]["get_disk_info"]["schemaHash"] = "tampered"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tampered.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ManifestError):
                load_manifest(path)

    def test_cli_generates_and_validates_manifest_without_tool_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            generated = Path(directory) / "generated.json"
            generated_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "kosika.cli",
                    "manifest",
                    "generate",
                    "--mcp-server",
                    str(SERVER),
                    "--source",
                    "local_stdio",
                    "--output",
                    str(generated),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(generated_result.returncode, 0, generated_result.stderr)
            self.assertEqual(load_manifest(generated), load_manifest(MANIFEST))

            validated_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "kosika.cli",
                    "manifest",
                    "validate",
                    str(generated),
                    "--mcp-server",
                    str(SERVER),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(validated_result.returncode, 0, validated_result.stderr)
            self.assertIn("Valid manifest", validated_result.stdout)

            diff_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "kosika.cli",
                    "manifest",
                    "diff",
                    str(generated),
                    "--mcp-server",
                    str(SERVER),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(diff_result.returncode, 0, diff_result.stderr)
            self.assertIn("No drift", diff_result.stdout)

            json_diff_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "kosika.cli",
                    "manifest",
                    "diff",
                    str(generated),
                    "--mcp-server",
                    str(SERVER),
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(json_diff_result.returncode, 0, json_diff_result.stderr)
            self.assertFalse(json.loads(json_diff_result.stdout)["drift"])

            check_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "kosika.cli",
                    "manifest",
                    "check",
                    str(generated),
                    "--mcp-server",
                    str(SERVER),
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(check_result.returncode, 0, check_result.stderr)
            self.assertFalse(json.loads(check_result.stdout)["drift"])

            drifted_data = json.loads(MANIFEST.read_text(encoding="utf-8"))
            drifted_data["server"]["version"] = "9.9.9"
            drifted = Path(directory) / "drifted.json"
            drifted.write_text(json.dumps(drifted_data), encoding="utf-8")
            drift_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "kosika.cli",
                    "manifest",
                    "diff",
                    str(drifted),
                    "--mcp-server",
                    str(SERVER),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(drift_result.returncode, 0, drift_result.stderr)
            self.assertIn("MCP manifest drift detected", drift_result.stdout)
            self.assertIn("server version", drift_result.stdout)

            check_drift_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "kosika.cli",
                    "manifest",
                    "check",
                    str(drifted),
                    "--mcp-server",
                    str(SERVER),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(check_drift_result.returncode, 1)
            self.assertIn("MCP manifest drift detected", check_drift_result.stdout)

    def test_server_identity_is_pinned_before_call(self) -> None:
        program = parse(LIVE_EXAMPLE.read_text(encoding="utf-8"))
        manifest = replace(load_manifest(MANIFEST), server_version="9.9.9")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(MCPProtocolError):
                run(
                    program,
                    "main",
                    Path(directory) / "trace.jsonl",
                    mcp_commands={"local_stdio": self.command()},
                    manifest=manifest,
                )

    def test_client_validates_input_before_call_and_output_after_call(self) -> None:
        with MCPStdioClient(self.command()) as client:
            with self.assertRaises(MCPProtocolError):
                client.call_tool(
                    "get_disk_info",
                    {},
                    input_schema={
                        "type": "object",
                        "properties": {"required_value": {"type": "string"}},
                        "required": ["required_value"],
                        "additionalProperties": False,
                    },
                )
            with self.assertRaises(MCPProtocolError):
                client.call_tool(
                    "get_disk_info",
                    {},
                    output_schema={
                        "type": "object",
                        "properties": {"missing_from_fixture": {"type": "string"}},
                        "required": ["missing_from_fixture"],
                        "additionalProperties": False,
                    },
                )


if __name__ == "__main__":
    unittest.main()
