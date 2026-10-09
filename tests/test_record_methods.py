"""Functions that belong to a record: `order.total()`."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika.ast import program_payload
from kosika.checker import CheckError
from kosika.modules import load_program
from kosika.parser import ParseError, parse
from kosika.runtime import run
from kosika.trace_replay import replay_trace

SOURCE = """
record Item {
  name: Text
  price: Number
  count: Number

  fn total(self) -> Number {
    return self.price * self.count
  }

  fn discounted(self, percent: Number) -> Item {
    return {name: self.name, price: self.price * (100 - percent) / 100, count: self.count}
  }

  fn label(self) -> Text {
    return f"{self.count} x {self.name} = {self.total()}"
  }
}

record Basket {
  items: Item[]

  fn total(self) -> Number {
    var sum = 0
    for item in self.items { sum += item.total() }
    return sum
  }

  fn add(self, item: Item) -> Basket {
    return {items: self.items.append(item)}
  }
}

record Out { text: Text total: Number }

fn solve(basket: Basket) -> Out {
  let more = basket.add({name: "milk", price: 2, count: 1})
  let cheap = more.items[0].discounted(percent: 50)
  return {text: cheap.label(), total: more.total()}
}

workflow main(basket: Basket) -> Out {
  result = solve(basket)
}
"""


class RecordMethodTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.folder = Path(self._dir.name)

    def program(self, source: str = SOURCE) -> Any:
        (self.folder / "main.kos").write_text(source, encoding="utf-8")
        return load_program(self.folder / "main.kos")

    def test_a_record_answers_to_its_own_functions(self) -> None:
        result = run(
            self.program(),
            "main",
            self.folder / "run.jsonl",
            inputs={"basket": {"items": [{"name": "tea", "price": 4, "count": 3}]}},
        )
        # Two records each have a `total`; each call reached the right one.
        self.assertEqual(result, {"text": "3 x tea = 6", "total": 14})
        self.assertEqual(replay_trace(self.folder / "run.jsonl"), result)

    def test_a_method_is_an_ordinary_function_with_the_record_first(self) -> None:
        program = parse(SOURCE)
        item = next(record for record in program.records if record.name == "Item")
        self.assertEqual(item.methods, ("total", "discounted", "label"))
        total = next(f for f in program.functions if f.name == "Item$total")
        self.assertEqual(
            [(p.name, p.type_name) for p in total.parameters], [("self", "Item")]
        )

    def test_built_in_methods_still_work_beside_them(self) -> None:
        source = SOURCE.replace(
            "    for item in self.items { sum += item.total() }",
            "    for item in self.items { sum += item.total() }\n"
            "    if self.items.length() == 0 { return 0 }",
        )
        self.program(source)

    def test_arguments_are_checked_like_any_call(self) -> None:
        for change in (
            ("discounted(percent: 50)", 'discounted(percent: "half")'),
            ("discounted(percent: 50)", "discounted()"),
            ("more.total()", "more.totals()"),
            ("cheap.label()", "cheap.label(1)"),
        ):
            with self.subTest(change=change[1]), self.assertRaises(CheckError):
                self.program(SOURCE.replace(*change))

    def test_names_a_method_may_not_have(self) -> None:
        for change, reason in (
            (
                ("fn label(self) -> Text {", "fn name(self) -> Text {"),
                "same name as one of its fields",
            ),
            (
                ("fn label(self) -> Text {", "fn length(self) -> Text {"),
                "these belong to maps",
            ),
            (
                ("fn label(self) -> Text {", "fn total(self) -> Text {"),
                "declared more than once",
            ),
        ):
            with (
                self.subTest(change=change[1]),
                self.assertRaises(CheckError) as caught,
            ):
                self.program(SOURCE.replace(*change).replace("cheap.label()", '"x"'))
            self.assertIn(reason, str(caught.exception))

    def test_two_records_with_the_same_fields_cannot_share_a_method_name(self) -> None:
        source = (
            'record A { n: Number\n  fn show(self) -> Text { return "a" } }\n'
            'record B { n: Number\n  fn show(self) -> Text { return "b" } }\n'
            "fn main(a: A) -> A {\n  return a\n}\n"
        )
        with self.assertRaisesRegex(CheckError, "exactly the same fields"):
            self.program(source)

    def test_a_method_must_take_self(self) -> None:
        with self.assertRaises(ParseError):
            parse('record A { n: Number\n  fn show() -> Text { return "a" } }')

    def test_a_record_is_copied_with_some_fields_changed(self) -> None:
        source = (
            "record Task { title: Text done: Bool spent: Number\n"
            "  fn finished(self) -> Task { return {..self, done: true} }\n"
            "  fn logged(self, minutes: Number) -> Task { return {..self, spent: self.spent + minutes} }\n"
            "}\n"
            "record Out { before: Task after: Task same: Task }\n"
            "fn solve(task: Task) -> Out {\n"
            "  return {before: task, after: task.logged(20).finished(), same: {..task}}\n"
            "}\n"
            "workflow main(task: Task) -> Out {\n  out = solve(task)\n}\n"
        )
        result = run(
            self.program(source),
            "main",
            self.folder / "run.jsonl",
            inputs={"task": {"title": "tea", "done": False, "spent": 5}},
        )
        # The record that was copied is not changed.
        self.assertEqual(result["before"], {"title": "tea", "done": False, "spent": 5})
        self.assertEqual(result["after"], {"title": "tea", "done": True, "spent": 25})
        self.assertEqual(result["same"], result["before"])
        self.assertEqual(replay_trace(self.folder / "run.jsonl"), result)

    def test_a_record_update_must_fit_the_record(self) -> None:
        base = (
            "record Task { title: Text done: Bool }\n"
            "fn change(task: Task, n: Number) -> Task {\n  return CHANGE\n}\n"
            "workflow main(task: Task) -> Task {\n  out = change(task, 1)\n}\n"
        )
        for change, reason in (
            ("{..task, finished: true}", "not a field of 'Task'"),
            ("{..task, done: 1}", "expects 'Bool'"),
            ("{..task, done: true, done: false}", "more than once"),
            ("{..n, done: true}", "needs a record"),
        ):
            with self.subTest(change=change), self.assertRaises(CheckError) as caught:
                self.program(base.replace("CHANGE", change))
            self.assertIn(reason, str(caught.exception))

    def test_a_record_without_methods_hashes_as_before(self) -> None:
        plain = parse("record A { n: Number }\nfn main(a: A) -> A {\n  return a\n}")
        self.assertNotIn("methods", json.dumps(program_payload(plain)))


if __name__ == "__main__":
    unittest.main()
