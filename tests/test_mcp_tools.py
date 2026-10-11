"""MCP servers as tools: granted by name, listed, called, and gated."""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from kosika.app import App
from kosika.mcp_tools import (
    MCPGrantError,
    MCPHub,
    bind_mcp_tools,
    declaration,
    parse_grant,
    shape,
)
from kosika.model import ModelResponse, ModelToolCall, ScriptedModelProvider
from kosika.modules import load_program
from kosika.parser import parse

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "mcp"
SERVER = EXAMPLES / "notes_server.py"
LIBRARIAN = (EXAMPLES / "librarian.kos").read_text(encoding="utf-8")
TEXT = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}


def picks(name: str, arguments: dict[str, Any]) -> ModelResponse:
    return ModelResponse(
        content=None,
        tool_calls=(ModelToolCall(f"{name}-{json.dumps(arguments)}", name, arguments),),
        finish_reason="tool_calls",
    )


def says(text: str) -> ModelResponse:
    return ModelResponse(content=json.dumps({"text": text}))


class GrantTests(unittest.TestCase):
    def test_a_grant_is_a_name_and_a_command(self) -> None:
        name, words = parse_grant('notes=python "my server.py" notes.json')
        self.assertEqual(name, "notes")
        self.assertEqual(words, ["python", "my server.py", "notes.json"])

    def test_a_grant_that_is_not_one_is_refused(self) -> None:
        for bad in (
            "notes",
            "=python x",
            "a b=python x",
            "notes=",
            "system=x",
            "memory=x",
        ):
            with self.subTest(grant=bad), self.assertRaises(MCPGrantError):
                parse_grant(bad)

    def test_an_answer_is_put_into_the_declared_record(self) -> None:
        self.assertEqual(shape("hello", TEXT, "t"), {"text": "hello"})
        self.assertEqual(shape({"text": "as is"}, TEXT, "t"), {"text": "as is"})
        blocks = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
        self.assertEqual(shape(blocks, TEXT, "t"), {"text": "a\nb"})
        self.assertEqual(shape({"n": 1}, TEXT, "t"), {"text": '{"n": 1}'})
        self.assertTrue(shape("x" * 30000, TEXT, "t")["text"].endswith("[cut]"))
        two = {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
            "required": ["a", "b"],
        }
        with self.assertRaises(MCPGrantError):
            shape("hello", two, "t")


