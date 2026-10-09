"""`read file`, `list files`, `write file ... with ...` and `fetch`."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from kosika.app import App
from kosika.ast import program_payload
from kosika.checker import CheckError
from kosika.modules import load_program
from kosika.parser import parse
from kosika.runtime import run
from kosika.system_tools import bind_system_tools
from kosika.testing import run_tests
from kosika.trace_replay import replay_trace

SOURCE = """
record Msg { text: Text }
record Out { text: Text }

fn build(note: FileContent, names: FileNames) -> FileChange {
  return {path: "out/report.txt", text: f"{names.names.length()} files. Note: {note.text.trim()}"}
}

fn done(report: FileChange, saved: FileSaved) -> Out {
  return {text: f"Wrote {saved.bytes} bytes to {report.path}"}
}

workflow main(msg: Msg) -> Out {
  note   = read file msg.text
  names  = list files "."
  report = build(note, names)
  saved  = write file report.path with report.text
  out    = done(report, saved)
  require approval
}

fn shown(note: FileContent) -> Out {
  return {text: note.text.trim()}
}

workflow peek(msg: Msg) -> Out {
  note = read file msg.text
  out  = shown(note)
}

fn title(page: WebPage) -> Out {
  return {text: page.title}
}

workflow look(msg: Msg) -> Out {
  page = fetch msg.text
  out  = title(page)
}

test "a page is scripted like any read-only tool" {
  answers web_fetch: [{url: "https://example.org", title: "Example", text: "hello"}]
  a = look({text: "https://example.org"})
  expect a.text == "Example"
}
"""


class DirectTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name) / "agents"
        self.work = Path(self._dir.name) / "work"
        (self.root / "desk").mkdir(parents=True)
        self.work.mkdir()
        (self.root / "desk" / "main.kos").write_text(SOURCE, encoding="utf-8")
        (self.work / "todo.txt").write_text("Buy milk\n", encoding="utf-8")
        (Path(self._dir.name) / "secret.txt").write_text("hidden", encoding="utf-8")
        self.program = load_program(self.root / "desk" / "main.kos")

    def peek(self, path: str, **extra: Any) -> Any:
        return run(
            self.program,
            "peek",
            Path(self._dir.name) / "run.jsonl",
            inputs={"msg": {"text": path}},
            **extra,
        )

    def test_the_steps_are_ordinary_tools_underneath(self) -> None:
        declared = {
            tool.name: (tool.source, tool.operation, tool.access)
            for tool in self.program.tools
        }
        self.assertEqual(declared["file_read"], ("system:files", "read", "observe"))
        self.assertEqual(declared["file_write"], ("system:files", "write", "execute"))
        self.assertEqual(declared["web_fetch"], ("system:web", "fetch", "observe"))
        self.assertTrue(all(result.passed for result in run_tests(self.program)))

    def test_reading_needs_the_folder_to_be_granted(self) -> None:
        # Not granted: the read is refused before any file is touched.
        with self.assertRaises((PermissionError, RuntimeError)):
            self.peek("todo.txt")
        tools = bind_system_tools(self.program, folder=self.work)
        self.assertEqual(
            self.peek("todo.txt", tool_bindings=tools), {"text": "Buy milk"}
        )
        self.assertEqual(
            replay_trace(Path(self._dir.name) / "run.jsonl"), {"text": "Buy milk"}
        )

    def test_a_path_outside_the_folder_is_refused(self) -> None:
        tools = bind_system_tools(self.program, folder=self.work)
        with self.assertRaisesRegex(ValueError, "leaves the granted folder"):
            self.peek("../secret.txt", tool_bindings=tools)

    def test_writing_waits_for_a_yes_and_shows_what_it_will_write(self) -> None:
        app = App(self.root, bind_models=lambda program: {}, allow_folder=self.work)
        job = app.start("desk/main.kos", {"text": "todo.txt"})
        deadline = time.monotonic() + 30
        view: dict[str, Any] = {}
        while time.monotonic() < deadline:
            view = app.poll(job.id, 0)
            if view["state"] != "running":
                break
            time.sleep(0.01)
        self.assertEqual(view["state"], "waiting", view.get("error"))
        self.assertEqual(
            view["request"]["arguments"],
            {"path": "out/report.txt", "text": "1 files. Note: Buy milk"},
        )
        self.assertFalse((self.work / "out").exists())
        app.decide(job.id, True)
        while time.monotonic() < deadline:
            view = app.poll(job.id, 0)
            if view["state"] in ("done", "failed", "refused"):
                break
            time.sleep(0.01)
        self.assertEqual(view["text"], "Wrote 23 bytes to out/report.txt")
        self.assertEqual(
            (self.work / "out" / "report.txt").read_text("utf-8"),
            "1 files. Note: Buy milk",
        )

    def test_a_workflow_that_writes_must_require_approval(self) -> None:
        with self.assertRaises(CheckError):
            parse(
                SOURCE.replace(
                    "  out    = done(report, saved)\n  require approval\n",
                    "  out    = done(report, saved)\n",
                )
            )
            from kosika.checker import check

            check(
                parse(
                    SOURCE.replace(
                        "  out    = done(report, saved)\n  require approval\n",
                        "  out    = done(report, saved)\n",
                    )
                )
            )

    def test_a_program_without_these_steps_is_unchanged(self) -> None:
        plain = parse("record A { n: Number }\nfn main(a: A) -> A {\n  return a\n}")
        text = json.dumps(program_payload(plain))
        self.assertNotIn("FileContent", text)
        self.assertNotIn("direct", text)

    def test_the_words_are_still_names_elsewhere(self) -> None:
        parse(
            "record A { n: Number }\n"
            "fn pick(fetch: Number, write: Number, list: Number) -> A {\n"
            "  return {n: fetch + write + list}\n}\n"
            "workflow main(a: A) -> A {\n  out = pick(a.n, a.n, a.n)\n}"
        )


if __name__ == "__main__":
    unittest.main()
