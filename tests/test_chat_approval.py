"""Acting from a chat: one approval for one action, and never without it."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika.chat import chat_loop
from kosika.checker import CheckError, check
from kosika.parser import parse
from kosika.runtime import run
from kosika.system_tools import SystemToolError, bind_system_tools
from kosika.testing import run_tests

SOURCE = """
record Msg { text: Text }
record Said { text: Text }
record NewFile { path: Text text: Text }
record Written { bytes: Number }

tool create_file {
  source system "files"
  operation: "create"
  access: execute
  input: NewFile
  output: Written
}

agent scribe {
  can: [create_file]
  max_steps: 1
}

fn kind_of(msg: Msg) -> Said {
  return {text: if msg.text.starts_with("write ") { "write" } else { "talk" }}
}

fn file_of(msg: Msg) -> NewFile {
  let rest = msg.text.slice(6, msg.text.length())
  let name = rest.split(":").first().or("")
  return {path: name.trim(), text: rest.slice(name.length() + 1, rest.length()).trim()}
}

fn wrote(file: NewFile, done: Written) -> Said {
  return {text: "created " + file.path}
}

fn echo(msg: Msg) -> Said {
  return {text: "you said " + msg.text}
}

workflow create_it(msg: Msg) -> Said {
  file = file_of(msg)
  done = execute scribe.create_file(file.path, file.text)
  said = wrote(file, done)
  require approval
}

workflow main(msg: Msg) -> Said {
  kind = kind_of(msg)
  said = match kind.text {
    case "write" { call create_it(msg) }
    case _ { echo(msg) }
  }
  require approval
}

test "talking needs no approval" {
  a = main({text: "hello"})
  expect a.text == "you said hello"
}

