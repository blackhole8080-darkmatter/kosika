"""Writing algorithms: `while`, ranges, `+=`, element assignment, big loops."""

from __future__ import annotations

import unittest
from typing import Any

from kosika.ast import program_payload
from kosika.checker import CheckError, check
from kosika.controls import ExecutionError
from kosika.execution import ExecutionLimits
from kosika.parser import ParseError, parse
from kosika.runtime import run

HEAD = """
record In { values: Number[] n: Number }
record Out { n: Number values: Number[] }
"""


def program(body: str) -> str:
    return (
        HEAD
        + "fn work(input: In) -> Out {\n"
        + body
        + "\n}\n"
        + "workflow main(input: In) -> Out {\n  out = work(input)\n}\n"
    )


def result(body: str, n: float = 0, values: list[float] | None = None, **options: Any):
    checked = parse(program(body))
    check(checked)
    return run(
        checked,
        "main",
        None,
        inputs={"input": {"n": n, "values": values or []}},
        **options,
    )


class WhileTests(unittest.TestCase):
    def test_while_runs_until_its_condition_fails(self) -> None:
        body = """
  var x = 48
  var y = 18
  while y != 0 {
    let t = y
    y = x % y
    x = t
  }
  return {n: x, values: []}"""
        self.assertEqual(result(body)["n"], 6)

    def test_a_false_condition_runs_the_body_zero_times(self) -> None:
        body = """
  var count = 0
  while input.n > 100 { count += 1 }
  return {n: count, values: []}"""
        self.assertEqual(result(body)["n"], 0)

    def test_break_leaves_a_while(self) -> None:
        body = """
  var count = 0
  while true {
    count += 1
    if count == 5 { break }
  }
  return {n: count, values: []}"""
        self.assertEqual(result(body)["n"], 5)

    def test_a_loop_that_never_ends_is_stopped_by_the_run_budget(self) -> None:
        body = """
  var count = 0
  while true { count += 1 }
  return {n: count, values: []}"""
        with self.assertRaises(ExecutionError) as caught:
            result(body, limits=ExecutionLimits(max_loop_iterations=500))
        self.assertEqual(caught.exception.code, "budget")
        self.assertIn("loop iterations budget exceeds 500", str(caught.exception))

    def test_the_condition_must_be_a_bool(self) -> None:
        with self.assertRaises(CheckError):
            check(
                parse(program("  while input.n { break }\n  return {n: 0, values: []}"))
            )


class RangeTests(unittest.TestCase):
    def test_to_includes_the_end_and_until_stops_before_it(self) -> None:
        body = """
  let through = for i in 1 to input.n { i }
  let before = for i in 0 until input.n { i }
  return {n: through.sum(), values: before}"""
        self.assertEqual(result(body, n=4), {"n": 10, "values": [0, 1, 2, 3]})

    def test_an_empty_range_runs_nothing(self) -> None:
        body = "  return {n: 0, values: for i in 5 until 2 { i }}"
        self.assertEqual(result(body)["values"], [])

    def test_the_end_may_be_any_expression_and_where_still_works(self) -> None:
        body = (
            "  return {n: 0, values: for i in 0 until input.values.length() * 2 "
            "where i % 2 == 0 { i }}"
        )
        self.assertEqual(result(body, values=[9, 9])["values"], [0, 2])

    def test_ranges_are_also_plain_methods(self) -> None:
        body = "  return {n: 3.to(5).sum(), values: 0.until(end: 3)}"
        self.assertEqual(result(body), {"n": 12, "values": [0, 1, 2]})

    def test_a_fractional_bound_fails_the_run(self) -> None:
        with self.assertRaisesRegex(Exception, "whole numbers"):
            result("  return {n: 0, values: for i in 0 until input.n { i }}", n=2.5)


class UpdateTests(unittest.TestCase):
    def test_compound_assignment(self) -> None:
        body = """
  var x = 10
  x += 5
  x -= 3
  x *= 4
  x /= 6
  return {n: x, values: []}"""
        self.assertEqual(result(body)["n"], 8)

    def test_element_assignment_gives_the_variable_a_new_array(self) -> None:
        body = """
  var items = input.values
  items[0] = 99
  items[1] += 1
  return {n: input.values[0], values: items}"""
        self.assertEqual(result(body, values=[1, 2, 3]), {"n": 1, "values": [99, 3, 3]})

    def test_a_map_entry_can_be_assigned(self) -> None:
        body = """
  var seen: Number{} = {}
  seen["a"] = 1
  seen["b"] = 2
  return {n: seen.length(), values: seen.values()}"""
        self.assertEqual(result(body), {"n": 2, "values": [1, 2]})

    def test_a_position_outside_the_array_fails_the_run(self) -> None:
        body = """
  var items = input.values
  items[7] = 1
  return {n: 0, values: items}"""
        with self.assertRaisesRegex(Exception, "outside an array of length 2"):
            result(body, values=[1, 2])

    def test_only_a_var_can_be_updated(self) -> None:
        for line in ("x += 1", "x[0] = 1"):
            source = program(
                f"  let x = input.values\n  {line}\n  return {{n: 0, values: []}}"
            )
            with self.assertRaises(CheckError):
                check(parse(source))

    def test_bubble_sort(self) -> None:
        body = """
  var items = input.values
  for i in 0 until items.length() {
    for j in 0 until items.length() - 1 - i {
      if items[j] > items[j + 1] {
        let t = items[j]
        items[j] = items[j + 1]
        items[j + 1] = t
      }
    }
  }
  return {n: 0, values: items}"""
        self.assertEqual(
            result(body, values=[5, 3, 9, 1, 7])["values"], [1, 3, 5, 7, 9]
        )


class LoopSizeTests(unittest.TestCase):
    def test_a_for_loop_is_no_longer_capped_at_128(self) -> None:
        body = """
  var total = 0
  for i in 1 to 5000 { total += i }
  return {n: total, values: []}"""
        self.assertEqual(result(body)["n"], 12502500)

    def test_a_written_limit_is_still_enforced(self) -> None:
        body = "  return {n: 0, values: for i in 0 until 10 limit 5 { i }}"
        with self.assertRaisesRegex(Exception, "limit of 5"):
            result(body)

    def test_repeat_takes_any_number(self) -> None:
        body = """
  var total = 0
  repeat input.n times { total += 2 }
  return {n: total, values: []}"""
        self.assertEqual(result(body, n=700)["n"], 1400)
        with self.assertRaisesRegex(Exception, "whole number"):
            result(body, n=1.5)
        with self.assertRaisesRegex(ParseError, "from 1 to 1000000"):
            parse(program("  repeat 0 times { }\n  return {n: 0, values: []}"))

    def test_loops_without_new_syntax_keep_their_hash_shape(self) -> None:
        body = "  return {n: 0, values: for v in input.values { v }}"
        self.assertNotIn("capped", str(program_payload(parse(program(body)))))


if __name__ == "__main__":
    unittest.main()
