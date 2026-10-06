"""Early `return` inside a function, and `if` without `else`."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika.checker import CheckError, check
from kosika.parser import ParseError, parse
from kosika.runtime import replay, run

RECORDS = (
    "record In { n: Number words: Text[] }\nrecord Out { text: Text value: Number }\n"
)


def program(body: str, extra: str = ""):
    source = RECORDS + extra + f"fn main(input: In) -> Out {{\n{body}\n}}\n"
    parsed = parse(source)
    check(parsed)
    return parsed


def result(
    body: str, n: int = 0, words: list[str] | None = None, extra: str = ""
) -> Any:
    inputs = {"input": {"n": n, "words": words or []}}
    return run(program(body, extra), "main", None, inputs=inputs)


def error_of(body: str, extra: str = "") -> str:
    try:
        program(body, extra)
    except (CheckError, ParseError) as exc:
        return str(exc)
    raise AssertionError("expected the program to be rejected")


GUARD = (
    'if input.n < 0 { return {text: "negative", value: 0} }\n'
    'if input.n == 0 { return {text: "zero", value: 0} }\n'
    'return {text: "positive", value: input.n * 2}'
)


class EarlyReturnTests(unittest.TestCase):
    def test_guard_clauses_leave_the_function_early(self) -> None:
        self.assertEqual(result(GUARD, -5), {"text": "negative", "value": 0})
        self.assertEqual(result(GUARD, 0), {"text": "zero", "value": 0})
        self.assertEqual(result(GUARD, 4), {"text": "positive", "value": 8})

    def test_return_from_inside_a_loop_stops_the_loop(self) -> None:
        body = (
            "var seen = 0\n"
            "for word in input.words {\n"
            "  seen = seen + 1\n"
            '  if word == "stop" { return {text: word, value: seen} }\n'
            "}\n"
            'return {text: "none", value: seen}'
        )
        self.assertEqual(
            result(body, words=["a", "stop", "b"]), {"text": "stop", "value": 2}
        )
        self.assertEqual(result(body, words=["a", "b"]), {"text": "none", "value": 2})

    def test_return_in_a_match_arm_and_an_else_branch(self) -> None:
        body = (
            "let label: Text = match input.n {\n"
            '  case 1 { return {text: "one", value: 1} }\n'
            '  case _ { "many" }\n'
            "}\n"
            "let size: Number = if input.n > 100 { 100 } else { return {text: label, value: -1} }\n"
            'return {text: "big", value: size}'
        )
        self.assertEqual(result(body, 1), {"text": "one", "value": 1})
        self.assertEqual(result(body, 5), {"text": "many", "value": -1})
        self.assertEqual(result(body, 500), {"text": "big", "value": 100})

    def test_only_the_innermost_function_returns(self) -> None:
        extra = (
            "fn clamp(n: Number) -> Number {\n"
            "  if n > 10 { return 10 }\n  return n\n}\n"
        )
        body = 'return {text: "x", value: clamp(50) + clamp(3)}'
        self.assertEqual(result(body, extra=extra)["value"], 13)

    def test_an_early_result_is_validated_traced_and_replayed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            answer = run(
                program(GUARD), "main", trace, inputs={"input": {"n": -1, "words": []}}
            )
            self.assertEqual(replay(trace), answer)

    def test_an_early_return_must_have_the_function_type(self) -> None:
        message = error_of(
            'if input.n < 0 { return "negative" }\nreturn {text: "x", value: 1}'
        )
        self.assertIn("early return expects 'Out', got 'Text'", message)

    def test_recursion_through_an_early_return_still_needs_a_bound(self) -> None:
        extra = (
            "fn count(n: Number) -> Number {\n"
            "  if n > 0 { return count(n - 1) }\n  return 0\n}\n"
        )
        with self.assertRaises(CheckError):
            program('return {text: "x", value: count(3)}', extra)
        bounded = extra.replace("-> Number {", "-> Number recurse 8 {", 1)
        self.assertEqual(
            result('return {text: "x", value: count(3)}', extra=bounded)["value"], 0
        )

    def test_a_function_still_needs_its_final_return(self) -> None:
        with self.assertRaises(ParseError):
            program('if input.n < 0 { return {text: "a", value: 0} }')

    def test_return_is_not_allowed_in_a_workflow_step(self) -> None:
        source = RECORDS + (
            'fn make(input: In) -> Out { return {text: "x", value: 1} }\n'
            "workflow main(input: In) -> Out { out = make(input: return input) }\n"
        )
        with self.assertRaises((CheckError, ParseError)) as raised:
            check(parse(source))
        self.assertIn("return", str(raised.exception))


class IfWithoutElseTests(unittest.TestCase):
    def test_as_a_statement_it_does_nothing_when_false(self) -> None:
        body = (
            "var total = 1\n"
            "if input.n > 5 { total = total + input.n }\n"
            'return {text: "x", value: total}'
        )
        self.assertEqual(result(body, 9)["value"], 10)
        self.assertEqual(result(body, 2)["value"], 1)

    def test_as_a_value_it_is_optional(self) -> None:
        body = (
            'let label: Text? = if input.n > 5 { "big" }\n'
            'return {text: label.or("small"), value: input.n}'
        )
        self.assertEqual(result(body, 9)["text"], "big")
        self.assertEqual(result(body, 2)["text"], "small")
        self.assertIn(
            "expects 'Text', got 'Text?'",
            error_of(
                'let label: Text = if input.n > 5 { "big" }\nreturn {text: label, value: 1}'
            ),
        )

    def test_else_if_chains_and_full_conditionals_are_unchanged(self) -> None:
        body = (
            'let t: Text = if input.n > 5 { "a" } else if input.n > 1 { "b" } else { "c" }\n'
            "return {text: t, value: 0}"
        )
        self.assertEqual([result(body, n)["text"] for n in (9, 3, 0)], ["a", "b", "c"])


if __name__ == "__main__":
    unittest.main()
