"""Calls may pass every argument without a name."""

from __future__ import annotations

import unittest
from typing import Any

from kosika.ast import program_payload
from kosika.checker import CheckError, check
from kosika.parser import ParseError, parse
from kosika.runtime import run

HEAD = """
record In { a: Number b: Number text: Text }
record Out { n: Number text: Text }

fn minus(a: Number, b: Number) -> Number {
  return a - b
}

fn label(text: Text, count: Number) -> Text {
  return text + ":" + count
}
"""


def program(body: str, extra: str = "") -> str:
    return (
        HEAD
        + extra
        + "fn work(input: In) -> Out {\n"
        + body
        + "\n}\n"
        + "workflow main(input: In) -> Out {\n  out = work(input)\n}\n"
    )


def result(body: str, extra: str = "") -> Any:
    checked = parse(program(body, extra))
    check(checked)
    return run(checked, "main", None, inputs={"input": {"a": 10, "b": 3, "text": "hi"}})


def rejected(body: str) -> str:
    try:
        check(parse(program(body)))
    except (CheckError, ParseError) as exc:
        return str(exc)
    raise AssertionError("expected the program to be rejected")


class PositionalTests(unittest.TestCase):
    def test_values_are_matched_by_position(self) -> None:
        self.assertEqual(
            result('  return {n: minus(10, 3), text: label("x", 2 + 3)}'),
            {"n": 7, "text": "x:5"},
        )

    def test_variables_with_other_names_are_matched_by_position(self) -> None:
        body = """
  let big = 10
  let small = 3
  return {n: minus(big, small), text: ""}"""
        self.assertEqual(result(body)["n"], 7)

    def test_same_named_variables_are_matched_by_name_in_any_order(self) -> None:
        # Passing `b, a` to (a, b) must not silently swap them.
        body = """
  let a = 10
  let b = 3
  return {n: minus(b, a), text: ""}"""
        self.assertEqual(result(body)["n"], 7)
        self.assertEqual(
            result('  return {n: minus(input.b, input.a), text: ""}')["n"], 7
        )

    def test_one_same_named_and_one_other_value_go_by_position(self) -> None:
        body = """
  let b = 10
  return {n: minus(b, 3), text: ""}"""
        self.assertEqual(result(body)["n"], 7)

    def test_built_in_methods_take_positions_too(self) -> None:
        body = """
  var items = [1, 2, 3]
  items = items.set(0, 9)
  return {n: items[0], text: "kosika".slice(0, 3)}"""
        self.assertEqual(result(body), {"n": 9, "text": "kos"})

    def test_named_calls_are_unchanged(self) -> None:
        self.assertEqual(result('  return {n: minus(b: 3, a: 10), text: ""}')["n"], 7)

    def test_a_step_can_pass_positions(self) -> None:
        source = HEAD + (
            "fn both(a: Number, b: Number) -> Out {\n"
            '  return {n: a - b, text: ""}\n}\n'
            "workflow main(input: In) -> Out {\n  out = both(input.a + 0, 4)\n}\n"
        )
        checked = parse(source)
        check(checked)
        self.assertEqual(
            run(checked, "main", None, inputs={"input": {"a": 10, "b": 3, "text": ""}}),
            {"n": 6, "text": ""},
        )

    def test_the_wrong_count_is_a_check_error(self) -> None:
        self.assertIn(
            "needs a name", rejected('  return {n: minus(1, 2, 3), text: ""}')
        )

    def test_the_wrong_type_names_the_parameter(self) -> None:
        self.assertIn("'count'", rejected('  return {n: 0, text: label("x", "y")}'))

    def test_naming_only_some_arguments_is_refused(self) -> None:
        self.assertIn(
            "needs a name", rejected('  return {n: minus(a: 1, 2 + 0), text: ""}')
        )

    def test_positional_and_named_calls_are_the_same_program(self) -> None:
        by_position = parse(program('  return {n: minus(10, 3), text: ""}'))
        by_name = parse(program('  return {n: minus(a: 10, b: 3), text: ""}'))
        self.assertEqual(program_payload(by_position), program_payload(by_name))


class ListLiteralTests(unittest.TestCase):
    def test_a_list_of_like_items_needs_no_type(self) -> None:
        body = """
  var items = [4, 5, 6]
  items[1] = 50
  let words = ["a", "b"]
  return {n: items.sum(), text: words.join("-")}"""
        self.assertEqual(result(body), {"n": 60, "text": "a-b"})

    def test_an_empty_or_mixed_list_still_needs_one(self) -> None:
        self.assertIn("annotate", rejected('  let x = []\n  return {n: 0, text: ""}'))
        self.assertIn(
            "annotate", rejected('  let x = [1, "a"]\n  return {n: 0, text: ""}')
        )
