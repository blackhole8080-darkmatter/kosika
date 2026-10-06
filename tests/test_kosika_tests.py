"""Tests written in Kosika: `test "name" { ... }` and `kosika test`."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kosika import cli
from kosika.ast import program_payload
from kosika.checker import check
from kosika.parser import ParseError, parse
from kosika.testing import run_tests, to_text

PROGRAM = """
record Ticket { text: Text }
record Out { queue: Text brief: Text }

model assistant

fn double(n: Number) -> Number {
  return n * 2
}

fn build(label: Choice, brief: Summary) -> Out {
  return {queue: label.label, brief: brief.text}
}

workflow main(ticket: Ticket) -> Out {
  label = classify ticket.text into ["billing", "other"] retry 1
  brief = summarize ticket.text
  out   = build(label, brief)
}
"""

GOOD = """
test "a double charge goes to billing" {
  answers assistant: [{label: "billing", confidence: 0.9}, {text: "Charged twice."}]
  ticket: Ticket = {text: "My card was charged twice."}
  result = main(ticket)
  expect result.queue == "billing"
  expect result.brief.contains("twice")
}
"""


def results(tests: str, only: str | None = None):
    program = parse(PROGRAM + tests)
    check(program)
    return run_tests(program, only=only)


def message(tests: str) -> str:
    (result,) = results(tests)
    assert not result.passed, "expected the test to fail"
    return result.message


class RunTests(unittest.TestCase):
    def test_a_pure_function_can_be_tested(self) -> None:
        (result,) = results('test "double" {\n  x = double(4)\n  expect x == 8\n}\n')
        self.assertTrue(result.passed)

    def test_a_workflow_runs_on_scripted_answers(self) -> None:
        (result,) = results(GOOD)
        self.assertTrue(result.passed, result.message)

    def test_a_failed_expectation_shows_both_sides(self) -> None:
        text = message('test "t" {\n  expect double(4) == 9\n}\n')
        self.assertEqual(text, "expected double(4) == 9 (left is 8, right is 9)")

    def test_the_failing_line_is_reported(self) -> None:
        (result,) = results('test "t" {\n  expect true\n  expect 1 > 2\n}\n')
        self.assertEqual(result.line, PROGRAM.count("\n") + 3)

    def test_retry_is_exercised_by_a_scripted_wrong_answer(self) -> None:
        tests = GOOD.replace(
            '[{label: "billing"', '[{label: "refund", confidence: 1}, {label: "billing"'
        )
        (result,) = results(tests)
        self.assertTrue(result.passed, result.message)

    def test_a_model_asked_without_answers_fails_the_test(self) -> None:
        self.assertIn(
            "no response left",
            message('test "t" {\n  result = main({text: "x"})\n  expect true\n}\n'),
        )

    def test_answers_that_are_never_asked_for_fail_the_test(self) -> None:
        tests = GOOD.replace(
            '{text: "Charged twice."}]', '{text: "Charged twice."}, {text: "extra"}]'
        )
        self.assertIn(
            "1 answer(s) for 'assistant' were never asked for", message(tests)
        )

    def test_a_run_that_fails_fails_the_test_with_its_reason(self) -> None:
        tests = GOOD.replace('label: "billing"', 'label: "refund"').replace(
            "retry 1", "retry 1"
        )
        self.assertIn("missing required property 'label'", message(tests))

    def test_type_errors_in_a_test_are_reported(self) -> None:
        self.assertIn(
            "expect needs a Bool", message('test "t" {\n  expect double(1)\n}\n')
        )
        self.assertIn(
            "expects 'Ticket'", message('test "t" {\n  r = main(5)\n  expect true\n}\n')
        )
        self.assertIn("unknown model", message('test "t" {\n  answers nobody: []\n}\n'))
        self.assertIn(
            "already bound", message('test "t" {\n  x = 1\n  x = 2\n  expect true\n}\n')
        )

    def test_one_test_can_be_chosen_by_name(self) -> None:
        tests = GOOD + 'test "other" {\n  expect false\n}\n'
        chosen = results(tests, only="a double charge goes to billing")
        self.assertEqual([r.passed for r in chosen], [True])
        self.assertEqual([r.passed for r in results(tests)], [True, False])

    def test_the_report_counts_passes_and_failures(self) -> None:
        report = to_text(results(GOOD + 'test "other" {\n  expect false\n}\n'))
        self.assertIn("PASS  a double charge goes to billing", report)
        self.assertIn("FAIL  other", report)
        self.assertTrue(report.endswith("1 passed, 1 failed"))
        self.assertEqual(to_text([]), "No tests found")


class LanguageTests(unittest.TestCase):
    def test_tests_are_not_part_of_the_program_hash(self) -> None:
        self.assertEqual(
            program_payload(parse(PROGRAM)), program_payload(parse(PROGRAM + GOOD))
        )

    def test_test_remains_usable_as_a_name(self) -> None:
        source = PROGRAM.replace("fn double(n: Number)", "fn test(n: Number)")
        check(parse(source))

    def test_an_unknown_line_in_a_test_is_a_parse_error(self) -> None:
        with self.assertRaisesRegex(ParseError, "Expected 'expect'"):
            parse(PROGRAM + 'test "t" {\n  return 1\n}\n')


class CommandTests(unittest.TestCase):
    def invoke(self, *arguments: str) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["kosika", *arguments]):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main()
        return code, out.getvalue() + err.getvalue()

    def test_the_command_reports_and_sets_its_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "main.kos"
            source.write_text(PROGRAM + GOOD, encoding="utf-8")
            code, output = self.invoke("test", str(source))
            self.assertEqual(code, 0)
            self.assertIn("1 passed, 0 failed", output)

            source.write_text(
                PROGRAM + GOOD + 'test "bad" {\n  expect false\n}\n', encoding="utf-8"
            )
            code, output = self.invoke("test", str(source), "--json")
            self.assertEqual(code, 1)
            self.assertEqual([r["passed"] for r in json.loads(output)], [True, False])

            source.write_text("record R {", encoding="utf-8")
            code, output = self.invoke("test", str(source))
            self.assertEqual(code, 2)
            self.assertIn("Check failed", output)
