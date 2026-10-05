"""Written-order function bodies, `else if`, `%`, built-in methods, and anchors."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika.builtin_methods import METHODS, MethodError, resolve
from kosika.checker import CheckError, check
from kosika.development import check_project
from kosika.parser import ParseError, parse
from kosika.runtime import replay, run

HEADER = "record Out { value: Number text: Text flag: Bool items: Text[] }\n"
MAIN = "workflow main() -> Out { step go { let out: Out = f() return out } }\n"


def result(body: str, *, extra: str = "") -> dict[str, Any]:
    """Run `fn f() -> Out` whose body is given, filling unset fields."""

    program = parse(HEADER + extra + f"fn f() -> Out {{\n{body}\n}}\n" + MAIN)
    check(program)
    return run(program, "main", None, inputs={})


def out(**fields: str) -> str:
    values = {"value": "0", "text": '""', "flag": "false", "items": '["x"]'}
    values.update(fields)
    return (
        "return {" + ", ".join(f"{key}: {value}" for key, value in values.items()) + "}"
    )


class WrittenOrderTests(unittest.TestCase):
    def test_a_let_after_a_loop_sees_the_loop_result(self) -> None:
        body = (
            "let values: Number[] = [10, 20]\n"
            "var total = 0\n"
            "for item in values { total = total + item }\n"
            "let tenth: Number = total / 10\n" + out(value="tenth")
        )
        self.assertEqual(result(body)["value"], 3)

    def test_a_var_declared_after_a_statement_starts_from_current_values(self) -> None:
        body = "var a = 1\na = a + 4\nvar b = a * 2\nb = b + 1\n" + out(value="b")
        self.assertEqual(result(body)["value"], 11)

    def test_a_statement_cannot_use_a_name_declared_below_it(self) -> None:
        body = "var a = 1\na = a + later\nlet later: Number = 2\n" + out(value="a")
        with self.assertRaisesRegex(CheckError, "unknown variable 'later'"):
            result(body)

    def test_declarations_first_bodies_parse_exactly_as_before(self) -> None:
        program = parse(
            HEADER
            + "fn f() -> Out {\nlet a: Number = 1\nvar b = 2\nb = b + a\n"
            + out(value="b")
            + "\n}\n"
            + MAIN
        )
        self.assertEqual(program.functions[0].order, ())
        mixed = parse(
            HEADER
            + "fn f() -> Out {\nvar b = 2\nb = b + 1\nlet a: Number = b\n"
            + out(value="a")
            + "\n}\n"
            + MAIN
        )
        self.assertEqual(
            mixed.functions[0].order,
            (("binding", 0), ("statement", 0), ("binding", 1)),
        )

    def test_written_order_runs_trace_and_replay(self) -> None:
        program = parse(
            HEADER
            + "fn f() -> Out {\nvar b = 2\nb = b + 1\nlet a: Number = b * 2\n"
            + out(value="a")
            + "\n}\n"
            + MAIN
        )
        check(program)
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            self.assertEqual(run(program, "main", trace, inputs={})["value"], 6)
            self.assertEqual(replay(trace)["value"], 6)


class ElseIfTests(unittest.TestCase):
    def grade(self, score: int) -> str:
        body = (
            f"let score: Number = {score}\n"
            'let letter: Text = if score >= 90 { "A" } else if score >= 75 { "B" } '
            'else if score >= 50 { "C" } else { "F" }\n' + out(text="letter")
        )
        return result(body)["text"]

    def test_the_first_true_branch_wins(self) -> None:
        self.assertEqual(
            [self.grade(score) for score in (95, 80, 60, 10)], ["A", "B", "C", "F"]
        )

    def test_a_chain_still_needs_a_final_else(self) -> None:
        with self.assertRaises(ParseError):
            result('let t: Text = if true { "a" } else if false { "b" }\n' + out())

    def test_branches_must_agree_on_a_type(self) -> None:
        with self.assertRaises(CheckError):
            result(
                'let t: Text = if true { "a" } else if false { 1 } else { "c" }\n'
                + out()
            )

    def test_else_if_works_as_a_statement(self) -> None:
        body = (
            "var n = 0\n"
            "if false { n = 1 } else if true { n = 2 } else { n = 3 }\n"
            + out(value="n")
        )
        self.assertEqual(result(body)["value"], 2)


class RemainderTests(unittest.TestCase):
    def test_remainder_has_the_sign_of_the_divisor(self) -> None:
        cases = {"17 % 5": 2, "-7 % 3": 2, "7 % -3": -2, "7.5 % 2": 1.5, "6 % 3": 0}
        for expression, expected in cases.items():
            with self.subTest(expression=expression):
                self.assertEqual(result(out(value=expression))["value"], expected)

    def test_remainder_binds_like_multiplication(self) -> None:
        self.assertEqual(result(out(value="1 + 7 % 4 * 2"))["value"], 7)

    def test_remainder_by_zero_fails_the_run(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Remainder by zero"):
            result("let zero: Number = 0\n" + out(value="5 % zero"))

    def test_remainder_needs_numbers(self) -> None:
        with self.assertRaises(CheckError):
            result(out(value='"a" % 2'))


class MethodTests(unittest.TestCase):
    def test_text_methods(self) -> None:
        cases: dict[str, tuple[str, Any]] = {
            '"héllo".length()': ("value", 5),
            '" Ab ".trim().upper()': ("text", "AB"),
            '"AbC".lower()': ("text", "abc"),
            '"kosika".contains(part: "sik")': ("flag", True),
            '"kosika".starts_with(prefix: "ko")': ("flag", True),
            '"kosika".ends_with(suffix: "ko")': ("flag", False),
            '"a,b,,c".split(separator: ",")': ("items", ["a", "b", "", "c"]),
            '"a-b".split(separator: "-").join(separator: "+")': ("text", "a+b"),
        }
        for expression, (field, expected) in cases.items():
            with self.subTest(expression=expression):
                self.assertEqual(result(out(**{field: expression}))[field], expected)

    def test_array_and_number_methods(self) -> None:
        body = (
            'let words: Text[] = ["a", "b", "c"]\n'
            "let numbers: Number[] = [1, 2, 3]\n"
            "let middle: Number = (words.length() / 2).floor()\n"
        )
        self.assertEqual(result(body + out(text="words[middle]"))["text"], "b")
        self.assertTrue(result(body + out(flag="numbers.contains(item: 2)"))["flag"])
        self.assertFalse(result(body + out(flag='words.contains(item: "z")'))["flag"])
        for expression, expected in {
            "(2.5).round()": 3,
            "(-2.5).round()": -2,
            "(2.1).ceil()": 3,
            "(-2.1).floor()": -3,
        }.items():
            with self.subTest(expression=expression):
                self.assertEqual(
                    result(body + out(value=expression))["value"], expected
                )

    def test_length_of_an_empty_array_and_of_records(self) -> None:
        extra = (
            "record Item { name: Text }\nrecord Box { items: Item[] none: Number[] }\n"
        )
        extra += 'fn box() -> Box { return {items: [{name: "a"}], none: []} }\n'
        body = "let b: Box = box()\n" + out(value="b.items.length() + b.none.length()")
        self.assertEqual(result(body, extra=extra)["value"], 1)

    def test_methods_outside_the_set_are_check_errors(self) -> None:
        cases = {
            '"a".size()': "no method 'size'",
            "(1).upper()": "no method 'upper'",
            "true.length()": "have no methods",
            '"a".contains(item: "a")': "takes (part: Text)",
            '"a".contains(part: 1)': "expects 'Text'",
            '"a".length(extra: 1)': "takes ()",
        }
        for expression, message in cases.items():
            with self.subTest(expression=expression):
                with self.assertRaises(CheckError) as raised:
                    result("let x = " + expression + "\n" + out())
                self.assertIn(message, str(raised.exception))

    def test_join_is_for_text_arrays_and_contains_for_scalar_arrays(self) -> None:
        with self.assertRaisesRegex(MethodError, "no method 'join'"):
            resolve("Number[]", "join")
        with self.assertRaisesRegex(MethodError, "arrays of Text, Number, or Bool"):
            resolve("Item[]", "contains")
        self.assertEqual(resolve("Item[]", "length").returns, "Number")

    def test_split_is_bounded_and_needs_a_separator(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "non-empty separator"):
            result(out(items='"abc".split(separator: "")'))
        long_text = "," * 600
        with self.assertRaisesRegex(RuntimeError, "more than 512 parts"):
            result(out(items=f'"{long_text}".split(separator: ",")'))

    def test_method_names_are_unique_per_receiver(self) -> None:
        keys = [(method.receiver, method.name) for method in METHODS]
        self.assertEqual(len(keys), len(set(keys)))

    def test_a_user_function_may_share_a_method_name(self) -> None:
        extra = "fn length(text: Text) -> Number { return text.length() + 1 }\n"
        self.assertEqual(
            result(out(value='length(text: "ab")'), extra=extra)["value"], 3
        )


class ListBuildingTests(unittest.TestCase):
    WORDS = 'let words: Text[] = ["tiny", "elephant", "house", "ox"]\n'

    def test_for_where_filters_and_maps(self) -> None:
        body = (
            self.WORDS
            + "let long: Text[] = for w in words where w.length() > 4 { w.upper() }\n"
        )
        self.assertEqual(
            result(body + out(items="long"))["items"], ["ELEPHANT", "HOUSE"]
        )

    def test_for_where_keeps_its_limit_and_needs_a_bool(self) -> None:
        body = (
            self.WORDS + "let few: Text[] = for w in words where true limit 2 { w }\n"
        )
        with self.assertRaisesRegex(RuntimeError, "limit of 2"):
            result(body + out(items="few"))
        wrong = self.WORDS + "let bad: Text[] = for w in words where w.length() { w }\n"
        with self.assertRaisesRegex(CheckError, "condition expects 'Bool'"):
            result(wrong + out(items="bad"))

    def test_a_loop_without_where_parses_as_before(self) -> None:
        program = parse(
            HEADER
            + "fn f() -> Out {\n"
            + self.WORDS
            + "let same: Text[] = for w in words { w }\n"
            + out(items="same")
            + "\n}\n"
            + MAIN
        )
        self.assertIsNone(program.functions[0].bindings[1].value.condition)

    def test_append_concat_reverse_and_slice_return_new_arrays(self) -> None:
        body = (
            self.WORDS
            + 'var grown: Text[] = ["a"]\n'
            + 'repeat 2 times { grown = grown.append(item: "b") }\n'
        )
        cases = {
            "grown": ["a", "b", "b"],
            'grown.concat(other: ["z"])': ["a", "b", "b", "z"],
            "words.reverse()": ["ox", "house", "elephant", "tiny"],
            "words.slice(start: 1, end: 3)": ["elephant", "house"],
            "words.slice(start: 4, end: 4)": [],
        }
        for expression, expected in cases.items():
            with self.subTest(expression=expression):
                self.assertEqual(
                    result(body + out(items=expression))["items"], expected
                )
        self.assertEqual(result(body + out(value="words.length()"))["value"], 4)

    def test_text_slice_and_number_to_text(self) -> None:
        self.assertEqual(
            result(out(text='"kosika".slice(start: 2, end: 5)'))["text"], "sik"
        )
        self.assertEqual(
            result(out(text='(3.5).to_text() + "/" + (4).to_text()'))["text"], "3.5/4"
        )

    def test_slices_outside_the_value_fail_the_run(self) -> None:
        for expression, message in {
            '"abc".slice(start: 2, end: 9)': "outside a value of length 3",
            '"abc".slice(start: 2, end: 1)': "outside a value of length 3",
            '"abc".slice(start: 0.5, end: 1)': "whole numbers",
        }.items():
            with self.subTest(expression=expression):
                with self.assertRaisesRegex(RuntimeError, message):
                    result(out(text=expression))

    def test_building_methods_keep_the_element_type(self) -> None:
        for expression in ("words.append(item: 1)", "words.concat(other: [1])"):
            with self.subTest(expression=expression):
                with self.assertRaises(CheckError):
                    result(self.WORDS + out(items=expression))

    def test_an_array_cannot_grow_without_bound(self) -> None:
        from kosika.builtin_methods import MAX_ARRAY_LENGTH, call, resolve

        full = ["x"] * MAX_ARRAY_LENGTH
        with self.assertRaisesRegex(RuntimeError, "would exceed"):
            call(resolve("Text[]", "append"), full, {"item": "y"})


class UnknownNameAnchorTests(unittest.TestCase):
    def test_an_unknown_name_points_at_its_first_use(self) -> None:
        source = (
            "record A { x: Number }\n"
            "workflow main() -> A {\n"
            "  step s {\n"
            "    let a: A = nope()\n"
            "    return a\n"
            "  }\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "main.kos"
            path.write_text(source, encoding="utf-8")
            report = check_project(path)["diagnostics"]
        diagnostic = report["diagnostics"][0]
        self.assertEqual(diagnostic["code"], "module/private-symbol")
        self.assertEqual(diagnostic["data"]["names"], ["nope"])
        self.assertNotIn("anchor_missing", diagnostic["data"])
        self.assertEqual(diagnostic["range"]["start"], {"line": 3, "character": 15})
        self.assertIn("unknown names", diagnostic["message"])


if __name__ == "__main__":
    unittest.main()
