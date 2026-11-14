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
    def test_no_padding_when_width_is_equal(self) -> None:
        self.assertEqual(value_of('"hello".pad_start(5, "*")', "Text"), "hello")

    def test_no_padding_when_width_is_smaller(self) -> None:
        self.assertEqual(value_of('"world".pad_start(3, "#")', "Text"), "world")

    def test_simple_padding(self) -> None:
        self.assertEqual(value_of('"abc".pad_start(5, "0")', "Text"), "00abc")
        self.assertEqual(value_of('"x".pad_start(4, "-")', "Text"), "---x")

    def test_padding_empty_string(self) -> None:
        self.assertEqual(value_of('"" .pad_start(3, "+")', "Text"), "+++")
        self.assertEqual(value_of('"" .pad_start(0, "+")', "Text"), "")

    def test_large_width_within_limit(self) -> None:
        # 10 characters total, original length 2, fill with '*'
        self.assertEqual(
            value_of('"ok".pad_start(10, "*")', "Text"),
            "********ok",
        )
