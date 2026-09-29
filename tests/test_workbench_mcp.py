import copy
import unittest
from unittest.mock import patch

from kosika.ast import ToolDecl
from kosika.mcp_stdio import MCPTool
from kosika.workbench import dispatch
from kosika.workbench_mcp import DEMO_SAMPLE, INPUT, OUTPUT, DemoMCP


def payload():
    return {
        key: copy.deepcopy(DEMO_SAMPLE[key])
        for key in ("files", "entry", "workflow", "inputs", "fixtures")
    }


class WorkbenchMCPTests(unittest.TestCase):
    def test_real_stdio_discovery_run_and_offline_replay(self):
        mcp = DemoMCP()
        result = dispatch("inspect-mcp", {}, mcp=mcp)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["transport"], "stdio")
        data = payload()
        checked = dispatch(
            "check", {key: data[key] for key in ("files", "entry", "workflow")}, mcp=mcp
        )
        self.assertEqual(checked["summary"]["tools"], ["inspect_text"])
        run = dispatch("run", data, mcp=mcp)
        self.assertEqual(
            run["result"],
            {"words": 4, "characters": len(data["inputs"]["document"]["text"])},
        )
        with patch(
            "kosika.workbench_mcp.MCPStdioClient",
            side_effect=AssertionError("Replay started a process"),
        ):
            self.assertEqual(
                dispatch("replay", {"trace": run["trace"]})["result"], run["result"]
            )

    def test_grants_schema_and_browser_command_injection_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "host"):
            dispatch("inspect-mcp", {})
        with self.assertRaises(ValueError):
            dispatch("inspect-mcp", {"command": ["anything"]}, mcp=DemoMCP())
        with self.assertRaises(ValueError):
            dispatch("run", payload())
        data = payload()
        data["files"]["main.kos"] = data["files"]["main.kos"].replace(
            "words: Number", "words: Text"
        )
        with self.assertRaises(ValueError):
            dispatch("run", data, mcp=DemoMCP())
        with self.assertRaises(PermissionError):
            DemoMCP().execute(
                ToolDecl(
                    "bad", "workbench_demo", "inspect_text", "execute", "Statistics"
                ),
                {},
            )
        data = payload()
        data["inputs"]["document"]["text"] = 42
        with self.assertRaises(ValueError):
            dispatch("run", data, mcp=DemoMCP())

    def test_discovery_strictness_and_cleanup_after_start_failure(self):
        mcp = DemoMCP()
        with patch("kosika.workbench_mcp.MCPStdioClient") as constructor:
            client = constructor.return_value
            client.start.side_effect = RuntimeError("handshake failed")
            with self.assertRaisesRegex(RuntimeError, "handshake failed"):
                mcp.inspect()
            client.close.assert_called_once()
        for tools in (
            [MCPTool("inspect_text", "", INPUT, {})],
            [MCPTool("inspect_text", "", INPUT, OUTPUT)] * 2,
        ):
            with (
                patch("kosika.workbench_mcp.MCPStdioClient") as constructor,
                patch("kosika.workbench_mcp.verify_server"),
            ):
                client = constructor.return_value
                client.list_tools.return_value = tools
                with self.assertRaises(ValueError):
                    mcp.inspect()
                client.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
