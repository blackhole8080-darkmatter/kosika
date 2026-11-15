"""Memory: `memory NAME of Record`, `remember`, and `recall`."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from kosika import cli
from kosika.ast import program_payload
from kosika.checker import check
from kosika.memory import MAX_KEY_LENGTH, MemoryStore, bind_memory_tools
from kosika.mindmap import build_mindmap
from kosika.modules import load_program
from kosika.parser import ParseError, parse
from kosika.runtime import replay, run
from kosika.testing import run_tests

SOURCE = """
record Visit { name: Text }
record Note { visits: Number }
record Out { visits: Number seen: Bool }

memory people of Note

fn next(known: PeopleRecalled) -> Note {
  let before = if known.found { known.value.value().visits } else { 0 }
  return {visits: before + 1}
}

fn out(known: PeopleRecalled, note: Note) -> Out {
  return {visits: note.visits, seen: known.found}
}

workflow main(visit: Visit) -> Out {
  known  = recall people under visit.name
  note   = next(known)
  saved  = remember note in people under visit.name
  result = out(known, note)
}
"""

ADA = {"visit": {"name": "Ada"}}


def checked(source: str = SOURCE):
    program = parse(source)
    check(program)
    return program


def error_of(source: str) -> str:
    try:
        checked(source)
    except ValueError as exc:
        return str(exc)
    raise AssertionError("expected the program to be rejected")


class RunTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.folder = Path(self._dir.name) / "memory"

    def visit(self, inputs: dict[str, Any] = ADA, trace: Path | None = None) -> Any:
        program = checked()
        return run(
            program,
            "main",
            trace,
            inputs=inputs,
            tool_bindings=bind_memory_tools(program, self.folder),
        )

    def test_a_fact_remembered_in_one_run_is_recalled_in_the_next(self) -> None:
        self.assertEqual(self.visit(), {"visits": 1, "seen": False})
        self.assertEqual(self.visit(), {"visits": 2, "seen": True})
        self.assertEqual(
            self.visit({"visit": {"name": "Grace"}}), {"visits": 1, "seen": False}
        )
        stored = json.loads((self.folder / "people.json").read_text("utf-8"))
        self.assertEqual(stored, {"Ada": {"visits": 2}, "Grace": {"visits": 1}})

    def test_without_a_grant_the_run_is_refused_before_it_starts(self) -> None:
        with self.assertRaisesRegex(PermissionError, "Memory 'people' was not granted"):
            run(checked(), "main", None, inputs=ADA)
        self.assertFalse(self.folder.exists())

    def test_replay_uses_the_trace_and_never_the_store(self) -> None:
        trace = Path(self._dir.name) / "trace.jsonl"
        first = self.visit(trace=trace)
        self.visit()
        self.visit()
        self.assertEqual(replay(trace), first)

    def test_a_stored_value_of_the_wrong_shape_fails_the_run(self) -> None:
        self.folder.mkdir()
        (self.folder / "people.json").write_text(
            json.dumps({"Ada": {"visits": "many"}}), encoding="utf-8"
        )
        with self.assertRaises(Exception) as caught:
            self.visit()
        self.assertIn("visits", str(caught.exception))

    def test_a_key_must_be_reasonable(self) -> None:
        for name in ("", "x" * (MAX_KEY_LENGTH + 1)):
            with self.subTest(length=len(name)):
                with self.assertRaisesRegex(Exception, "memory key"):
                    self.visit({"visit": {"name": name}})

    def test_a_store_without_a_folder_lives_only_in_the_process(self) -> None:
        store = MemoryStore()
        self.assertEqual(store.recall("people", "Ada"), {"found": False, "value": None})
        self.assertEqual(
            store.remember("people", "Ada", {"visits": 1}), {"key": "Ada", "entries": 1}
        )
        self.assertTrue(store.recall("people", "Ada")["found"])


class LanguageTests(unittest.TestCase):
    def test_memory_is_ordinary_tools_underneath(self) -> None:
        program = checked()
        tools = {tool.name: tool for tool in program.tools}
        self.assertEqual(sorted(tools), ["recall_people", "remember_people"])
        self.assertEqual(tools["recall_people"].source, "memory:people")
        self.assertEqual([agent.name for agent in program.agents], ["memory"])
        labels = {node["label"] for node in build_mindmap(program)["nodes"]}
        self.assertIn("recall_people", labels)

    def test_recall_like_adds_its_tool_only_where_it_is_used(self) -> None:
        program = checked(
            SOURCE.replace(
                "workflow ",
                "workflow nearest(a: Ask) -> PeopleLike {\n"
                "  found = recall people like a.text\n}\n\nworkflow ",
                1,
            ).replace("record ", "record Ask { text: Text }\nrecord ", 1)
        )
        tools = {tool.name: tool for tool in program.tools}
        self.assertEqual(sorted(tools), ["like_people", "recall_people", "remember_people"])
        self.assertEqual(tools["like_people"].operation, "like")
        self.assertIn("like_people", next(a for a in program.agents if a.name == "memory").can)

    def test_only_the_declared_record_can_be_remembered(self) -> None:
        wrong = SOURCE.replace(
            "saved  = remember note in people", "saved  = remember visit in people"
        )
        self.assertIn("value", error_of(wrong))

    def test_an_undeclared_memory_is_an_error(self) -> None:
        self.assertIn(
            "remember_places",
            error_of(
                SOURCE.replace("remember note in people", "remember note in places")
            ),
        )

    def test_declaration_mistakes_are_reported(self) -> None:
        self.assertIn(
            "holds unknown record 'Nope'",
            error_of(SOURCE.replace("memory people of Note", "memory people of Nope")),
        )
        self.assertIn(
            "declared more than once",
            error_of(
                SOURCE.replace(
                    "memory people of Note",
                    "memory people of Note\nmemory people of Note",
                )
            ),
        )
        self.assertIn(
            "already declared differently",
            error_of(SOURCE + "\nrecord PeopleRecalled { x: Text }\n"),
        )

    def test_remember_and_recall_stay_usable_as_function_names(self) -> None:
        source = SOURCE + "\nfn recall(n: Number) -> Number {\n  return n\n}\n"
        checked(source)

    def test_malformed_steps_are_parse_errors(self) -> None:
        with self.assertRaisesRegex(ParseError, "Expected 'under'"):
            parse(
                SOURCE.replace(
                    "recall people under visit.name", "recall people visit.name"
                )
            )
        with self.assertRaisesRegex(ParseError, "Expected 'in'"):
            parse(SOURCE.replace("remember note in people", "remember note people"))

    def test_a_program_without_memory_keeps_its_hash_shape(self) -> None:
        plain = parse("record A { x: Text }\nfn f(a: A) -> A {\n  return a\n}\n")
        self.assertNotIn("memories", json.dumps(program_payload(plain)))
        self.assertIn('"memories"', json.dumps(program_payload(checked())))

    def test_tests_get_a_fresh_memory_each(self) -> None:
        tests = """
