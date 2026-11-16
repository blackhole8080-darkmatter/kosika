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
    def test_truncate_short_text_unchanged(self) -> None:
        # text length less than most
        self.assertEqual(value_of('"hello".truncate(10)', "Text"), "hello")
        # text length equal to most
        self.assertEqual(value_of('"world".truncate(5)', "Text"), "world")

    def test_truncate_long_text_ellipsis(self) -> None:
        # most = 3, text longer than 3 characters
        self.assertEqual(value_of('"abcdef".truncate(3)', "Text"), "ab…")
        # most = 1, any non‑empty text should become just the ellipsis
        self.assertEqual(value_of('"xyz".truncate(1)', "Text"), "…")
        # most = 2, text length equal to most stays unchanged
        self.assertEqual(value_of('"ab".truncate(2)', "Text"), "ab")
        # most = 2, text longer than most gets truncated
        self.assertEqual(value_of('"abc".truncate(2)', "Text"), "a…")

    def test_truncate_empty_string(self) -> None:
        # empty string is always unchanged regardless of most
        self.assertEqual(value_of('"" .truncate(5)', "Text"), "")
        self.assertEqual(value_of('"" .truncate(1)', "Text"), "")
