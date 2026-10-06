"""An optional proved present by a test can be used as its plain value."""

from __future__ import annotations

import unittest
from typing import Any

from kosika.checker import CheckError, check
from kosika.parser import parse
from kosika.runtime import run

HEAD = """
record In { text: Text values: Number[] }
record Out { n: Number }
"""


def program(body: str) -> str:
    return (
        HEAD
        + "fn work(input: In) -> Out {\n"
        + body
        + "\n}\n"
        + "workflow main(input: In) -> Out {\n  out = work(input)\n}\n"
    )


def result(body: str, text: str = "7", values: list[float] | None = None) -> Any:
    checked = parse(program(body))
    check(checked)
    return run(
        checked,
        "main",
        None,
        inputs={"input": {"text": text, "values": values or []}},
    )


def rejected(body: str) -> str:
    with unittest.TestCase().assertRaises(CheckError) as caught:
        check(parse(program(body)))
    return str(caught.exception)


class BranchTests(unittest.TestCase):
    def test_the_true_branch_uses_the_value(self) -> None:
        body = """
  let n = input.text.to_number()
  return {n: if n.exists() { n + 1 } else { 0 }}"""
        self.assertEqual(result(body), {"n": 8})
        self.assertEqual(result(body, text="x"), {"n": 0})

    def test_not_narrows_the_else_branch(self) -> None:
        body = """
  let n = input.text.to_number()
  return {n: if not n.exists() { 0 } else { n * 2 }}"""
        self.assertEqual(result(body), {"n": 14})

    def test_and_narrows_both_names(self) -> None:
        body = """
  let a = input.text.to_number()
  let b = input.values.first()
  return {n: if a.exists() and b.exists() { a + b } else { 0 }}"""
        self.assertEqual(result(body, values=[3]), {"n": 10})
        self.assertEqual(result(body), {"n": 0})

    def test_the_older_spelling_still_checks(self) -> None:
        body = """
  let n = input.text.to_number()
  return {n: if n.exists() { n.value() + n.or(0) } else { 0 }}"""
        self.assertEqual(result(body), {"n": 14})

    def test_the_other_branch_is_not_narrowed(self) -> None:
        message = rejected("""
  let n = input.text.to_number()
  return {n: if n.exists() { 0 } else { n + 1 }}""")
        self.assertIn("Number?", message)

    def test_or_does_not_narrow_the_true_branch(self) -> None:
        message = rejected("""
  let a = input.text.to_number()
  let b = input.values.first()
  return {n: if a.exists() or b.exists() { a + 1 } else { 0 }}""")
        self.assertIn("Number?", message)

    def test_a_var_is_never_narrowed(self) -> None:
        message = rejected("""
  var n = input.text.to_number()
  return {n: if n.exists() { n + 1 } else { 0 }}""")
        self.assertIn("Number?", message)


class GuardTests(unittest.TestCase):
    def test_an_early_return_narrows_the_rest_of_the_function(self) -> None:
        body = """
  let n = input.text.to_number()
  if not n.exists() { return {n: -1} }
  let doubled = n * 2
  return {n: doubled}"""
        self.assertEqual(result(body), {"n": 14})
        self.assertEqual(result(body, text="x"), {"n": -1})

    def test_a_guard_that_does_not_leave_narrows_nothing(self) -> None:
        message = rejected("""
  let n = input.text.to_number()
  var seen = 0
  if not n.exists() { seen = 1 }
  return {n: n + seen}""")
        self.assertIn("Number?", message)

    def test_continue_narrows_the_rest_of_a_loop_body(self) -> None:
        body = """
  var total = 0
  for part in input.text.split(",") {
    let n = part.to_number()
    if not n.exists() { continue }
    total = total + n
  }
  return {n: total}"""
        self.assertEqual(result(body, text="1,x,5"), {"n": 6})

    def test_where_narrows_the_loop_variable(self) -> None:
        body = """
  let parsed = for part in input.text.split(",") { part.to_number() }
  let kept = for n in parsed where n.exists() { n * 10 }
  return {n: kept.sum()}"""
        self.assertEqual(result(body, text="1,x,5"), {"n": 60})


if __name__ == "__main__":
    unittest.main()
