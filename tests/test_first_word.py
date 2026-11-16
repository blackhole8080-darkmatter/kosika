"""The Text method first_word, tested on the examples its request gave."""

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
        self.assertEqual(value_of('"hello big world".first_word()', "Text"), "hello")
        self.assertEqual(value_of('"  spaced out".first_word()', "Text"), "spaced")
        self.assertEqual(value_of('"".first_word()', "Text"), "")


if __name__ == "__main__":
    unittest.main()
