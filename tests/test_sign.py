"""Tests for the Number.sign() method."""

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


class SignMethodTests(unittest.TestCase):
    def test_sign_of_positive_number_is_one(self) -> None:
        self.assertEqual(value_of("5.sign()", "Number"), 1)
        self.assertEqual(value_of("0.1.sign()", "Number"), 1)
        self.assertEqual(value_of("100.sign()", "Number"), 1)

    def test_sign_of_negative_number_is_minus_one(self) -> None:
        self.assertEqual(value_of("-5.sign()", "Number"), -1)
        self.assertEqual(value_of("-0.1.sign()", "Number"), -1)
        self.assertEqual(value_of("-100.sign()", "Number"), -1)

    def test_sign_of_zero_is_zero(self) -> None:
        self.assertEqual(value_of("0.sign()", "Number"), 0)
        self.assertEqual(value_of("0.0.sign()", "Number"), 0)