test "writing cannot happen in a test" {
  a = main({text: "write a.txt: x"})
  expect a.text == "created a.txt"
}
"""


def checked(source: str = SOURCE):
    program = parse(source)
    check(program)
    return program


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.outside = Path(self._dir.name)
        self.folder = self.outside / "granted"
        self.folder.mkdir()
        (self.folder / "old.txt").write_text("keep me", encoding="utf-8")
        self.asked: list[dict[str, Any]] = []

    def talk(self, lines: list[str], answers: list[bool] | None) -> list[str]:
        program = checked()
        pending = iter(lines)
        replies = iter(answers or [])
        said: list[str] = []

        def approve(request: dict[str, Any]) -> bool:
            self.asked.append(request)
            return next(replies)

        chat_loop(
            program,
            "main",
            model_bindings={},
            tool_bindings=bind_system_tools(program, folder=self.folder),
            read=lambda: next(pending, None),
            write=said.append,
            show_time=False,
            approve=approve if answers is not None else None,
            approved_by="tester",
        )
        return said


class ChatApprovalTests(Fixture):
    def test_yes_runs_exactly_the_action_that_was_shown(self) -> None:
        said = self.talk(["write notes/a.txt: buy milk"], [True])
        self.assertEqual(said, ["created notes/a.txt"])
        self.assertEqual(
            (self.folder / "notes" / "a.txt").read_text("utf-8"), "buy milk"
        )
        (request,) = self.asked
        self.assertEqual(request["tool"], "create_file")
        self.assertEqual(
            request["arguments"], {"path": "notes/a.txt", "text": "buy milk"}
        )

    def test_no_changes_nothing_and_the_chat_goes_on(self) -> None:
        said = self.talk(["write a.txt: x", "hello"], [False])
        self.assertEqual(said, ["Refused. Nothing was changed.", "you said hello"])
        self.assertFalse((self.folder / "a.txt").exists())

    def test_each_action_is_asked_about_separately(self) -> None:
        self.talk(["write a.txt: one", "write b.txt: two"], [True, False])
        self.assertEqual(len(self.asked), 2)
        self.assertTrue((self.folder / "a.txt").exists())
        self.assertFalse((self.folder / "b.txt").exists())

    def test_talking_never_asks(self) -> None:
        self.assertEqual(self.talk(["hello"], []), ["you said hello"])
        self.assertEqual(self.asked, [])

    def test_without_an_approver_the_action_fails_and_nothing_changes(self) -> None:
        said = self.talk(["write a.txt: x", "hello"], None)
        self.assertIn("requires a verified approval record", said[0])
        self.assertEqual(said[1], "you said hello")
        self.assertFalse((self.folder / "a.txt").exists())

    def test_an_approved_action_still_cannot_overwrite_or_leave_the_folder(
        self,
    ) -> None:
        said = self.talk(
            ["write old.txt: gone", "write ../escape.txt: out"], [True, True]
        )
        self.assertIn("create never overwrites", said[0])
        self.assertIn("leaves the granted folder", said[1])
        self.assertEqual((self.folder / "old.txt").read_text("utf-8"), "keep me")
        self.assertFalse((self.outside / "escape.txt").exists())


class RuntimeGuardTests(Fixture):
    def test_a_path_that_is_not_taken_needs_no_approval_session(self) -> None:
        program = checked()
        result = run(
            program,
            "main",
            None,
            inputs={"msg": {"text": "hello"}},
            tool_bindings=bind_system_tools(program, folder=self.folder),
        )
        self.assertEqual(result, {"text": "you said hello"})

    def test_a_taken_path_is_refused_at_the_call_without_a_session(self) -> None:
        program = checked()
        with self.assertRaisesRegex(PermissionError, "requires a verified approval"):
            run(
                program,
                "main",
                None,
                inputs={"msg": {"text": "write a.txt: x"}},
                tool_bindings=bind_system_tools(program, folder=self.folder),
            )
        self.assertFalse((self.folder / "a.txt").exists())

    def test_a_workflow_that_always_acts_is_still_refused_up_front(self) -> None:
        program = checked()
        with self.assertRaisesRegex(PermissionError, "requires a verified approval"):
            run(
                program,
                "create_it",
                None,
                inputs={"msg": {"text": "write a.txt: x"}},
                tool_bindings=bind_system_tools(program, folder=self.folder),
            )

    def test_tests_in_the_language_can_never_act(self) -> None:
        results = {r.name: r for r in run_tests(checked())}
        self.assertTrue(results["talking needs no approval"].passed)
        failed = results["writing cannot happen in a test"]
        self.assertFalse(failed.passed)
        self.assertIn("requires a verified approval record", failed.message)

    def test_callers_must_declare_that_they_may_act(self) -> None:
        source = SOURCE.replace(
            "    case _ { echo(msg) }\n  }\n  require approval\n",
            "    case _ { echo(msg) }\n  }\n",
        )
        with self.assertRaisesRegex(CheckError, "must require approval"):
            checked(source)


class CreateTests(Fixture):
    def create(self, path: str, text: str = "x") -> Any:
        program = checked()
        tool = next(t for t in program.tools if t.name == "create_file")
        return bind_system_tools(program, folder=self.folder)["create_file"](
            tool, {"path": path, "text": text}
        )

    def test_create_adds_a_file_and_its_folders(self) -> None:
        self.assertEqual(self.create("a/b/c.txt", "héllo"), {"bytes": 6})
        self.assertEqual(
            (self.folder / "a" / "b" / "c.txt").read_text("utf-8"), "héllo"
        )

    def test_create_never_replaces_what_is_there(self) -> None:
        for path in ("old.txt", "."):
            with self.subTest(path=path), self.assertRaises((SystemToolError, OSError)):
                self.create(path)
        self.assertEqual((self.folder / "old.txt").read_text("utf-8"), "keep me")

    def test_create_must_be_declared_execute(self) -> None:
        with self.assertRaisesRegex(CheckError, "must declare access: execute"):
            checked(SOURCE.replace("access: execute", "access: observe"))


if __name__ == "__main__":
    unittest.main()
