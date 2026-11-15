"""Methods the assistant's own program wished it had: abs, power, is_whole,
index_of, chars, pad_left and pad_right."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika.checker import CheckError
from kosika.modules import load_program
from kosika.runtime import run


def answer(body: str, returns: str = "Number[]") -> Any:
    source = (
        f"record Out {{ values: {returns} }}\n"
        f"fn f() -> Out {{\n  return {{values: {body}}}\n}}\n"
        "workflow main() -> Out { out = f() }\n"
    )
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "main.kos"
        path.write_text(source, encoding="utf-8")
        return run(load_program(path), "main", None, inputs={})["values"]


class NumberTests(unittest.TestCase):
    def test_abs_drops_the_sign(self) -> None:
        self.assertEqual(
            answer("[(0 - 4).abs(), 4.abs(), (0 - 2.5).abs(), 0.abs()]"), [4, 4, 2.5, 0]
        )

    def test_whole_powers_of_whole_numbers_are_exact(self) -> None:
        values = answer("[2.power(10), 7.power(0), 10.power(30), (0 - 3).power(3)]")
        self.assertEqual(values, [1024, 1, 10**30, -27])
        self.assertTrue(all(isinstance(value, int) for value in values))

    def test_other_powers_are_rounded_to_nine_places(self) -> None:
        self.assertEqual(
            answer(
                "[2.power(0 - 1), 9.power(0.5), 2.power(0.5), 1.5.power(2), 4.power(0 - 2)]"
            ),
            [0.5, 3, 1.414213562, 2.25, 0.0625],
        )

    def test_powers_that_cannot_be_held_fail_in_plain_words(self) -> None:
        for body, words in (
            ("[0.power(0 - 1)]", "0 cannot be raised to a negative power"),
            ("[2.power(1001)]", "exponent between -1000 and 1000"),
            ("[(0 - 8).power(0.5)]", "whole exponent when the number is negative"),
            ("[10.5.power(1000)]", "too large to hold"),
        ):
            with self.subTest(body=body), self.assertRaises(Exception) as caught:
                answer(body)
            self.assertIn(words, str(caught.exception))

    def test_is_whole(self) -> None:
        self.assertEqual(
            answer(
                "[3.is_whole(), 3.5.is_whole(), (0 - 2).is_whole(), (6 / 3).is_whole()]",
                "Bool[]",
            ),
            [True, False, True, True],
        )


class PlaceTests(unittest.TestCase):
    def test_where_an_item_is_in_a_list(self) -> None:
        self.assertEqual(
            answer(
                '[["a", "b", "c", "b"].index_of("b").or(0 - 1), ["a"].index_of("z").or(0 - 1), [4, 5].index_of(5).or(0 - 1)]'
            ),
            [1, -1, 1],
        )

    def test_nothing_found_is_absent_rather_than_a_number(self) -> None:
        self.assertEqual(
            answer(
                '[["a"].index_of("z").exists(), ["a"].index_of("a").exists(), "hello".index_of("zz").exists()]',
                "Bool[]",
            ),
            [False, True, False],
        )

    def test_where_a_part_begins_in_a_text(self) -> None:
        self.assertEqual(
            answer(
                '["hello world".index_of("world").or(0 - 1), "hello".index_of("l").or(0 - 1), "hello".index_of("h").or(0 - 1)]'
            ),
            [6, 2, 0],
        )
        with self.assertRaises(Exception) as caught:
            answer('["hello".index_of("").or(0)]')
        self.assertIn("non-empty", str(caught.exception))

    def test_the_item_must_be_of_the_lists_own_kind(self) -> None:
        with self.assertRaises(CheckError):
            answer('[["a"].index_of(1).or(0)]')


class TextTests(unittest.TestCase):
    def test_a_text_as_its_characters(self) -> None:
        self.assertEqual(answer('"abc".chars()', "Text[]"), ["a", "b", "c"])
        self.assertEqual(answer('"".chars()', "Text[]"), [])
        self.assertEqual(answer('["a1b2".chars().length()]'), [4])

    def test_padding_makes_a_text_at_least_that_long(self) -> None:
        self.assertEqual(
            answer(
                '["5".pad_left(2, "0"), "12".pad_left(2, "0"), "123".pad_left(2, "0"), "ab".pad_right(5, "."), "".pad_left(3, "-")]',
                "Text[]",
            ),
            ["05", "12", "123", "ab...", "---"],
        )

    def test_padding_refuses_what_makes_no_sense(self) -> None:
        for body, words in (
            ('["5".pad_left(2, "")]', "exactly one character"),
            ('["5".pad_left(2, "ab")]', "exactly one character"),
            ('["5".pad_right(0 - 1, "0")]', "not negative"),
            ('["5".pad_left(1.5, "0")]', "whole width"),
        ):
            with self.subTest(body=body), self.assertRaises(Exception) as caught:
                answer(body, "Text[]")
            self.assertIn(words, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
