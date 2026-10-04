"""A function body runs in the order it is written.

Before this, every ``let``/``var`` ran first and every loop or assignment ran
after, whatever their position. A ``let`` written after a loop silently read
the value from before the loop. These tests pin written order as the only
order, in the parser, the checker, the runtime, and replay.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from kosika.ast import Assignment, ForExpr, LocalBinding
from kosika.checker import CheckError, check
from kosika.modules import load_program
from kosika.parser import parse
from kosika.runtime import replay, run


def _run(source: str, inputs: dict | None = None) -> object:
    program = parse(source)
    check(program)
    return run(program, "main", None, inputs=inputs)


INVOICE = """
record Order { prices: Number[] }
record Invoice { subtotal: Number discount: Number total: Number }

fn bill(order: Order) -> Invoice {
  var subtotal = 0
  for price in order.prices { subtotal = subtotal + price }
  let discount = if subtotal >= 100 { subtotal / 10 } else { 0 }
  return {subtotal: subtotal, discount: discount, total: subtotal - discount}
}

workflow main(order: Order) -> Invoice {
  step total { let invoice: Invoice = bill(order: order) return invoice }
}
"""


class WrittenOrderTests(unittest.TestCase):
    def test_a_let_after_a_loop_reads_what_the_loop_assigned(self) -> None:
        self.assertEqual(
            _run(INVOICE, {"order": {"prices": [60, 50, 40]}}),
            {"subtotal": 150, "discount": 15, "total": 135},
        )

    def test_a_var_after_an_assignment_starts_from_the_assigned_value(self) -> None:
        self.assertEqual(
            _run(
                """
                record R { first: Number second: Number }
                fn go() -> R {
                  var first = 1
                  first = first + 10
                  var second = first * 2
                  first = first + 100
                  return {first: first, second: second}
                }
                workflow main() -> R { step s { let r = go() return r } }
                """
            ),
            {"first": 111, "second": 22},
        )

    def test_two_loops_run_one_after_the_other(self) -> None:
        self.assertEqual(
            _run(
                """
                record R { total: Number above: Number }
                fn go(values: Number[]) -> R {
                  var total = 0
                  for value in values { total = total + value }
                  let mean = total / 4
                  var above = 0
                  for value in values {
                    above = if value > mean { above + 1 } else { above }
                  }
                  return {total: total, above: above}
                }
                workflow main() -> R {
                  step s { let r = go(values: [1, 2, 3, 10]) return r }
                }
                """
            ),
            {"total": 16, "above": 1},
        )

    def test_a_statement_cannot_use_a_local_declared_below_it(self) -> None:
        program = parse(
            """
            record R { n: Number }
            fn go() -> R {
              var n = 0
              n = n + later
              let later = 5
              return {n: n}
            }
            workflow main() -> R { step s { let r = go() return r } }
            """
        )
        with self.assertRaisesRegex(CheckError, "unknown variable 'later'"):
            check(program)

    def test_a_local_cannot_use_a_local_declared_below_it(self) -> None:
        program = parse(
            """
            record R { n: Number }
            fn go() -> R {
              let n = later + 1
              let later = 5
              return {n: n}
            }
            workflow main() -> R { step s { let r = go() return r } }
            """
        )
        with self.assertRaisesRegex(CheckError, "unknown variable 'later'"):
            check(program)

    def test_the_parser_keeps_the_body_in_written_order(self) -> None:
        function = parse(INVOICE).functions[0]
        self.assertEqual(
            [type(item) for item in function.body],
            [LocalBinding, ForExpr, LocalBinding],
        )
        # The two views stay available to tools that only list locals.
        self.assertEqual(
            [item.name for item in function.bindings], ["subtotal", "discount"]
        )
        self.assertEqual([type(item) for item in function.statements], [ForExpr])

    def test_assignments_keep_their_position(self) -> None:
        function = parse(
            """
            fn go() -> Number {
              var n = 1
              n = 2
              let m = n
              return m
            }
            """
        ).functions[0]
        self.assertEqual(
            [type(item) for item in function.body],
            [LocalBinding, Assignment, LocalBinding],
        )

    def test_reordering_a_body_changes_the_program_identity(self) -> None:
        before = parse("fn go() -> Number { var n = 1 n = 2 let m = n return m }")
        after = parse("fn go() -> Number { var n = 1 let m = n n = 2 return m }")
        self.assertNotEqual(asdict(before), asdict(after))

    def test_written_order_replays_identically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "invoice.kos"
            source.write_text(INVOICE, encoding="utf-8")
            trace = Path(directory) / "invoice.jsonl"
            program = load_program(source)
            result = run(
                program, "main", trace, inputs={"order": {"prices": [60, 50, 40]}}
            )
            self.assertEqual(result["discount"], 15)
            self.assertEqual(replay(trace), result)

    def test_a_nested_function_body_keeps_its_order(self) -> None:
        self.assertEqual(
            _run(
                """
                record R { n: Number }
                fn go(base: Number) -> R {
                  let doubled: Number = base * 2
                  fn build(extra: Number) -> R {
                    var n = doubled
                    n = n + extra
                    let final = n + 1
                    return {n: final}
                  }
                  return build(extra: 3)
                }
                workflow main() -> R { step s { let r = go(base: 4) return r } }
                """
            ),
            {"n": 12},
        )


if __name__ == "__main__":
    unittest.main()
