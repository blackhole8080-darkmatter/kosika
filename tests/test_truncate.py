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
        self.assertEqual(value_of('"hi".truncate(2)', "Text"), "hi")
        self.assertEqual(value_of('"hello".truncate(5)', "Text"), "hello")
        self.assertEqual(value_of('"" .truncate(1)', "Text"), "")

    def test_truncation_with_ellipsis(self) -> None:
        # most = 4, original length 5 -> first 3 chars + ellipsis
        self.assertEqual(value_of('"hello".truncate(4)', "Text"), "hel…")
        # most = 3, original length 6 -> first 2 chars + ellipsis
        self.assertEqual(value_of('"kosika".truncate(3)', "Text"), "ko…")
        # most = 1, any non‑empty string -> just the ellipsis
        self.assertEqual(value_of('"abc".truncate(1)', "Text"), "…")
        # most = 2, original length 2 -> unchanged (edge of limit)
        self.assertEqual(value_of('"ab".truncate(2)', "Text"), "ab")

    def test_truncate_unicode_and_emoji(self) -> None:
        # Unicode characters count as one code point each
        self.assertEqual(value_of('"😀😃😄".truncate(2)', "Text"), "😀…")
        # Mixed ASCII and Unicode; length equals most, so unchanged
        self.assertEqual(value_of('"a😀b".truncate(3)', "Text"), "a😀b")

    def test_truncate_multiple_calls(self) -> None:
        # Chain calls to ensure method works repeatedly
        expr = '"abcdefgh".truncate(5).truncate(3)'
        self.assertEqual(value_of(expr, "Text"), "ab…")
