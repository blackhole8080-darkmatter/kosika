"""What needs a Python parser to tell about a file a plan would write.

What can be told by reading lines is checked in Kosika, by the coder's own
program; see `tests/test_plan_check.py` and the coder's tests.
"""

from __future__ import annotations

import unittest

from kosika.code_checks import plan_code_problems, undefined_names
from kosika.skills import plan_problems

CALC = '''def add(a, b):
    """Return a plus b."""
    return a + b


def double(n):
    return n * 2
'''
TEST = """from calc import add, double


def test_add():
    assert add(2, 3) == 5
"""


def check(files: dict[str, str], now: dict[str, str] | None = None) -> list[str]:
    return plan_code_problems(files, (now or {}).get)


class CaughtTests(unittest.TestCase):
    """Mistakes the coder was seen to make on a real model."""

    def test_a_test_that_forgets_its_import_is_caught(self) -> None:
        found = check(
            {
                "greet.py": "def greet(name):\n    return f'Hello, {name}!'\n",
                "test_greet.py": "def test_greet():\n    assert greet('A') == 'Hello, A!'\n",
            }
        )
        self.assertEqual(
            found,
            [
                "test_greet.py: it uses greet, which is not defined or imported in the file"
            ],
        )

    def test_importing_what_the_other_file_will_not_have_is_caught(self) -> None:
        found = check(
            {
                "calc.py": "def subtract(a, b):\n    return a - b\n",
                "test_calc.py": TEST.replace("add, double", "add, double, subtract"),
            },
            {"calc.py": CALC, "test_calc.py": TEST},
        )
        self.assertEqual(
            found,
            [
                "test_calc.py: it imports add and double from calc, but calc.py does not define them"
            ],
        )

    def test_an_import_from_a_file_the_plan_leaves_alone_is_checked_against_it(
        self,
    ) -> None:
        test = TEST.replace("add, double", "add, halve")
        found = check({"test_calc.py": test}, {"calc.py": CALC})
        self.assertEqual(
            found,
            [
                "test_calc.py: it imports halve from calc, but calc.py does not define it"
            ],
        )
        self.assertEqual(check({"test_calc.py": TEST}, {"calc.py": CALC}), [])

    def test_what_is_not_python_is_said_with_its_line(self) -> None:
        found = check({"calc.py": "def f(:\n    pass\n"})
        self.assertEqual(len(found), 1)
        self.assertTrue(
            found[0].startswith("calc.py: it is not valid Python at line 1"), found[0]
        )

    def test_a_name_never_defined_is_caught(self) -> None:
        found = check(
            {
                "calc.py": "from typing import Generic\n\n\nclass Calculator(Generic[T]):\n    pass\n"
            }
        )
        self.assertEqual(
            found, ["calc.py: it uses T, which is not defined or imported in the file"]
        )


class QuietTests(unittest.TestCase):
    """Sound code must not be complained about."""

    def test_ordinary_python_raises_nothing(self) -> None:
        text = """import os
from pathlib import Path


class Shape:
    sides = 0

    def area(self) -> float:
        return float(self.sides)


def total(items, *extra, scale=1, **named):
    found = [item * scale for item in items if item]
    for at, value in enumerate(extra):
        found.append(value + at)
    try:
        with open(os.devnull) as handle:
            handle.read()
    except OSError as error:
        print(error, named)
    square = lambda x: x * x  # noqa: E731
    return sum(map(square, found)), Path(".")


if __name__ == "__main__":
    print(total([1, 2]), Shape().area())
"""
        self.assertEqual(undefined_names(text), [])
        self.assertEqual(check({"shapes.py": text}), [])

    def test_a_star_import_is_not_judged(self) -> None:
        self.assertEqual(
            undefined_names("from calc import *\n\nprint(add(1, 2))\n"), []
        )

    def test_other_files_are_left_alone(self) -> None:
        self.assertEqual(check({"notes.md": "def f(:"}), [])

    def test_imports_from_outside_the_project_are_not_judged(self) -> None:
        self.assertEqual(
            check({"a.py": "from os.path import join\n\nprint(join('a', 'b'))\n"}), []
        )

    def test_what_a_file_used_to_hold_is_not_this_modules_business(self) -> None:
        # Losing a function is told by the agent's own check, in Kosika.
        new = "def subtract(a, b):\n    return a - b\n"
        self.assertEqual(check({"calc.py": new}, {"calc.py": CALC}), [])

    def test_the_plan_check_uses_them(self) -> None:
        actions = [("write_file", {"path": "t.py", "text": "print(nowhere)\n"})]
        found = plan_problems(None, "write t.py", actions)
        self.assertEqual(
            found,
            ["t.py: it uses nowhere, which is not defined or imported in the file"],
        )


if __name__ == "__main__":
    unittest.main()
