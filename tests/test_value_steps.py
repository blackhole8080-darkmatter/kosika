"""A value where a step expects a call: `otherwise {matches: []}`."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika.ast import program_payload
from kosika.checker import CheckError
from kosika.diagnostics import diagnostic_from_exception
from kosika.modules import load_program
from kosika.parser import parse
from kosika.runtime import run
from kosika.testing import run_tests
from kosika.trace_replay import replay_trace

SOURCE = """
record Msg { text: Text }
record Hits { matches: Text[] }
record Query { path: Text query: Text }
record Said { text: Text count: Number }

tool search {
  source system "files"
  operation: "search"
  access: observe
  input: Query
  output: Hits
}

agent hands {
  can: [search]
  max_steps: 1
}

fn found(hits: Hits) -> Said {
  return {text: hits.matches.join(","), count: hits.matches.length()}
}

workflow main(msg: Msg) -> Said {
  hits = execute hands.search(".", msg.text) otherwise {matches: []}
  said = match msg.text {
    case "none" { {text: "nothing asked", count: -1} }
    case _ { found(hits) }
  }
}

test "the search answers" {
  answers search: [{matches: ["a", "b"]}]
  a = main({text: "tea"})
  expect a.text == "a,b"
}

test "the search fails, so the value stands in" {
  a = main({text: "tea"})
  expect a.count == 0
}

test "a value as a branch" {
  a = main({text: "none"})
  expect a.text == "nothing asked"
}
"""


class ValueStepTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.folder = Path(self._dir.name)

    def program(self, source: str = SOURCE) -> Any:
        (self.folder / "main.kos").write_text(source, encoding="utf-8")
        return load_program(self.folder / "main.kos")

    def test_the_program_passes_its_own_tests(self) -> None:
        results = run_tests(self.program())
        self.assertEqual([result.passed for result in results], [True, True, True])

    def test_a_value_becomes_a_function_that_returns_it(self) -> None:
        program = self.program()
        made = {f.name: f.output_type for f in program.functions if "$" in f.name}
        self.assertEqual(
            made, {"main$hits$otherwise": "Hits", "main$said$case1": "Said"}
        )

    def test_a_run_with_a_value_branch_replays(self) -> None:
        result = run(
            self.program(),
            "main",
            self.folder / "run.jsonl",
            inputs={"msg": {"text": "none"}},
        )
        self.assertEqual(result, {"text": "nothing asked", "count": -1})
        self.assertEqual(replay_trace(self.folder / "run.jsonl"), result)

    def test_the_value_must_fit_the_type_it_stands_in_for(self) -> None:
        for change in (
            ("otherwise {matches: []}", "otherwise {matches: 3}"),
            ("otherwise {matches: []}", "otherwise {lines: []}"),
            ('{text: "nothing asked", count: -1}', '{text: "nothing asked"}'),
        ):
            with self.subTest(change=change[1]), self.assertRaises(CheckError):
                self.program(SOURCE.replace(*change))

    def test_a_value_alone_is_not_a_step(self) -> None:
        source = SOURCE.replace(
            '  hits = execute hands.search(".", msg.text) otherwise {matches: []}',
            "  hits = {matches: []}",
        )
        with self.assertRaises(CheckError) as caught:
            self.program(source)
        self.assertIn("has a value where a call is needed", str(caught.exception))
        self.assertEqual(
            diagnostic_from_exception(caught.exception).code, "check/value-step"
        )

    def test_a_program_without_one_hashes_as_before(self) -> None:
        plain = parse("record A { n: Number }\nfn main(a: A) -> A {\n  return a\n}")
        self.assertNotIn("$value", json.dumps(program_payload(plain)))


if __name__ == "__main__":
    unittest.main()
