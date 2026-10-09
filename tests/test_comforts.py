"""Text with values written into it, the clock, and chance."""

from __future__ import annotations

import json
import random
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika.ambient import bind_ambient_tools, moment
from kosika.ast import program_payload
from kosika.modules import load_program
from kosika.parser import ParseError, parse
from kosika.runtime import run
from kosika.story import build_story, read_trace
from kosika.testing import run_tests
from kosika.trace_replay import replay_trace

FORMAT = """
record Order { item: Text price: Number count: Number }
record Out { text: Text }

fn describe(order: Order) -> Out {
  let total = order.price * order.count
  return {text: f"{order.count} x {order.item} at {order.price} is {total}. {{braces}} {order.item.upper()}"}
}

workflow main(order: Order) -> Out {
  result = describe(order)
}

test "values are written in" {
  a = main({item: "tea", price: 2.5, count: 4})
  expect a.text == "4 x tea at 2.5 is 10. {braces} TEA"
  expect f"plain" == "plain"
  expect f"{1 + 2} and {a.text.length()}" == "3 and 34"
  expect f"" == ""
}
"""

AMBIENT = """
record Msg { text: Text }
record Out { text: Text }

fn build(today: Moment, roll: RandomNumber) -> Out {
  return {text: f"{today.weekday} {today.date} {today.time}, rolled {roll.value}"}
}

workflow main(msg: Msg) -> Out {
  today = now
  roll  = random 1 to 6
  out   = build(today, roll)
}

test "scripted" {
  answers clock_now: [{date: "2026-10-09", time: "14:05", weekday: "Friday", year: 2026, month: 10, day: 9, hour: 14, minute: 5, epoch: 1791554700}]
  answers random_number: [{value: 4}]
  a = main({text: "hi"})
  expect a.text == "Friday 2026-10-09 14:05, rolled 4"
}
"""


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.folder = Path(self._dir.name)

    def program(self, source: str) -> Any:
        (self.folder / "main.kos").write_text(source, encoding="utf-8")
        return load_program(self.folder / "main.kos")


class FormatTests(Base):
    def test_values_are_written_into_text(self) -> None:
        results = run_tests(self.program(FORMAT))
        self.assertEqual([(r.passed, r.message) for r in results], [(True, "")])

    def test_it_is_only_another_way_to_write_joining(self) -> None:
        with_f = parse('fn a(n: Number) -> Text {\n  return f"n is {n}!"\n}')
        by_hand = parse('fn a(n: Number) -> Text {\n  return "n is " + n + "!"\n}')
        self.assertEqual(program_payload(with_f), program_payload(by_hand))

    def test_a_name_called_f_is_still_a_name(self) -> None:
        source = 'fn a(f: Text) -> Text {\n  return f + "x"\n}\nfn b(f: Text) -> Text {\n  return a(f)\n}'
        parse(source)

    def test_a_badly_formed_text_is_refused_where_it_is_written(self) -> None:
        for bad in ('f"open {name"', 'f"empty {}"', 'f"close } alone"', 'f"two {a b}"'):
            with self.subTest(text=bad), self.assertRaises(ParseError):
                parse(f"fn a(name: Text) -> Text {{\n  return {bad}\n}}")

    def test_what_is_written_in_must_be_text_or_a_number(self) -> None:
        with self.assertRaises(ValueError):
            self.program(
                "record R { xs: Number[] }\nrecord O { t: Text }\n"
                'fn a(r: R) -> O {\n  return {t: f"list {r.xs}"}\n}\n'
                "workflow main(r: R) -> O {\n  o = a(r)\n}\n"
            )


class AmbientTests(Base):
    def go(self, **extra: Any) -> Any:
        return run(
            self.program(AMBIENT),
            "main",
            self.folder / "run.jsonl",
            inputs={"msg": {"text": "hi"}},
            **extra,
        )

    def test_a_test_scripts_the_time_and_the_dice(self) -> None:
        self.assertTrue(all(r.passed for r in run_tests(self.program(AMBIENT))))

    def test_a_real_run_needs_no_grant_and_replays_to_the_same_moment(self) -> None:
        result = self.go()
        self.assertRegex(
            result["text"], r"^\w+day \d{4}-\d\d-\d\d \d\d:\d\d, rolled [1-6]$"
        )
        self.assertEqual(replay_trace(self.folder / "run.jsonl"), result)
        steps = build_story(read_trace(self.folder / "run.jsonl"))["steps"]
        self.assertEqual(steps[0]["did"], "looked at the clock")
        self.assertEqual(steps[1]["did"], "drew a number from 1 to 6")

    def test_the_caller_may_supply_its_own_clock_and_dice(self) -> None:
        program = self.program(AMBIENT)
        tools = bind_ambient_tools(
            program, clock=lambda: 1791554700.0, chance=random.Random(7)
        )
        first = self.go(tool_bindings=tools)
        again = self.go(
            tool_bindings=bind_ambient_tools(
                program, clock=lambda: 1791554700.0, chance=random.Random(7)
            )
        )
        self.assertEqual(first, again)
        self.assertIn("2026-10-", first["text"])

    def test_a_moment_has_every_part(self) -> None:
        at = moment(0)
        self.assertEqual(
            sorted(at),
            sorted(
                [
                    "date",
                    "time",
                    "weekday",
                    "year",
                    "month",
                    "day",
                    "hour",
                    "minute",
                    "epoch",
                ]
            ),
        )
        self.assertEqual(at["epoch"], 0)
        self.assertIn(at["weekday"], ("Wednesday", "Thursday"))

    def test_random_wants_whole_numbers_in_order(self) -> None:
        for change in ("random 6 to 1", "random 1.5 to 6"):
            with (
                self.subTest(change=change),
                self.assertRaises((RuntimeError, ValueError)),
            ):
                (self.folder / "main.kos").write_text(
                    AMBIENT.replace("random 1 to 6", change), encoding="utf-8"
                )
                run(
                    load_program(self.folder / "main.kos"),
                    "main",
                    None,
                    inputs={"msg": {"text": "hi"}},
                )

    def test_a_program_without_them_is_unchanged(self) -> None:
        plain = parse("record A { n: Number }\nfn main(a: A) -> A {\n  return a\n}")
        text = json.dumps(program_payload(plain))
        self.assertNotIn("world", text)
        self.assertNotIn("Moment", text)

    def test_names_called_now_and_random_are_still_names(self) -> None:
        parse(
            "record A { n: Number }\n"
            "fn pick(now: Number, random: Number) -> A {\n  return {n: now + random}\n}\n"
            "workflow main(a: A) -> A {\n  out = pick(a.n, a.n)\n}"
        )


if __name__ == "__main__":
    unittest.main()
