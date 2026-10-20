"""A workflow step may be a plain value, worked out by code: `count =
before + 1`, `big = if count > 3 { true } else { false }`, or a record,
typed from where it goes or written out as `tally: Tally = {...}`."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika.ast import program_payload
from kosika.checker import CheckError, check
from kosika.parser import parse
from kosika.modules import load_program
from kosika.runtime import run
from kosika.testing import run_tests
from kosika.trace_replay import replay_trace

SOURCE = """
record Msg { text: Text }
record Tally { count: Number }
record Out { text: Text count: Number big: Bool }

memory tallies of Tally

fn base(m: Msg) -> Number {
  return m.text.length()
}

fn keep(t: Tally) -> Tally {
  return t
}

workflow main(m: Msg) -> Out {
  before = base(m)
  count  = before + 1
  big    = if count > 3 { true } else { false }
  tally  = {count: count}
  kept   = keep(tally)
  typed: Tally = {count: kept.count}
  shout  = base(m) + 1
  result = {text: m.text.upper() + "!" count: typed.count big: big}
}
"""


class ValueStepTests(unittest.TestCase):
    def test_values_are_steps_and_records_take_their_type_from_use(self) -> None:
        program = parse(SOURCE)
        check(program)
        out = run(program, "main", None, inputs={"m": {"text": "hey"}})
        self.assertEqual(out, {"text": "HEY!", "count": 4, "big": True})

    def test_fields_of_a_record_value_need_no_commas(self) -> None:
        self.assertIn("big: big}", SOURCE)
        check(parse(SOURCE))

    def test_a_value_whose_type_cannot_be_told_says_what_to_write(self) -> None:
        bad = SOURCE.replace("  kept   = keep(tally)\n", "").replace(
            "{count: kept.count}", "{count: count}"
        )
        with self.assertRaisesRegex(CheckError, "write it out, as `tally: Type = ...`"):
            check(parse(bad))

    def test_a_call_with_more_after_it_is_a_value(self) -> None:
        program = parse(SOURCE)
        step = next(s for s in program.workflows[0].steps if s.name == "shout")
        self.assertTrue(step.call.function_name.endswith("$value"))

    def test_a_fault_in_the_value_is_the_one_reported(self) -> None:
        bad = SOURCE.replace("count  = before + 1", 'count  = before + "1"')
        with self.assertRaisesRegex(CheckError, r"operator '\+' expects Number"):
            check(parse(bad))
        typo = SOURCE.replace("count  = before + 1", "count  = befor + 1")
        with self.assertRaisesRegex(CheckError, "unknown variable 'befor'"):
            check(parse(typo))


BESIDE_A_CALL = """
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


class ValueBesideACallTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.folder = Path(self._dir.name)

    def program(self, source: str = BESIDE_A_CALL) -> Any:
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
                self.program(BESIDE_A_CALL.replace(*change))

    def test_a_program_without_one_hashes_as_before(self) -> None:
        plain = parse("record A { n: Number }\nfn main(a: A) -> A {\n  return a\n}")
        self.assertNotIn("$value", json.dumps(program_payload(plain)))


if __name__ == "__main__":
    unittest.main()
