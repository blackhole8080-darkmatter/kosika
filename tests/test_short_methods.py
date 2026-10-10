"""The short text and list methods: take, drop, words, replace, trim_marks, is_empty."""

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


class ShortMethodTests(unittest.TestCase):
    def test_take_and_drop_never_run_off_the_end(self) -> None:
        self.assertEqual(value_of('"kosika".take(3)', "Text"), "kos")
        self.assertEqual(value_of('"kosika".take(99)', "Text"), "kosika")
        self.assertEqual(value_of('"kosika".drop(3)', "Text"), "ika")
        self.assertEqual(value_of('"kosika".drop(99)', "Text"), "")
        self.assertEqual(value_of("[1, 2, 3].take(2)", "Number[]"), [1, 2])
        self.assertEqual(value_of("[1, 2, 3].drop(1)", "Number[]"), [2, 3])
        self.assertEqual(value_of("[1, 2, 3].take(0)", "Number[]"), [])

    def test_a_count_must_be_a_whole_number_that_is_not_negative(self) -> None:
        for expression in ('"abc".take(-1)', '"abc".drop(1.5)'):
            with self.subTest(expression=expression):
                with self.assertRaisesRegex(RuntimeError, "whole number"):
                    value_of(expression, "Text")

    def test_words_leaves_out_the_empty_parts(self) -> None:
        self.assertEqual(
            value_of('"  read   notes/plan.md\\n now ".words()', "Text[]"),
            ["read", "notes/plan.md", "now"],
        )
        self.assertEqual(value_of('"   ".words()', "Text[]"), [])

    def test_replace_changes_every_one(self) -> None:
        self.assertEqual(
            value_of('"a\\\\b\\\\c".replace("\\\\", "/")', "Text"), "a/b/c"
        )
        self.assertEqual(value_of('"a?b?".replace(old: "?", new: "")', "Text"), "ab")
        with self.assertRaisesRegex(RuntimeError, "non-empty"):
            value_of('"abc".replace("", "x")', "Text")

    def test_trim_marks_forgives_the_ends_and_nothing_else(self) -> None:
        self.assertEqual(
            value_of('" ...\\"Due on the 1st!\\" ".trim_marks(".,!\\"")', "Text"),
            "Due on the 1st",
        )
        self.assertEqual(value_of('"a.b.".trim_marks(".")', "Text"), "a.b")
        self.assertEqual(value_of('"...".trim_marks(".")', "Text"), "")

    def test_is_empty(self) -> None:
        setup = "let none: Text[] = []"
        self.assertTrue(value_of('"".is_empty() and none.is_empty()', "Bool", setup))
        self.assertFalse(value_of('" ".is_empty() or [0].is_empty()', "Bool"))

    def test_the_checker_knows_them(self) -> None:
        for expression in ('"abc".take("1")', "5.take(1)", '"abc".word()'):
            with self.subTest(expression=expression), self.assertRaises(CheckError):
                value_of(expression, "Text")


if __name__ == "__main__":
    unittest.main()
