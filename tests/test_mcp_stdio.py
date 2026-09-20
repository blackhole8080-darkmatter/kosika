from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from kosika.manifest import load_manifest
from kosika.mcp_stdio import MCPProtocolError, MCPStdioClient
from kosika.parser import parse
from kosika.runtime import run

ROOT = Path(__file__).parents[1]
SERVER = ROOT / "examples" / "mcp_host_server.py"
MANIFEST = ROOT / "examples" / "mcp_manifest.json"
LIVE_EXAMPLE = ROOT / "examples" / "test_host_live.syn"


class MCPStdioTests(unittest.TestCase):
    def command(self) -> list[str]:
        return [sys.executable, str(SERVER)]

    def test_initialize_discover_and_call(self) -> None:
        with MCPStdioClient(self.command()) as client:
            self.assertEqual(client.server_info["name"], "kosika-test-mcp")
            tools = client.list_tools()
            self.assertEqual([tool.name for tool in tools], ["get_disk_info"])
            result = client.call_tool("get_disk_info", {})
            self.assertEqual(result, {"mount": "/", "used_percent": 42.0})

    def test_unknown_declared_tool_is_rejected(self) -> None:
        with MCPStdioClient(self.command()) as client:
            with self.assertRaises(MCPProtocolError):
                client.require_tool("does_not_exist")

    def test_runtime_executes_real_stdio_server(self) -> None:
        program = parse(LIVE_EXAMPLE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "live.jsonl"
            result = run(
                program,
                "main",
                trace,
                mcp_commands={"local_stdio": self.command()},
                manifest=load_manifest(MANIFEST),
            )
            self.assertEqual(result, {"mount": "/", "used_percent": 42.0})
            self.assertIn('"transport": "stdio"', trace.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
