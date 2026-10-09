"""`fail "reason"` and `try { } otherwise { }`."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika.checker import CheckError
from kosika.execution import ExecutionLimits
from kosika.modules import load_program
from kosika.parser import parse
from kosika.runtime import Failure, run
from kosika.trace_replay import replay_trace

SOURCE = """
record In { amount: Number people: Number }
record Out { text: Text }

fn share(amount: Number, people: Number) -> Number {
  if people <= 0 { fail "there must be at least one person" }
  if amount < 0 { fail f"the amount cannot be negative, got {amount}" }
  return amount / people
}

fn solve(input: In) -> Out {
  let each = try { share(input.amount, input.people) } otherwise problem { return {text: f"Could not split it: {problem}"} }
  let safe = try { share(10, 0) } otherwise { 0 }
  let far = try { [1, 2, 3][7] } otherwise { -1 }
  return {text: f"Each pays {each}. safe={safe} far={far}"}
}

fn strict(input: In) -> Out {
  return {text: f"{share(input.amount, input.people)}"}
}

fn calm(input: In) -> Out {
  return {text: "the step's own otherwise took it"}
}

workflow main(input: In) -> Out {
  result = solve(input)
}

workflow guarded(input: In) -> Out {
  result = strict(input) otherwise calm(input)
}

workflow bare(input: In) -> Out {
  result = strict(input)
}
"""


class FailAndTryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.folder = Path(self._dir.name)

    def program(self, source: str = SOURCE) -> Any:
        (self.folder / "main.kos").write_text(source, encoding="utf-8")
        return load_program(self.folder / "main.kos")

    def go(
        self, amount: float, people: float, workflow: str = "main", **extra: Any
    ) -> Any:
        return run(
            self.program(),
            workflow,
            self.folder / "run.jsonl",
            inputs={"input": {"amount": amount, "people": people}},
            **extra,
        )

    def test_a_body_that_works_gives_its_value(self) -> None:
        self.assertEqual(self.go(30, 3), {"text": "Each pays 10. safe=0 far=-1"})

    def test_a_failure_is_caught_with_its_reason(self) -> None:
        self.assertEqual(
            self.go(30, 0),
            {"text": "Could not split it: there must be at least one person"},
        )
        self.assertEqual(
            self.go(-5, 2),
            {"text": "Could not split it: the amount cannot be negative, got -5"},
        )

    def test_a_run_with_caught_failures_still_replays(self) -> None:
        result = self.go(30, 0)
        self.assertEqual(replay_trace(self.folder / "run.jsonl"), result)

    def test_a_failure_nobody_tried_reaches_the_steps_otherwise(self) -> None:
        self.assertEqual(
            self.go(30, 0, "guarded"), {"text": "the step's own otherwise took it"}
        )

    def test_a_failure_nothing_takes_fails_the_run_with_its_reason(self) -> None:
        with self.assertRaisesRegex(Failure, "there must be at least one person"):
            self.go(30, 0, "bare")

    def test_a_spent_budget_is_not_something_try_can_catch(self) -> None:
        source = SOURCE.replace(
            "let safe = try { share(10, 0) } otherwise { 0 }",
            "var n = 0\n  let safe = try {\n    while true { n = n + 1 }\n    1\n  } otherwise { 0 }",
        )
        (self.folder / "main.kos").write_text(source, encoding="utf-8")
        with self.assertRaises(RuntimeError) as caught:
            run(
                load_program(self.folder / "main.kos"),
                "main",
                None,
                inputs={"input": {"amount": 30, "people": 3}},
                limits=ExecutionLimits(max_loop_iterations=1000),
            )
        self.assertNotIsInstance(caught.exception, Failure)

    def test_retry_runs_the_body_again_before_giving_up(self) -> None:
        program = parse(
            "fn a(n: Number) -> Number {\n"
            '  return try { fail "always" } retry 2 otherwise { n }\n}'
        )
        self.assertEqual(program.functions[0].name, "a")

    def test_both_sides_must_give_the_same_type_and_the_reason_is_text(self) -> None:
        for change, reason in (
            (
                ("otherwise { 0 }", 'otherwise { "none" }'),
                "both must give the same type",
            ),
            (('fail "there must be at least one person"', "fail 7"), None),
            (
                (
                    "otherwise problem { return",
                    "otherwise problem { let n: Number = problem\n return",
                ),
                None,
            ),
        ):
            with (
                self.subTest(change=change[1]),
                self.assertRaises(CheckError) as caught,
            ):
                self.program(SOURCE.replace(*change))
            if reason:
                self.assertIn(reason, str(caught.exception))

    def test_a_name_called_fail_is_still_a_name(self) -> None:
        parse("fn a(fail: Number) -> Number {\n  return fail + 1\n}")


if __name__ == "__main__":
    unittest.main()
