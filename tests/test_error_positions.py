"""Checker errors point at the token they name, when that is unambiguous."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika.development import check_project

HEAD = (
    "record Order { customer: Text items: Text total: Number }\n"
    "record Out { n: Number t: Text }\n"
)
MAIN = "workflow main(order: Order) -> Out { out = f(order) }\n"


def diagnose(source: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "main.kos"
        path.write_text(source, encoding="utf-8")
        report = check_project(path)["diagnostics"]
    assert not report["ok"], "expected a diagnostic"
    return report["diagnostics"][0]


def position(source: str) -> tuple[int, int]:
    start = diagnose(source)["range"]["start"]
    return start["line"] + 1, start["character"] + 1


def function(*body: str) -> str:
    lines = "".join(f"  {line}\n" for line in body)
    return HEAD + "fn f(order: Order) -> Out {\n" + lines + "}\n" + MAIN


class NamedTokenTests(unittest.TestCase):
    def test_an_unknown_field_points_at_the_field(self) -> None:
        source = function(
            "let count = order.items.length()",
            "return {n: count, t: order.custmer}",
        )
        self.assertEqual(position(source), (5, 30))
        self.assertEqual(diagnose(source)["data"]["anchor"], "name")

    def test_a_misspelled_argument_points_at_the_argument(self) -> None:
        source = function(
            'let names = order.items.split(seperator: ",")',
            'return {n: names.length(), t: "x"}',
        )
        self.assertEqual(position(source), (4, 33))

    def test_an_unknown_variable_and_method_point_at_their_use(self) -> None:
        self.assertEqual(
            position(function("let count = 1", 'return {n: cuont, t: "x"}')), (5, 14)
        )
        self.assertEqual(
            position(function('return {n: order.items.lenght(), t: "x"}')), (4, 26)
        )

    def test_a_quoted_field_name_points_at_the_string(self) -> None:
        source = (
            "record Row { size: Number }\nrecord In { rows: Row[] }\nrecord Out { n: Number }\n"
            'fn main(input: In) -> Out {\n  return {n: input.rows.sum_by("sise")}\n}\n'
        )
        diagnostic = diagnose(source)
        self.assertEqual(diagnostic["range"]["start"], {"line": 4, "character": 31})
        self.assertEqual(diagnostic["range"]["end"]["character"], 37)

    def test_a_wrong_local_type_points_at_the_local(self) -> None:
        source = function("let count: Text = order.total", "return {n: 1, t: count}")
        diagnostic = diagnose(source)
        self.assertEqual(diagnostic["code"], "check/type-mismatch")
        self.assertEqual(diagnostic["owner"], "f")
        self.assertEqual((diagnostic["range"]["start"]["line"] + 1,), (4,))

    def test_a_one_line_step_error_points_into_that_line(self) -> None:
        source = (
            HEAD
            + 'fn f(order: Order) -> Out { return {n: 1, t: "x"} }\n'
            + "workflow main(order: Order) -> Out {\n  first = f(order)\n  out = f(ordr: order)\n}\n"
        )
        self.assertEqual(position(source), (6, 11))


class AmbiguityTests(unittest.TestCase):
    def test_a_name_used_twice_falls_back_to_the_local_declaration(self) -> None:
        source = function(
            "let a = order.items.lenght()",
            "let b = order.customer.lenght()",
            'return {n: a + b, t: "x"}',
        )
        diagnostic = diagnose(source)
        self.assertEqual(diagnostic["range"]["start"], {"line": 3, "character": 6})

    def test_a_name_used_twice_with_no_local_falls_back_to_the_function(self) -> None:
        source = function('return {n: order.totl + order.totl, t: "x"}')
        diagnostic = diagnose(source)
        self.assertEqual(diagnostic["range"]["start"]["line"], 2)
        self.assertNotIn("anchor", diagnostic["data"])

    def test_the_suggestion_is_never_used_as_the_position(self) -> None:
        # `customer` (the suggestion) appears in the body; `custmer` is the fault.
        source = function(
            "let who = order.customer",
            "return {n: 1, t: order.custmer}",
        )
        self.assertEqual(position(source), (5, 26))


if __name__ == "__main__":
    unittest.main()
