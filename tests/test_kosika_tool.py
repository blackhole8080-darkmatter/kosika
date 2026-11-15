"""The `kosika` tool: a program checks and tests a program given as text."""

from __future__ import annotations

import unittest

from kosika.parser import parse
from kosika.system_tools import (
    bind_kosika_tools,
    bind_system_tools,
    check_source,
    declaration_problem,
)

DECLARED = """
record Code { source: Text }
record Checked { ok: Bool problem: Text tests: Number passed: Number }

tool check_program {
  source system "kosika"
  operation: "check"
  access: observe
  input: Code
  output: Checked
}
"""

GOOD = """
record In { n: Number }
record Out { twice: Number }

fn solve(input: In) -> Out {
  return {twice: input.n * 2}
}

workflow main(input: In) -> Out {
  result = solve(input)
}

test "doubles" {
  a = main({n: 4})
  expect a.twice == 8
}

test "zero" {
  a = main({n: 0})
  expect a.twice == 0
}
"""


def check(source: str) -> dict:
    program = parse(DECLARED)
    return check_source(program.tools[0], {"source": source})


class KosikaToolTests(unittest.TestCase):
    def test_the_declaration_only_looks_and_needs_no_grant(self) -> None:
        program = parse(DECLARED)
        self.assertIsNone(declaration_problem(program, program.tools[0]))
        self.assertEqual(list(bind_kosika_tools(program)), ["check_program"])
        # Bound with nothing granted at all.
        self.assertEqual(
            list(bind_system_tools(program, folder=None)), ["check_program"]
        )
        bad = parse(DECLARED.replace('operation: "check"', 'operation: "run"'))
        self.assertIn(
            "use one of: check, check_files", declaration_problem(bad, bad.tools[0]) or ""
        )

    def test_a_good_program_checks_and_its_tests_pass(self) -> None:
        self.assertEqual(
            check(GOOD), {"ok": True, "problem": "", "tests": 2, "passed": 2}
        )

    def test_a_program_that_does_not_check_says_why_without_the_scratch_path(
        self,
    ) -> None:
        result = check(GOOD.replace("input.n * 2", "input.m * 2"))
        self.assertFalse(result["ok"])
        self.assertIn("m", result["problem"])
        self.assertNotIn("kosika-check-", result["problem"])
        self.assertEqual(result["tests"], 0)
        for broken in ("this is not kosika", "fn (", "def f(): pass"):
            with self.subTest(source=broken):
                self.assertFalse(check(broken)["ok"])

    def test_a_failing_test_is_named_with_its_line(self) -> None:
        result = check(GOOD.replace("expect a.twice == 8", "expect a.twice == 9"))
        self.assertEqual(
            (result["ok"], result["tests"], result["passed"]), (False, 2, 1)
        )
        self.assertIn('Test "doubles" failed at line', result["problem"])

    def test_the_checked_program_reaches_nothing(self) -> None:
        # It may declare tools and models, but its tests can use only what
        # they script, and it can import nothing.
        reaching = (
            GOOD
            + """
record Where { path: Text }
record FileText { text: Text }

tool read_file {
  source system "files"
  operation: "read"
  access: observe
  input: Where
  output: FileText
}

agent hands {
  can: [read_file]
  max_steps: 1
}

workflow peek(w: Where) -> FileText {
  file = execute hands.read_file(w.path)
}

test "tries to read a real file" {
  f = peek({path: "C:/Windows/win.ini"})
  expect f.text.length() > 0
}
"""
        )
        result = check(reaching)
        self.assertFalse(result["ok"])
        self.assertIn("tries to read a real file", result["problem"])
        self.assertFalse(check('import "../secrets.kos"\n' + GOOD)["ok"])

    def test_a_program_that_is_too_large_or_loops_forever_comes_back(self) -> None:
        self.assertIn("larger than 64 KiB", check("// " + "x" * 70000)["problem"])
        endless = GOOD.replace(
            "  return {twice: input.n * 2}",
            "  var i = 0\n  while true { i = i + 1 }\n  return {twice: i}",
        )
        result = check(endless)
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
