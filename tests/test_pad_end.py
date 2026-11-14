"""Tests for the Text.pad_end method."""

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


class PadEndMethodTests(unittest.TestCase):
    def test_pad_end_basic(self) -> None:
        # shorter than width, single fill character
        self.assertEqual(
            value_of('"abc".pad_end(5, "x")', "Text"),
            "abcxx",
        )
        # already at width, unchanged
        self.assertEqual(
            value_of('"hello".pad_end(5, "!")', "Text"),
            "hello",
        )
        # longer than width, unchanged
        self.assertEqual(
            value_of('"longtext".pad_end(4, "?")', "Text"),
            "longtext",
        )

    def test_pad_end_with_various_fill_chars(self) -> None:
        self.assertEqual(
            value_of('"".pad_end(3, " ")', "Text"),
            "   ",
        )
        self.assertEqual(
            value_of('"a".pad_end(4, "-")', "Text"),
            "a---",
        )
        self.assertEqual(
            value_of('"test".pad_end(6, "*")', "Text"),
            "test**",
        )

    def test_pad_end_zero_width(self) -> None:
        # width zero with empty text stays empty
        self.assertEqual(
            value_of('"".pad_end(0, "z")', "Text"),
            "",
        )