class ServerTests(unittest.TestCase):
    """Against a real MCP server process, the example notes server."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.folder = Path(self._dir.name)
        self.notes = self.folder / "notes.json"
        self.notes.write_text(
            json.dumps([{"text": "Dentist on Friday at 10:30", "tag": "health"}]),
            "utf-8",
        )
        self.command = [sys.executable, str(SERVER), str(self.notes)]
        self.hub = MCPHub({"notes": self.command})
        self.addCleanup(self.hub.close)

    def test_a_server_says_what_it_offers_and_what_only_reads(self) -> None:
        offered = {tool["name"]: tool["read_only"] for tool in self.hub.tools("notes")}
        self.assertEqual(
            offered, {"search_notes": True, "list_notes": True, "add_note": False}
        )
        with self.assertRaises(MCPGrantError):
            self.hub.tools("elsewhere")
        with self.assertRaises(MCPGrantError):
            self.hub.tool("notes", "delete_everything")

    def test_a_tool_is_called_and_absent_fields_are_left_out(self) -> None:
        found = self.hub.call("notes", "search_notes", {"query": "dentist"})
        self.assertEqual(found, "1. Dentist on Friday at 10:30 [health]")
        saved = self.hub.call("notes", "add_note", {"text": "Buy milk", "tag": None})
        self.assertEqual(saved, "Saved as note 2.")
        self.assertEqual(
            json.loads(self.notes.read_text("utf-8"))[1], {"text": "Buy milk"}
        )

    def test_the_text_to_paste_is_a_valid_declaration(self) -> None:
        texts = [declaration("notes", tool) for tool in self.hub.tools("notes")]
        program = parse("\n\n".join(texts))
        declared = {tool.name: (tool.source, tool.access) for tool in program.tools}
        self.assertEqual(
            declared,
            {
                "search_notes": ("notes", "observe"),
                "list_notes": ("notes", "observe"),
                "add_note": ("notes", "execute"),
            },
        )
        self.assertIn("record AddNoteIn { text: Text tag: Text? }", texts[2])

    def test_fields_kosika_cannot_express_are_left_out_and_named(self) -> None:
        odd = {
            "name": "find-things",
            "read_only": True,
            "input_schema": {
                "type": "object",
                "properties": {
                    "q": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "where": {"type": "object"},
                },
                "required": ["q"],
            },
        }
        text = declaration("s", odd)
        self.assertIn("// Left out, because Kosika has no type for them: where", text)
        self.assertIn("record FindThingsIn { q: Text tags: Text[]? }", text)
        self.assertIn("tool find_things {", text)
        parse(text)

    def test_a_tool_that_changes_things_cannot_be_declared_as_only_looking(
        self,
    ) -> None:
        source = LIBRARIAN.replace(
            'operation: "add_note"\n  access: execute',
            'operation: "add_note"\n  access: observe',
        ).replace("  require approval\n", "")
        (self.folder / "main.kos").write_text(source, encoding="utf-8")
        program = load_program(self.folder / "main.kos")
        tools = bind_mcp_tools(program, self.hub)
        add = next(tool for tool in program.tools if tool.name == "add_note")
        with self.assertRaisesRegex(MCPGrantError, "Declare it access: execute"):
            tools["add_note"](add, {"text": "x", "tag": None})
        self.assertEqual(len(json.loads(self.notes.read_text("utf-8"))), 1)

    def test_a_server_that_cannot_start_says_so(self) -> None:
        hub = MCPHub({"gone": ["kosika-no-such-program-anywhere"]})
        with self.assertRaisesRegex(MCPGrantError, "could not be started"):
            hub.tools("gone")


class StudioTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name) / "agents"
        (self.root / "librarian").mkdir(parents=True)
        (self.root / "librarian" / "main.kos").write_text(LIBRARIAN, encoding="utf-8")
        self.notes = Path(self._dir.name) / "notes.json"
        self.notes.write_text(json.dumps([{"text": "Dentist on Friday"}]), "utf-8")

    def app(self, responses: list[ModelResponse], granted: bool = True) -> App:
        provider = ScriptedModelProvider(responses)
        app = App(
            self.root,
            bind_models=lambda program: {"thinker": provider},
            mcp={"notes": [sys.executable, str(SERVER), str(self.notes)]}
            if granted
            else None,
        )
        self.addCleanup(app.close)
        self.provider = provider
        return app

    def until(self, app: App, run_id: str, *states: str) -> dict[str, Any]:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            view = app.poll(run_id, 0)
            if view["state"] in states:
                return view
            time.sleep(0.01)
        self.fail(f"the run never reached {states}")

    def test_studio_lists_the_granted_servers_and_their_tools(self) -> None:
        app = self.app([])
        self.assertEqual(app.overview()["mcp"], ["notes"])
        (server,) = app.mcp_tools()["servers"]
        self.assertEqual([tool["name"] for tool in server["tools"]][0], "search_notes")
        self.assertIn('source mcp "notes"', server["tools"][0]["declaration"])
        self.assertEqual(self.app([], granted=False).mcp_tools(), {"servers": []})

    def test_an_agent_chooses_a_server_tool_and_is_told_its_own_words(self) -> None:
        app = self.app(
            [picks("search_notes", {"query": "dentist"}), says("It is on Friday.")]
        )
        job = app.start("librarian/main.kos", {"text": "when is the dentist?"})
        done = self.until(app, job.id, "done", "failed", "waiting")
        self.assertEqual(
            (done["state"], done["text"]),
            ("done", "It is on Friday."),
            done.get("error"),
        )
        described = {
            tool.name: tool.description for tool in self.provider.requests[0].tools
        }
        self.assertIn(
            "Find notes that contain all the words", described["search_notes"]
        )
        self.assertIn("The person is asked before it happens.", described["add_note"])
        self.assertIn(
            "1. Dentist on Friday", self.provider.requests[1].messages[-1].content
        )

    def test_a_server_tool_that_changes_things_waits_for_a_yes(self) -> None:
        add = picks("add_note", {"text": "Car service on the 14th", "tag": None})
        # An agent that can change things shows its plan first, so the model
        # is asked once and the change is the one step of that plan.
        app = self.app([add, says("Saved.")])
        job = app.start("librarian/main.kos", {"text": "remember the car service"})
        view = self.until(app, job.id, "waiting", "failed")
        self.assertEqual(view["state"], "waiting", view.get("error"))
        self.assertEqual(view["request"]["tool"], "plan")
        self.assertEqual(
            [(step["tool"], step["kind"]) for step in view["request"]["plan"]],
            [("add_note", "notes")],
        )
        self.assertEqual(len(json.loads(self.notes.read_text("utf-8"))), 1)
        app.decide(job.id, True)
        done = self.until(app, job.id, "done", "failed", "refused")
        self.assertEqual(done["state"], "done", done.get("error"))
        self.assertEqual(
            json.loads(self.notes.read_text("utf-8"))[1],
            {"text": "Car service on the 14th"},
        )

    def test_without_the_grant_the_server_is_never_reached(self) -> None:
        app = self.app([picks("search_notes", {"query": "dentist"})], granted=False)
        job = app.start("librarian/main.kos", {"text": "when is the dentist?"})
        view = self.until(app, job.id, "failed", "done", "waiting")
        self.assertEqual(view["state"], "failed")
        self.assertIn("no authorized host binding", view["error"])
        self.assertEqual(self.provider.requests, [])


if __name__ == "__main__":
    unittest.main()
