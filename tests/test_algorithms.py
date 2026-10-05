"""Built-in sorting, aggregation, and grouping methods."""

from __future__ import annotations

import copy
import unittest
from typing import Any

from kosika.builtin_methods import call, resolve_for_value
from kosika.checker import CheckError, check
from kosika.parser import parse
from kosika.runtime import run

SOURCE = """
record Order { customer: Text city: Text total: Number }
record In { orders: Order[] scores: Number[] words: Text[] }
record Out {
  by_total: Text[]
  by_name: Text[]
  revenue: Number
  top: Text
  cheapest: Text
  cities: Text[]
  lisbon: Number
  sorted: Number[]
  words: Text[]
  stats: Number[]
  distinct: Text[]
  none: Number?
}

fn build(input: In) -> Out {
  let groups: Order[]{} = input.orders.group_by(field: "city")
  let empty: Number[] = []
  return {
    by_total: for o in input.orders.sort_by(field: "total") { o.customer },
    by_name: for o in input.orders.sort_by(field: "customer").reverse() { o.customer },
    revenue: input.orders.sum_by(field: "total"),
    top: input.orders.max_by(field: "total").value().customer,
    cheapest: input.orders.min_by(field: "total").value().customer,
    cities: groups.keys(),
    lisbon: groups.get(key: "Lisbon").or(default: []).length(),
    sorted: input.scores.sort().reverse(),
    words: input.words.sort(),
    stats: [
      input.scores.sum(),
      input.scores.min().value(),
      input.scores.max().value(),
      input.scores.average().value()
    ],
    distinct: input.words.unique(),
    none: empty.max()
  }
}

workflow main(input: In) -> Out { out = build(input) }
"""

INPUTS: dict[str, Any] = {
    "input": {
        "orders": [
            {"customer": "Ada", "city": "Lisbon", "total": 30},
            {"customer": "Bo", "city": "Oslo", "total": 12},
            {"customer": "Cy", "city": "Lisbon", "total": 99},
        ],
        "scores": [3, 9, 1, 7],
        "words": ["pear", "apple", "pear", "fig"],
    }
}


def rejected(old: str, new: str) -> str:
    try:
        check(parse(SOURCE.replace(old, new)))
    except CheckError as exc:
        return str(exc)
    raise AssertionError("expected the program to be rejected")


class AlgorithmTests(unittest.TestCase):
    def test_sorting_aggregating_and_grouping(self) -> None:
        program = parse(SOURCE)
        check(program)
        inputs = copy.deepcopy(INPUTS)
        self.assertEqual(
            run(program, "main", None, inputs=inputs),
            {
                "by_total": ["Bo", "Ada", "Cy"],
                "by_name": ["Cy", "Bo", "Ada"],
                "revenue": 141,
                "top": "Cy",
                "cheapest": "Bo",
                "cities": ["Lisbon", "Oslo"],
                "lisbon": 2,
                "sorted": [9, 7, 3, 1],
                "words": ["apple", "fig", "pear", "pear"],
                "stats": [20, 1, 9, 5.0],
                "distinct": ["pear", "apple", "fig"],
                "none": None,
            },
        )
        self.assertEqual(inputs, INPUTS, "inputs are never changed in place")

    def test_empty_arrays_give_neutral_or_absent_results(self) -> None:
        inputs = {"input": {"orders": [], "scores": [], "words": []}}
        source = SOURCE.replace(
            'input.orders.max_by(field: "total").value().customer',
            '(for o in input.orders { o.customer }).first().or(default: "nobody")',
        )
        source = source.replace(
            'input.orders.min_by(field: "total").value().customer',
            'if input.orders.min_by(field: "total").exists() { "some" } else { "none" }',
        )
        for method in ("min", "max", "average"):
            source = source.replace(
                f"input.scores.{method}().value()",
                f"input.scores.{method}().or(default: 0)",
            )
        program = parse(source)
        check(program)
        result = run(program, "main", None, inputs=inputs)
        self.assertEqual(result["revenue"], 0)
        self.assertEqual(result["stats"], [0, 0, 0, 0])
        self.assertEqual((result["top"], result["cheapest"]), ("nobody", "none"))
        self.assertEqual((result["cities"], result["sorted"]), ([], []))

    def test_sorting_is_stable_and_ties_keep_the_first(self) -> None:
        rows = [
            {"name": "b", "rank": 1},
            {"name": "a", "rank": 2},
            {"name": "c", "rank": 1},
        ]
        by_rank = call(resolve_for_value(rows, "sort_by"), rows, {"field": "rank"})
        self.assertEqual([row["name"] for row in by_rank], ["b", "c", "a"])
        smallest = call(resolve_for_value(rows, "min_by"), rows, {"field": "rank"})
        self.assertEqual(smallest["name"], "b")

    def test_unique_keeps_first_seen_order_and_bool_apart_from_number(self) -> None:
        items = [1, True, 1.0, 2, True]
        self.assertEqual(
            call(resolve_for_value(items, "unique"), items, {}), [1, True, 2]
        )

    def test_field_names_are_checked_against_the_record(self) -> None:
        cases = {
            (
                'sort_by(field: "total")',
                'sort_by(field: "totl")',
            ): "Did you mean 'total'?",
            ('sum_by(field: "total")', 'sum_by(field: "city")'): "expects 'Number'",
            ('group_by(field: "city")', 'group_by(field: "total")'): "expects 'Text'",
            (
                'max_by(field: "total")',
                "max_by(field: input.words.first().value())",
            ): "write the field name in quotes",
            ("input.scores.sort()", 'input.scores.sort_by(field: "x")'): (
                "needs an array of records"
            ),
            ('sum_by(field: "total")', 'sum_by(by: "total")'): "takes (field: Text)",
        }
        for (old, new), message in cases.items():
            with self.subTest(new=new):
                self.assertIn(message, rejected(old, new))

    def test_methods_are_limited_to_types_they_make_sense_for(self) -> None:
        cases = {
            ("input.words.sort()", "input.orders.sort()"): "no method 'sort'",
            ("input.scores.sum()", "input.words.sum()"): "no method 'sum'",
            (
                "distinct: input.words.unique()",
                "distinct: for o in input.orders.unique() { o.city }",
            ): "unique works on arrays of Text, Number, or Bool",
        }
        for (old, new), message in cases.items():
            with self.subTest(new=new):
                self.assertIn(message, rejected(old, new))


if __name__ == "__main__":
    unittest.main()
