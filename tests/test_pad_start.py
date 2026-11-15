"""Tests for the Text.pad_start method."""

from __future__ import annotations

import unittest
from typing import Any

from kosika.checker import check
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


class PadStartTests(unittest.TestCase):
    def test_no_padding_needed(self) -> None:
        self.assertEqual(value_of('"hello".pad_start(5, "*")', "Text"), "hello")
        self.assertEqual(value_of('"world".pad_start(3, "-")', "Text"), "world")

    def test_simple_padding(self) -> None:
        self.assertEqual(value_of('"hi".pad_start(5, "0")', "Text"), "000hi")
        self.assertEqual(value_of('"" .pad_start(4, "x")', "Text"), "xxxx")
        self.assertEqual(value_of('"a".pad_start(2, "!")', "Text"), "!a")

    def test_multiple_character_fill_error_not_tested(self) -> None:
        # This test suite only checks successful calls; error cases are covered elsewhere.
        pass

    def test_edge_cases(self) -> None:
        # width exactly one more than length
        self.assertEqual(value_of('"abc".pad_start(4, "#")', "Text"), "#abc")
        # width zero (should return empty string unchanged)
        self.assertEqual(value_of('"" .pad_start(0, "?")', "Text"), "")
        # very large width within limits
        self.assertEqual(
            value_of('"x".pad_start(10, "y")', "Text"),
            "yyyyyyyyyx",
        )
