from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from kosika.manifest import load_manifest
from kosika.mcp_stdio import MCPProtocolError, MCPStdioClient, MCPToolError
from kosika.parser import parse
from kosika.runtime import run

ROOT = Path(__file__).parents[1]
SERVER = ROOT / "examples" / "mcp_host_server.py"
MANIFEST = ROOT / "examples" / "mcp_manifest.json"
LIVE_EXAMPLE = ROOT / "examples" / "test_host_live.kos"


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

    def test_mcp_validates_protocol_messages_and_requests(self) -> None:
        with self.assertRaisesRegex(ValueError, "command cannot be empty"):
            MCPStdioClient([])

        client = MCPStdioClient(["unused"], timeout=0.01)
        with self.assertRaisesRegex(RuntimeError, "not started"):
            client.notify("notifications/initialized")
        with self.assertRaisesRegex(RuntimeError, "not started"):
            client.request("ping")

        with patch.object(client, "_send"):
            client._messages.put({"id": 99, "result": {}})
            client._messages.put({"id": 2, "error": {"code": -1}})
            with self.assertRaisesRegex(MCPProtocolError, "failed"):
                client.request("broken")

            with self.assertRaises(TimeoutError):
                client.request("timeout")

    def test_mcp_tool_shapes_and_schema_fail_closed(self) -> None:
        client = MCPStdioClient(["unused"])
        with patch.object(
            client,
            "request",
            return_value={"result": {"tools": [{"name": "inspect"}]}},
        ):
            self.assertEqual(client.require_tool("inspect").description, "")
        with patch.object(
            client,
            "request",
            return_value={"result": {"tools": "invalid"}},
        ):
            with self.assertRaisesRegex(MCPProtocolError, "tools list"):
                client.list_tools()
        with patch.object(
            client,
            "request",
            return_value={"result": {"tools": ["invalid"]}},
        ):
            with self.assertRaisesRegex(MCPProtocolError, "invalid tool definition"):
                client.list_tools()

        schema = {
            "type": "object",
            "properties": {"value": {"type": "number"}},
            "required": ["value"],
            "additionalProperties": False,
        }
        tools_response = {"result": {"tools": [{"name": "inspect"}]}}
        with patch.object(
            client,
            "request",
            side_effect=[
                tools_response,
                {"result": {"structuredContent": {"value": 2}}},
            ],
        ) as request:
            self.assertEqual(
                client.call_tool(
                    "inspect", {"value": 2}, input_schema=schema, output_schema=schema
                ),
                {"value": 2},
            )
            self.assertEqual(request.call_count, 2)

        with patch.object(
            client,
            "request",
            side_effect=[
                tools_response,
                {"result": {"isError": True, "content": ["no"]}},
            ],
        ):
            with self.assertRaisesRegex(MCPToolError, "returned an error"):
                client.call_tool("inspect", {"value": 2})

        with patch.object(client, "request", return_value=tools_response):
            with self.assertRaisesRegex(MCPProtocolError, "Invalid arguments"):
                client.call_tool("inspect", {"value": "bad"}, input_schema=schema)

        invalid_result = {"result": {"structuredContent": {"value": "bad"}}}
        with patch.object(
            client, "request", side_effect=[tools_response, invalid_result]
        ):
            with self.assertRaisesRegex(MCPProtocolError, "Invalid result"):
                client.call_tool("inspect", {"value": 2}, output_schema=schema)

    def test_mcp_content_and_reader_failures_are_bounded(self) -> None:
        client = MCPStdioClient(["unused"])
        tools_response = {"result": {"tools": [{"name": "inspect"}]}}
        for content, expected in (
            ([{"type": "text", "text": '{"value": 3}'}], {"value": 3}),
            ([{"type": "text", "text": "plain text"}], "plain text"),
            ([{"type": "image", "data": "x"}], [{"type": "image", "data": "x"}]),
        ):
            with (
                self.subTest(content=content),
                patch.object(
                    client,
                    "request",
                    side_effect=[tools_response, {"result": {"content": content}}],
                ),
            ):
                self.assertEqual(client.call_tool("inspect", {}), expected)

        with patch.object(client, "_send"):
            client._messages.put(RuntimeError("reader failed"))
            with self.assertRaisesRegex(RuntimeError, "reader failed"):
                client.request("bad-message")

        with patch.object(client, "_send"):
            client._next_id = 1
            client._messages.put({"id": 1, "result": {}})
            self.assertEqual(client.request("ok"), {"id": 1, "result": {}})

    def test_mcp_reader_reports_stdout_closure_and_ignores_stderr(self) -> None:
        client = MCPStdioClient(["unused"])
        stdout = io.StringIO("\nnot-json\n[]\n")
        stderr = io.StringIO("diagnostic only\n")
        client._start_reader(stdout, "stdout")
        client._start_reader(stderr, "stderr")
        client._reader_threads[0].join(timeout=1)
        client._reader_threads[1].join(timeout=1)

        first = client._messages.get(timeout=1)
        second = client._messages.get(timeout=1)
        third = client._messages.get(timeout=1)
        self.assertIsInstance(first, MCPProtocolError)
        self.assertIn("Invalid JSON", str(first))
        self.assertIsInstance(second, MCPProtocolError)
        self.assertIn("JSON object", str(second))
        self.assertIsInstance(third, MCPProtocolError)
        self.assertIn("closed stdout", str(third))
        self.assertTrue(client._messages.empty())

    def test_mcp_reader_converts_stream_failures_to_protocol_errors(self) -> None:
        class FailingStream:
            def __iter__(self):
                raise OSError("stream broke")

        client = MCPStdioClient(["unused"])
        client._start_reader(FailingStream(), "stdout")
        client._reader_threads[0].join(timeout=1)
        failure = client._messages.get(timeout=1)
        self.assertIsInstance(failure, OSError)
        self.assertEqual(str(failure), "stream broke")

    def test_mcp_close_is_idempotent_and_escalates_termination(self) -> None:
        client = MCPStdioClient(["unused"])
        self.assertIsNone(client.close())

        process = MagicMock()
        process.stdin = MagicMock()
        process.stdout = MagicMock()
        process.stderr = MagicMock()
        process.wait.side_effect = [
            subprocess.TimeoutExpired("unused", 2),
            subprocess.TimeoutExpired("unused", 2),
            None,
        ]
        client.process = process
        client.close()
        self.assertIsNone(client.process)
        process.stdin.close.assert_called_once_with()
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_count, 3)
        process.stdout.close.assert_called_once_with()
        process.stderr.close.assert_called_once_with()
        client.close()
        self.assertEqual(process.wait.call_count, 3)

    def test_mcp_close_terminates_once_after_first_wait_timeout(self) -> None:
        client = MCPStdioClient(["unused"])
        process = MagicMock()
        process.stdin = None
        process.stdout = None
        process.stderr = None
        process.wait.side_effect = [subprocess.TimeoutExpired("unused", 2), None]
        client.process = process
        client.close()
        process.terminate.assert_called_once_with()
        process.kill.assert_not_called()
        self.assertEqual(process.wait.call_count, 2)

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
