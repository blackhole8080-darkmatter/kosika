"""Studio-oriented example MCP servers stay bounded and approval-aware."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from kosika.mcp_stdio import MCPToolError
from kosika.mcp_tools import MCPHub
from kosika.parser import parse

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "mcp"
DESKTOP_SERVER = EXAMPLES / "desktop_server.py"
TASKS_SERVER = EXAMPLES / "tasks_server.py"


def load_desktop_module():
    spec = importlib.util.spec_from_file_location("desktop_server", DESKTOP_SERVER)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load desktop example server")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StudioMCPExampleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.tasks_file = self.folder / "tasks.json"
        self.hub = MCPHub(
            {
                "desktop": [sys.executable, str(DESKTOP_SERVER)],
                "tasks": [sys.executable, str(TASKS_SERVER), str(self.tasks_file)],
            }
        )
        self.addCleanup(self.hub.close)

    def test_only_app_launch_is_an_execute_tool(self) -> None:
        desktop = {tool["name"]: tool["read_only"] for tool in self.hub.tools("desktop")}
        tasks = {tool["name"]: tool["read_only"] for tool in self.hub.tools("tasks")}
        self.assertEqual(desktop, {"list_apps": True, "open_app": False})
        self.assertEqual(
            tasks,
            {
                "list_tasks": True,
                "search_tasks": True,
                "add_task": False,
                "complete_task": False,
            },
        )

    def test_tasks_can_be_added_searched_and_completed(self) -> None:
        added = self.hub.call(
            "tasks",
            "add_task",
            {"title": "Book dentist", "due": "2030-02-03"},
        )
        task_id = added.split()[2].rstrip(":")
        found = self.hub.call("tasks", "search_tasks", {"query": "book dentist"})
        self.assertIn(task_id, found)
        self.assertIn("due 2030-02-03", found)
        self.assertEqual(
            self.hub.call("tasks", "complete_task", {"task_id": task_id}),
            f"Completed task {task_id}.",
        )
        saved = json.loads(self.tasks_file.read_text(encoding="utf-8"))
        self.assertTrue(saved[0]["completed"])

    def test_desktop_server_rejects_arbitrary_app_names(self) -> None:
        self.assertTrue(self.hub.call("desktop", "list_apps", {}).startswith("Supported apps:"))
        with self.assertRaises(MCPToolError):
            self.hub.call("desktop", "open_app", {"app": "powershell"})

    def test_open_app_uses_a_fixed_command_without_a_shell(self) -> None:
        desktop = load_desktop_module()
        popen = Mock()
        result = desktop.call(
            "open_app",
            {"app": "notepad"},
            system="Windows",
            which=lambda _command: "C:\\Windows\\System32\\notepad.exe",
            popen=popen,
        )
        self.assertEqual(result, "Opened notepad.")
        popen.assert_called_once_with(["notepad.exe"], shell=False)

    def test_studio_example_declares_mutations_as_execute(self) -> None:
        program = parse((EXAMPLES / "studio_helper.kos").read_text(encoding="utf-8"))
        declared = {
            tool.name: tool.access
            for tool in program.tools
        }
        self.assertEqual(declared["open_app"], "execute")
        self.assertEqual(declared["add_task"], "execute")
        self.assertEqual(declared["complete_task"], "execute")
        self.assertEqual(declared["search_tasks"], "observe")


if __name__ == "__main__":
    unittest.main()
