"""The trim_start and trim_end text methods."""

from __future__ import annotations

import unittest
from typing import Any

from kosika.checker import CheckError, check
from kosika.parser import parse
from kosika.runtime import run


def value_of(expression: str, type_name: str, setup: str = "") -> Any:
    source = (
        f"record Out {{ value: {type_name} }}\n"
        f"fn f() -> Out {{\n{setup}\nreturn {{value: {expression}}}\n}}\n"
        "workflow main() -> Out { out = f() }\n"
    )
    program = parse(source)
    check(program)
    return run(program, "main", None, inputs={})["value"]


class TrimEndsTests(unittest.TestCase):
    def test_trim_start_removes_leading_whitespace(self) -> None:
        self.assertEqual(value_of('"  hello  ".trim_start()', "Text"), "hello  ")
        self.assertEqual(value_of('"hello".trim_start()', "Text"), "hello")
        self.assertEqual(value_of('"   ".trim_start()', "Text"), "")
        self.assertEqual(value_of('"  \t\n  hello".trim_start()', "Text"), "hello")

    def test_trim_end_removes_trailing_whitespace(self) -> None:
        self.assertEqual(value_of('"  hello  ".trim_end()', "Text"), "  hello")
        self.assertEqual(value_of('"hello".trim_end()', "Text"), "hello")
        self.assertEqual(value_of('"   ".trim_end()', "Text"), "")
        self.assertEqual(value_of('"hello  \t\n  ".trim_end()', "Text"), "hello")

    def test_trim_start_and_end_together(self) -> None:
        self.assertEqual(
            value_of('"  hello  ".trim_start().trim_end()', "Text"),
            "hello",
        )
        self.assertEqual(
            value_of('"  hello  ".trim_end().trim_start()', "Text"),
            "hello",
        )
