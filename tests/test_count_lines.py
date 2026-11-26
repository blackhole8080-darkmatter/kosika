"""Tests for the Text.count_lines method."""

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


class CountLinesTests(unittest.TestCase):
    def test_count_lines_counts_lines_in_text(self) -> None:
        self.assertEqual(value_of('"".count_lines()', "Number"), 0)
        self.assertEqual(value_of('"hello".count_lines()', "Number"), 1)
        self.assertEqual(value_of('"hello\\nworld".count_lines()', "Number"), 2)
        self.assertEqual(value_of('"hello\\nworld\\n".count_lines()', "Number"), 2)
        self.assertEqual(value_of('"a\\nb\\nc\\nd".count_lines()', "Number"), 4)
        self.assertEqual(value_of('"\\n".count_lines()', "Number"), 1)
