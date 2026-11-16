"""The Text method is_upper_letter, tested on the examples its request gave."""

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
        self.assertEqual(value_of('"Q".is_upper_letter()', "Bool"), True)
        self.assertEqual(value_of('"q".is_upper_letter()', "Bool"), False)
        self.assertEqual(value_of('"AB".is_upper_letter()', "Bool"), False)
        self.assertEqual(value_of('"".is_upper_letter()', "Bool"), False)


if __name__ == "__main__":
    unittest.main()
