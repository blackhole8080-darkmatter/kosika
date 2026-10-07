"""Shell and file tools: declared, granted by the host, approved every time."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from kosika import cli, system_tools
from kosika.checker import CheckError, check
from kosika.parser import parse
from kosika.system_tools import SystemToolError, bind_system_tools

SOURCE = """
record Note { name: Text body: Text }
record FileWrite { path: Text text: Text }
record FileWritten { bytes: Number }
record FileRead { path: Text }
record FileText { text: Text }
record ShellArguments { args: Text[] }
record ShellResult { exit_code: Number stdout: Text stderr: Text }
record Report { saved: Number echoed: Text code: Number }

tool save_note {
  source system "files"
  operation: "write"
  access: execute
  input: FileWrite
  output: FileWritten
}

tool read_note {
  source system "files"
  operation: "read"
  access: execute
  input: FileRead
  output: FileText
}

tool run_python {
  source system "shell"
  operation: "PYTHON"
  access: execute
  input: ShellArguments
  output: ShellResult
}

agent worker { can: [save_note, read_note, run_python] }

fn report(written: FileWritten, back: FileText, ran: ShellResult) -> Report {
  return {saved: written.bytes, echoed: back.text, code: ran.exit_code}
}

workflow main(note: Note) -> Report {
  written = execute worker.save_note(path: note.name, text: note.body)
  back    = execute worker.read_note(path: note.name)
  ran     = execute worker.run_python(args: ["-c", "print(40 + 2)"])
  result  = report(written, back, ran)
  require approval
}
""".replace("PYTHON", Path(sys.executable).name)

NOTE = {"note": {"name": "notes/hello.txt", "body": "hi there"}}


def program(source: str = SOURCE):
    parsed = parse(source)
    check(parsed)
    return parsed


def check_error(old: str, new: str) -> str:
    with contextlib.suppress(CheckError):
        program(SOURCE.replace(old, new))
        raise AssertionError("expected the program to be rejected")
    try:
        program(SOURCE.replace(old, new))
    except CheckError as exc:
        return str(exc)
    return ""


class DeclarationTests(unittest.TestCase):
    def test_system_tools_parse_with_a_system_source(self) -> None:
        sources = {tool.name: tool.source for tool in program().tools}
        self.assertEqual(
            sources,
            {
                "save_note": "system:files",
                "read_note": "system:files",
                "run_python": "system:shell",
            },
        )

    def test_a_system_tool_must_require_approval(self) -> None:
        message = check_error(
            'operation: "write"\n  access: execute',
            'operation: "write"\n  access: observe',
        )
        self.assertIn("must declare access: execute", message)
        self.assertIn(
            "needs approval before execute tool",
            check_error("  require approval\n", ""),
        )

    def test_bad_declarations_are_check_errors(self) -> None:
        cases = {
            (
                'source system "shell"',
                'source system "network"',
            ): "unknown system source",
            ('operation: "write"', 'operation: "delete"'): "unknown files operation",
            (
                f'operation: "{Path(sys.executable).name}"',
                'operation: "rm -rf"',
            ): "exactly one program",
            ("record FileRead { path: Text }", "record FileRead { file: Text }"): (
                "input record must have exactly the fields {path: Text}"
            ),
            (
                "record ShellResult { exit_code: Number stdout: Text stderr: Text }",
                "record ShellResult { exit_code: Number stdout: Text }",
            ): "output record must have exactly the fields",
        }
        for (old, new), message in cases.items():
            with self.subTest(new=new):
                self.assertIn(message, check_error(old, new))


class GrantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name) / "granted"
        self.folder.mkdir()
        self.program = program()

    def tool(self, name: str):
        return next(tool for tool in self.program.tools if tool.name == name)

    def call(self, name: str, **arguments: Any) -> Any:
        bindings = bind_system_tools(self.program, folder=self.folder, allow_shell=True)
        return bindings[name](self.tool(name), arguments)

    def test_nothing_is_bound_without_a_grant(self) -> None:
        self.assertEqual(bind_system_tools(self.program, folder=None), {})
        files_only = bind_system_tools(self.program, folder=self.folder)
        self.assertEqual(sorted(files_only), ["read_note", "save_note"])
        with self.assertRaisesRegex(SystemToolError, "needs a granted folder"):
            bind_system_tools(self.program, folder=None, allow_shell=True)
        with self.assertRaisesRegex(SystemToolError, "not an existing folder"):
            bind_system_tools(self.program, folder=self.folder / "missing")

    def test_files_are_written_read_and_listed_inside_the_folder(self) -> None:
        self.assertEqual(
            self.call("save_note", path="a/b.txt", text="héllo"), {"bytes": 6}
        )
        self.assertEqual((self.folder / "a" / "b.txt").read_text("utf-8"), "héllo")
        self.assertEqual(self.call("read_note", path="a/b.txt"), {"text": "héllo"})
        lister = program(
            SOURCE.replace('operation: "read"', 'operation: "list"')
            .replace(
                "record FileText { text: Text }", "record FileText { names: Text[] }"
            )
            .replace("echoed: back.text", 'echoed: back.names.join(separator: ",")')
        )
        bound = bind_system_tools(lister, folder=self.folder)
        tool = next(tool for tool in lister.tools if tool.name == "read_note")
        (self.folder / "top.txt").write_text("x", encoding="utf-8")
        self.assertEqual(
            bound["read_note"](tool, {"path": "."}), {"names": ["a/", "top.txt"]}
        )
        self.assertEqual(bound["read_note"](tool, {"path": "a"}), {"names": ["b.txt"]})

    def test_paths_that_leave_the_folder_are_refused(self) -> None:
        outside = Path(self.directory.name) / "secret.txt"
        outside.write_text("secret", encoding="utf-8")
        for path in ("../secret.txt", "a/../../secret.txt", str(outside), "", "  "):
            with self.subTest(path=path):
                with self.assertRaises(SystemToolError):
                    self.call("read_note", path=path)
                with self.assertRaises(SystemToolError):
                    self.call("save_note", path=path, text="overwritten")
        self.assertEqual(outside.read_text("utf-8"), "secret")

    def test_a_symlink_out_of_the_folder_is_refused(self) -> None:
        outside = Path(self.directory.name) / "elsewhere"
        outside.mkdir()
        link = self.folder / "link"
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("this account cannot create symlinks")
        with self.assertRaisesRegex(SystemToolError, "symlink"):
            self.call("save_note", path="link/x.txt", text="escaped")
        self.assertEqual(list(outside.iterdir()), [])

    def test_file_limits_and_kinds(self) -> None:
        (self.folder / "big.txt").write_bytes(b"x" * (system_tools.MAX_FILE_BYTES + 1))
        (self.folder / "raw.bin").write_bytes(b"\xff\xfe\x00")
        (self.folder / "dir").mkdir()
        cases = {
            "big.txt": "larger than 1 MiB",
            "raw.bin": "not UTF-8",
            "dir": "is not a file",
            "missing.txt": "is not a file",
        }
        for path, message in cases.items():
            with self.subTest(path=path):
                with self.assertRaisesRegex(SystemToolError, message):
                    self.call("read_note", path=path)
        with self.assertRaisesRegex(SystemToolError, "is a folder"):
            self.call("save_note", path="dir", text="x")
        with self.assertRaisesRegex(SystemToolError, "larger than 1 MiB"):
            self.call(
                "save_note", path="x", text="x" * (system_tools.MAX_FILE_BYTES + 1)
            )

    def test_the_shell_tool_runs_only_its_declared_program_in_the_folder(self) -> None:
        result = self.call(
            "run_python", args=["-c", "import os; print(os.path.basename(os.getcwd()))"]
        )
        self.assertEqual(
            (result["exit_code"], result["stdout"].strip()), (0, "granted")
        )
        failed = self.call("run_python", args=["-c", "import sys; sys.exit(3)"])
        self.assertEqual(failed["exit_code"], 3)
        literal = self.call("run_python", args=["-c", "print('a && echo b')"])
        self.assertEqual(literal["stdout"].strip(), "a && echo b")

    def test_long_output_is_cut_and_slow_programs_are_stopped(self) -> None:
        loud = self.call("run_python", args=["-c", "print('x' * 200000)"])
        self.assertTrue(loud["stdout"].endswith("[output cut]"))
        with patch.object(system_tools, "SHELL_TIMEOUT_SECONDS", 1):
            with self.assertRaisesRegex(SystemToolError, "was stopped"):
                self.call("run_python", args=["-c", "import time; time.sleep(30)"])

    def test_a_missing_program_is_reported(self) -> None:
        source = SOURCE.replace(
            f'operation: "{Path(sys.executable).name}"',
            'operation: "kosika-no-such-program"',
        )
        parsed = program(source)
        bound = bind_system_tools(parsed, folder=self.folder, allow_shell=True)
        tool = next(tool for tool in parsed.tools if tool.name == "run_python")
        with self.assertRaisesRegex(SystemToolError, "was not found"):
            bound["run_python"](tool, {"args": []})


class ApproveEachTimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.folder = root / "granted"
        self.folder.mkdir()
        self.source = root / "main.kos"
        self.source.write_text(SOURCE, encoding="utf-8")
        self.inputs = root / "input.json"
        self.inputs.write_text(json.dumps(NOTE), encoding="utf-8")
        self.trace = root / "trace.jsonl"
        self.asked: list[str] = []

    def invoke(
        self, *extra: str, answers: list[str], tty: bool = True
    ) -> tuple[int, str, str]:
        replies = list(answers)

        def ask(prompt: str) -> str:
            self.asked.append(prompt)
            return replies.pop(0)

        out, err = io.StringIO(), io.StringIO()
        argv = [
            "kosika",
            "run",
            str(self.source),
            "--input",
            str(self.inputs),
            "--trace",
            str(self.trace),
            *extra,
        ]
        with (
            patch.object(sys, "argv", argv),
            patch.object(sys.stdin, "isatty", lambda: tty, create=True),
            patch("builtins.input", ask),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = cli.main()
        return code, out.getvalue(), err.getvalue()

    def granted(self) -> tuple[str, ...]:
        return ("--allow-folder", str(self.folder), "--allow-shell", "--approve")

    def test_each_action_is_asked_about_and_then_runs(self) -> None:
        code, out, err = self.invoke(
            *self.granted(), "--approved-by", "aryan", answers=["y", "yes", "Y"]
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.asked), 3)
        self.assertIn("'saved': 8", out)
        self.assertIn("'echoed': 'hi there'", out)
        self.assertEqual(
            (self.folder / "notes" / "hello.txt").read_text("utf-8"), "hi there"
        )
        self.assertIn("tool      : save_note", err)
        self.assertIn('"path": "notes/hello.txt"', err)
        self.assertIn("tool      : run_python", err)

    def test_refusing_the_first_action_runs_nothing(self) -> None:
        code, _, err = self.invoke(*self.granted(), answers=["n"])
        self.assertEqual(code, 3)
        self.assertIn("Refused. Nothing was run.", err)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_refusing_a_later_action_keeps_what_was_approved(self) -> None:
        code, _, _ = self.invoke(*self.granted(), answers=["y", "y", ""])
        self.assertEqual(code, 3)
        self.assertEqual(len(self.asked), 3)
        self.assertTrue((self.folder / "notes" / "hello.txt").exists())

    def test_nobody_at_the_terminal_means_no_approval(self) -> None:
        code, out, _ = self.invoke(*self.granted(), answers=[], tty=False)
        self.assertEqual(code, 3)
        self.assertEqual(self.asked, [])
        self.assertIn('"status": "approval_required"', out)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_without_the_approve_flag_nothing_runs(self) -> None:
        code, _, err = self.invoke(
            "--allow-folder", str(self.folder), "--allow-shell", answers=[]
        )
        self.assertEqual(code, 2)
        self.assertIn("requires a verified approval record", err)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_a_declaration_without_a_grant_reaches_nothing(self) -> None:
        code, _, err = self.invoke("--approve", answers=["y", "y", "y"])
        self.assertNotEqual(code, 0)
        self.assertNotIn("'saved'", err)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_shell_stays_closed_unless_allowed(self) -> None:
        code, _, _ = self.invoke(
            "--allow-folder", str(self.folder), "--approve", answers=["y", "y", "y"]
        )
        self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