test "first" {
  a = main({name: "Ada"})
  b = main({name: "Ada"})
  expect a.visits == 1
  expect b.visits == 2
}
test "second starts empty" {
  a = main({name: "Ada"})
  expect a.seen == false
}
"""
        results = run_tests(checked(SOURCE + tests))
        self.assertEqual([r.passed for r in results], [True, True], results)


class CommandTests(unittest.TestCase):
    def test_run_with_a_memory_folder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.kos").write_text(SOURCE, encoding="utf-8")
            (root / "input.json").write_text(json.dumps(ADA), encoding="utf-8")
            self.assertEqual(len(load_program(root / "main.kos").memories), 1)

            def invoke(*extra: str) -> tuple[int, str]:
                out, err = io.StringIO(), io.StringIO()
                arguments = [
                    "kosika",
                    "run",
                    str(root / "main.kos"),
                    "--input",
                    str(root / "input.json"),
                    "--trace",
                    str(root / "trace.jsonl"),
                    *extra,
                ]
                with patch.object(sys, "argv", arguments):
                    with (
                        contextlib.redirect_stdout(out),
                        contextlib.redirect_stderr(err),
                    ):
                        code = cli.main()
                return code, out.getvalue() + err.getvalue()

            code, output = invoke()
            self.assertNotEqual(code, 0)
            self.assertIn("was not granted", output)
            invoke("--memory", str(root / "mem"))
            code, output = invoke("--memory", str(root / "mem"))
            self.assertEqual(code, 0, output)
            self.assertIn("'visits': 2", output)


if __name__ == "__main__":
    unittest.main()
