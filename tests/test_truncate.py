"""The truncate method: shortens text to a maximum length with an ellipsis."""

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


class TruncateTests(unittest.TestCase):
    def test_truncate_keeps_short_text_unchanged(self) -> None:
        self.assertEqual(value_of('"abc".truncate(3)', "Text"), "abc")
        self.assertEqual(value_of('"abc".truncate(4)', "Text"), "abc")
        self.assertEqual(value_of('"abc".truncate(10)', "Text"), "abc")

    def test_truncate_shortens_long_text(self) -> None:
        self.assertEqual(value_of('"abcdef".truncate(3)', "Text"), "ab…")
        self.assertEqual(value_of('"abcdef".truncate(4)', "Text"), "abc…")
        self.assertEqual(value_of('"abcdef".truncate(1)', "Text"), "…")
