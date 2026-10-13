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


GOOD_KOS = 'fn f(n: Number) -> Number {\n  return n + 1\n}\n\ntest "f adds one" {\n  expect f(1) == 2\n}\n'


class KosikaTests(unittest.TestCase):
    """A Kosika file must check, and its tests must pass, before it is written."""

    def test_a_sound_file_raises_nothing(self) -> None:
        self.assertEqual(check({"a.kos": GOOD_KOS}), [])

    def test_a_failing_test_is_named_with_its_line(self) -> None:
        found = check({"a.kos": GOOD_KOS.replace("== 2", "== 3")})
        self.assertEqual(len(found), 1)
        self.assertTrue(
            found[0].startswith('a.kos: its test "f adds one" fails at line 6:'),
            found[0],
        )

    def test_what_is_not_kosika_is_said_in_the_checkers_words(self) -> None:
        habit = check({"a.kos": GOOD_KOS.replace("n + 1", "len(n)")})
        python = check({"lib/a.kos": "def f(n):\n    return n\n"})
        self.assertTrue(habit[0].startswith("a.kos: it is not valid Kosika:"), habit[0])
        self.assertIn("len", habit[0])
        self.assertEqual(
            python,
            ["lib/a.kos: it is not valid Kosika: Unexpected declaration 'def' at 1:1"],
        )

    def test_one_planned_file_may_import_another(self) -> None:
        files = {
            "lib.kos": "export [double]\n\nfn double(n: Number) -> Number {\n  return n * 2\n}\n",
            "main.kos": 'import "lib.kos"\n\ntest "it doubles" {\n  expect double(2) == 4\n}\n',
        }
        self.assertEqual(check(files), [])
        self.assertTrue(
            check(
                {"main.kos": files["main.kos"].replace("== 4", "== 5")}
                | {"lib.kos": files["lib.kos"]}
            )
        )

    def test_a_file_that_imports_one_not_in_the_plan_is_not_judged(self) -> None:
        self.assertEqual(
            check(
                {
                    "main.kos": 'import "elsewhere.kos"\n\ntest "x" {\n  expect 1 == 2\n}\n'
                }
            ),
            [],
        )

    def test_python_and_kosika_are_both_read_in_one_plan(self) -> None:
        found = check(
            {"t.py": "print(nowhere)\n", "a.kos": GOOD_KOS.replace("== 2", "== 3")}
        )
        self.assertEqual(len(found), 2)

    def test_a_program_may_ask_for_this_reading_as_a_tool(self) -> None:
        import tempfile
        from pathlib import Path

        from kosika.modules import load_program
        from kosika.runtime import run
        from kosika.system_tools import bind_system_tools

        source = (
            "record Planned { path: Text text: Text before: Text exists: Bool }\n"
            "record Batch { files: Planned[] }\n"
            "record Engine { problems: Text[] }\n"
            'tool code_check {\n  source system "kosika"\n  operation: "check_files"\n'
            "  access: observe\n  input: Batch\n  output: Engine\n}\n"
            "agent helper {\n  can: [code_check]\n  max_steps: 1\n}\n"
            "workflow main(batch: Batch) -> Engine {\n  found = execute helper.code_check(batch.files)\n}\n"
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "main.kos"
            path.write_text(source, encoding="utf-8")
            program = load_program(path)
            files = [
                {
                    "path": "a.kos",
                    "text": GOOD_KOS.replace("== 2", "== 3"),
                    "before": "",
                    "exists": False,
                },
                {"path": "t.py", "text": "def f(:\n", "before": "", "exists": False},
            ]
            result = run(
                program,
                "main",
                None,
                inputs={"batch": {"files": files}},
                tool_bindings=bind_system_tools(program, folder=None),
            )
        self.assertEqual(len(result["problems"]), 2)
        self.assertTrue(
            result["problems"][0].startswith("t.py: it is not valid Python")
        )
        self.assertTrue(
            result["problems"][1].startswith('a.kos: its test "f adds one" fails')
        )
