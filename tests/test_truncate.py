"""Tests for the Text.truncate method."""

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


class TruncateMethodTests(unittest.TestCase):
    def test_no_truncation_when_short_enough(self) -> None:
        self.assertEqual(value_of('"hello".truncate(5)', "Text"), "hello")
        self.assertEqual(value_of('"hi".truncate(10)', "Text"), "hi")
        self.assertEqual(value_of('"" .truncate(1)', "Text"), "")

    def test_truncation_with_ellipsis(self) -> None:
        self.assertEqual(value_of('"abcdef".truncate(4)', "Text"), "abc…")
        self.assertEqual(value_of('"12345".truncate(3)', "Text"), "12…")
        self.assertEqual(value_of('"a".truncate(1)', "Text"), "a")

    def test_edge_cases(self) -> None:
        # most exactly the length -> unchanged
        self.assertEqual(value_of('"xyz".truncate(3)', "Text"), "xyz")
        # most larger than length -> unchanged
        self.assertEqual(value_of('"xyz".truncate(100)', "Text"), "xyz")
        # empty string with any valid most
        self.assertEqual(value_of('"" .truncate(5)', "Text"), "")

    def test_single_character_truncate(self) -> None:
        # When most is 1, result is always the ellipsis regardless of input length >0
        self.assertEqual(value_of('"abc".truncate(1)', "Text"), "…")
        self.assertEqual(value_of('"a".truncate(1)', "Text"), "a")
        # Empty string stays empty even with most=1
        self.assertEqual(value_of('"" .truncate(1)', "Text"), "")


if __name__ == "__main__":
    unittest.main()
