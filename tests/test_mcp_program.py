"""A Kosika program's workflows served as MCP tools (kosika serve-mcp)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from kosika import ask_folder
from kosika.mcp_program import ProgramServer, ProgramServerError

ROOT = Path(__file__).resolve().parent.parent

NOTES = """// Notes kept in a folder, for any app that asks.

record Note { name: Text text: Text }
record NewFile { path: Text text: Text }
record Wrote { bytes: Number }
record Saved { saved: Bool name: Text }
record Look { name: Text }

tool write_note {
  source system "files"
  operation: "write"
  access: execute
  input: NewFile
  output: Wrote
}

agent scribe {
  can: [write_note]
  max_steps: 1
}

fn shout(text: Text) -> Text { return text.upper() }

workflow keep(n: Note) -> Saved {
  wrote = execute scribe.write_note(path: n.name + ".txt", text: n.text)
  result = {saved: wrote.bytes > 0, name: n.name}
  require approval
}

workflow loud(l: Look) -> Look {
  result = {name: shout(l.name)}
}
"""


class ProgramServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.source = self.tmp / "notes.kos"
        self.source.write_text(NOTES, encoding="utf-8")
        (self.tmp / "kept").mkdir()

    def test_each_workflow_is_a_tool_with_its_own_schema(self) -> None:
        server = ProgramServer(self.source, [])
        tools = {t["name"]: t for t in server.tools()}
        self.assertEqual(sorted(tools), ["keep", "loud"])
        schema = tools["keep"]["inputSchema"]
        self.assertEqual(schema["required"], ["n"])
        self.assertEqual(schema["properties"]["n"]["properties"]["text"], {"type": "string"})
        self.assertIn("waits for the person's yes", tools["keep"]["description"])
        self.assertIn("Notes kept in a folder", tools["keep"]["description"])
        self.assertNotIn("waits", tools["loud"]["description"])
        only = ProgramServer(self.source, [], only=["loud"])
        self.assertEqual([t["name"] for t in only.tools()], ["loud"])

    def test_the_terminal_yes_and_per_call_flags_are_refused(self) -> None:
        with self.assertRaisesRegex(ProgramServerError, "--approve-in"):
            ProgramServer(self.source, ["--approve"])
        with self.assertRaisesRegex(ProgramServerError, "chosen by each call"):
            ProgramServer(self.source, ["--workflow", "keep"])
        with self.assertRaisesRegex(ProgramServerError, "no workflow nope"):
            ProgramServer(self.source, [], only=["nope"])

    def test_a_call_gives_the_input_and_nothing_else(self) -> None:
        server = ProgramServer(self.source, [])
        with self.assertRaisesRegex(TypeError, "missing l"):
            server.call("loud", {})
        with self.assertRaisesRegex(TypeError, "not extra"):
            server.call("loud", {"l": {"name": "x"}, "extra": 1})
        with self.assertRaises(LookupError):
            server.call("nope", {})

    def test_a_pure_workflow_answers_at_once(self) -> None:
        answer = ProgramServer(self.source, []).call("loud", {"l": {"name": "nova"}})
        self.assertFalse(answer["isError"], answer)
        self.assertEqual(answer["structuredContent"], {"name": "NOVA"})

    def test_an_action_waits_for_a_yes_in_the_ask_folder(self) -> None:
        asks = self.tmp / "asks"
        server = ProgramServer(self.source, ["--allow-folder", str(self.tmp / "kept"), "--approve-in", str(asks), "--approve-wait", "30"])
        stop = threading.Event()

        def say_yes() -> None:
            while not stop.is_set():
                if asks.exists() and ask_folder.waiting(asks):
                    ask_folder.answer(asks, None, "yes", "aryan")
                time.sleep(0.2)

        helper = threading.Thread(target=say_yes, daemon=True)
        helper.start()
        try:
            answer = server.call("keep", {"n": {"name": "dentist", "text": "Friday at 9"}})
        finally:
            stop.set()
        self.assertFalse(answer["isError"], answer)
        self.assertEqual(answer["structuredContent"], {"saved": True, "name": "dentist"})
        self.assertEqual((self.tmp / "kept" / "dentist.txt").read_text(encoding="utf-8"), "Friday at 9")

    def test_a_no_does_nothing_and_says_so(self) -> None:
        asks = self.tmp / "asks"
        server = ProgramServer(self.source, ["--allow-folder", str(self.tmp / "kept"), "--approve-in", str(asks), "--approve-wait", "30"])
        stop = threading.Event()

        def say_no() -> None:
            while not stop.is_set():
                if asks.exists() and ask_folder.waiting(asks):
                    ask_folder.answer(asks, None, "no", "aryan")
                time.sleep(0.2)

        threading.Thread(target=say_no, daemon=True).start()
        try:
            answer = server.call("keep", {"n": {"name": "dentist", "text": "Friday at 9"}})
        finally:
            stop.set()
        self.assertTrue(answer["isError"])
        self.assertFalse((self.tmp / "kept" / "dentist.txt").exists())

    def test_an_mcp_client_talks_to_it_over_stdio(self) -> None:
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        process = subprocess.Popen(
            [sys.executable, "-m", "kosika.cli", "serve-mcp", str(self.source)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env,
        )
        try:
            def ask(message: dict) -> dict:
                process.stdin.write(json.dumps(message) + "\n")
                process.stdin.flush()
                return json.loads(process.stdout.readline())

            hello = ask({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            self.assertEqual(hello["result"]["serverInfo"]["name"], "kosika-notes")
            listed = ask({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            self.assertEqual(sorted(t["name"] for t in listed["result"]["tools"]), ["keep", "loud"])
            called = ask({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "loud", "arguments": {"l": {"name": "comet"}}}})
            self.assertEqual(called["result"]["structuredContent"], {"name": "COMET"})
        finally:
            process.stdin.close()
            process.wait(timeout=10)

    def test_the_cli_refuses_the_terminal_yes(self) -> None:
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        done = subprocess.run(
            [sys.executable, "-m", "kosika.cli", "serve-mcp", str(self.source), "--", "--approve"],
            capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        )
        self.assertEqual(done.returncode, 2)
        self.assertIn("--approve-in", done.stderr)


if __name__ == "__main__":
    unittest.main()
