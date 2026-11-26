"""The Text method is_palindrome, tested on the examples its request gave."""

from __future__ import annotations

import unittest
from typing import Any

from kosika.checker import check
from kosika.parser import parse
from kosika.runtime import run


def value_of(expression: str, type_name: str) -> Any:
    source = (
        f"record Out {{ value: {type_name} }}\n"
        f"fn f() -> Out {{\nreturn {{value: {expression}}}\n}}\n"
        "workflow main() -> Out { out = f() }\n"
    )
    program = parse(source)
    check(program)
    return run(program, "main", None, inputs={})["value"]


class ExampleTests(unittest.TestCase):
    def test_the_examples_give_what_the_request_says(self) -> None:
        self.assertEqual(value_of('"Racecar".is_palindrome()', "Bool"), True)
        self.assertEqual(value_of('"A man, a plan, a canal: Panama".is_palindrome()', "Bool"), True)
        self.assertEqual(value_of('"Kosika".is_palindrome()', "Bool"), False)
        self.assertEqual(value_of('"".is_palindrome()', "Bool"), True)


if __name__ == "__main__":
    unittest.main()
