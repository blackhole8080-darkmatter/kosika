"""Tests for the Text.truncate method."""

from __future__ import annotations

import unittest
from typing import Any

from kosika.checker import check, CheckError
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
        # most = 3, text longer than 3 characters
        self.assertEqual(value_of('"abcdef".truncate(3)', "Text"), "ab…")
        # most = 1, any non‑empty text should become just the ellipsis
        self.assertEqual(value_of('"abc".truncate(1)', "Text"), "…")
        # most = 4, text exactly 5 characters -> first 3 + ellipsis
        self.assertEqual(value_of('"12345".truncate(4)', "Text"), "123…")

    def test_truncate_with_unicode_ellipsis(self) -> None:
        # Ensure the ellipsis character is the correct Unicode character
        result = value_of('"xyz".truncate(2)', "Text")
        self.assertEqual(result, "x…")
        self.assertTrue("…" in result)

    def test_truncate_empty_string(self) -> None:
        # Empty string is always unchanged regardless of most
        self.assertEqual(value_of('"" .truncate(10)', "Text"), "")
        self.assertEqual(value_of('"" .truncate(1)', "Text"), "")
