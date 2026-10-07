"""Read-only file tools need a folder grant but no approval; slow models fail cleanly."""

from __future__ import annotations

import tempfile
import unittest
import urllib.request
from pathlib import Path
from typing import Any

from kosika.checker import CheckError, check
from kosika.model import (
    ChatMessage,
    ModelProviderError,
    ModelRequest,
    OpenAICompatibleProvider,
)
from kosika.parser import parse
from kosika.runtime import run
from kosika.system_tools import bind_system_tools

SOURCE = """
record Where { path: Text }
record Listing { names: Text[] }
record FileText { text: Text }
record Out { names: Text[] text: Text }

tool list_folder {
  source system "files"
  operation: "list"
  access: observe
  input: Where
  output: Listing
}

tool read_file {
  source system "files"
  operation: "read"
  access: observe
  input: Where
  output: FileText
}

agent hands {
  can: [list_folder, read_file]
  max_steps: 1
}

fn nothing(where: Where) -> FileText {
  return {text: ""}
}

fn out(found: Listing, file: FileText) -> Out {
  return {names: found.names, text: file.text}
}

workflow main(where: Where) -> Out {
  found  = execute hands.list_folder(".")
  file   = execute hands.read_file(where.path) otherwise nothing(where)
  result = out(found, file)
}
"""


def checked(source: str = SOURCE):
    program = parse(source)
    check(program)
    return program


class ReadOnlyFilesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.outside = Path(self._dir.name)
        self.folder = self.outside / "granted"
        (self.folder / "sub").mkdir(parents=True)
        (self.folder / "note.txt").write_text("hello", encoding="utf-8")
        (self.outside / "secret.txt").write_text("secret", encoding="utf-8")

    def look(self, path: str) -> Any:
        program = checked()
        return run(
            program,
            "main",
            None,
            inputs={"where": {"path": path}},
            tool_bindings=bind_system_tools(program, folder=self.folder),
        )

    def test_listing_and_reading_need_no_approval(self) -> None:
        # No `require approval`, no session, no approval record.
        self.assertEqual(
            self.look("note.txt"), {"names": ["note.txt", "sub/"], "text": "hello"}
        )

    def test_nothing_outside_the_granted_folder_can_be_read(self) -> None:
        for path in (
            "../secret.txt",
            str(self.outside / "secret.txt"),
            "sub/../../secret.txt",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.look(path)["text"], "")

    def test_nothing_is_changed_by_looking(self) -> None:
        before = sorted(p.name for p in self.folder.rglob("*"))
        self.look("note.txt")
        self.assertEqual(sorted(p.name for p in self.folder.rglob("*")), before)

    def test_writing_and_shell_still_need_execute(self) -> None:
        for change in (
            ('operation: "read"', 'operation: "write"'),
            (
                'source system "files"\n  operation: "read"',
                'source system "shell"\n  operation: "python"',
            ),
        ):
            with self.subTest(change=change[1]):
                with self.assertRaisesRegex(CheckError, "must declare access: execute"):
                    checked(SOURCE.replace(*change))

    def test_without_a_folder_grant_nothing_is_bound(self) -> None:
        self.assertEqual(bind_system_tools(checked(), folder=None), {})


class SlowModelTests(unittest.TestCase):
    def test_a_timeout_is_a_provider_failure_not_a_crash(self) -> None:
        def never(request: urllib.request.Request, timeout: float) -> bytes:
            raise TimeoutError("timed out")

        for protocol in ("openai", "ollama"):
            with self.subTest(protocol=protocol):
                provider = OpenAICompatibleProvider(
                    "http://localhost:1",
                    "m",
                    timeout=7,
                    transport=never,
                    protocol=protocol,
                )
                with self.assertRaisesRegex(
                    ModelProviderError, "did not answer within 7 seconds"
                ):
                    provider.complete(
                        ModelRequest(messages=(ChatMessage(role="user", content="x"),))
                    )


if __name__ == "__main__":
    unittest.main()


class BackslashTests(unittest.TestCase):
    def test_a_windows_path_in_a_string_is_kept_as_written(self) -> None:
        import warnings

        from kosika.lexer import lex

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            value = lex(r'"C:\Users\kosika\new \x41 \n \N{nope} end"')[0].value
        # `\U` and `\k` are not escapes and stay; `\n` and `\x41` are decoded.
        self.assertEqual(value, "C:\\Users\\kosika\new A \n \\N{nope} end")
