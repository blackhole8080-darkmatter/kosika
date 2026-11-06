"""Optional values (`T?`) and maps (`T{}`)."""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika.builtin_methods import MAX_MAP_ENTRIES, call, resolve
from kosika.checker import CheckError, check
from kosika.function_types import assignable, base_type, unify
from kosika.modules import load_program
from kosika.parser import ParseError, parse
from kosika.runtime import replay, run
from kosika.schema import SchemaValidationError, schema_for_type, validate

SOURCE = """
record Person { name: Text nickname: Text? age: Number? }
record In { people: Person[] stock: Number{} raw: Text }
record Out {
  shown: Text[]
  total: Number
  pen: Number
  sofa: Bool
  keys: Text[]
  parsed: Number
  first: Text
  none: Text?
  ages: Number{}
}

fn display(person: Person) -> Text {
  return person.nickname.or(default: person.name)
}

fn build(input: In) -> Out {
  var ages: Number{} = {}
  for person in input.people {
    ages = ages.set(key: person.name, value: person.age.or(default: 0))
  }
  let restocked: Number{} = input.stock.set(key: "ink", value: 3).remove(key: "lamp")
  var total = 0
  for amount in restocked.values() { total = total + amount }
  let nobody: Text[] = []
  return {
    shown: for person in input.people { display(person) },
    total: total,
    pen: input.stock.get(key: "pen").or(default: 0),
    sofa: input.stock.has(key: "sofa") or input.stock.get(key: "sofa").exists(),
    keys: restocked.keys(),
    parsed: input.raw.to_number().or(default: -1),
    first: input.people.first().value().name,
    none: nobody.last(),
    ages: ages
  }
}

workflow main(input: In) -> Out { out = build(input) }
"""

INPUTS: dict[str, Any] = {
    "input": {
        "people": [
            {"name": "Ada", "nickname": None, "age": 36},
            {"name": "Grace", "nickname": "Amazing", "age": None},
        ],
        "stock": {"pen": 5, "lamp": 1},
        "raw": " 42.5 ",
    }
}

EXPECTED = {
    "shown": ["Ada", "Amazing"],
    "total": 8,
    "pen": 5,
    "sofa": False,
    "keys": ["pen", "ink"],
    "parsed": 42.5,
    "first": "Ada",
    "none": None,
    "ages": {"Ada": 36, "Grace": 0},
}


def checked(source: str = SOURCE):
    program = parse(source)
    check(program)
    return program


def rejected(old: str, new: str) -> str:
    try:
        checked(SOURCE.replace(old, new))
    except (CheckError, ParseError) as exc:
        return str(exc)
    raise AssertionError("expected the program to be rejected")


def value_of(expression: str, type_name: str, setup: str = "") -> Any:
    source = (
        f"record Out {{ value: {type_name} }}\n"
        f"fn f() -> Out {{\n{setup}\nreturn {{value: {expression}}}\n}}\n"
        "workflow main() -> Out { out = f() }\n"
    )
    return run(checked(source), "main", None, inputs={})["value"]


class EndToEndTests(unittest.TestCase):
    def test_optionals_and_maps_run_trace_and_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            result = run(checked(), "main", trace, inputs=copy.deepcopy(INPUTS))
            self.assertEqual(result, EXPECTED)
            self.assertEqual(replay(trace), EXPECTED)

    def test_types_survive_a_file_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "main.kos"
            path.write_text(SOURCE, encoding="utf-8")
            program = load_program(path)
        fields = {field.name: field.type_name for field in program.records[0].fields}
        self.assertEqual(
            fields, {"name": "Text", "nickname": "Text?", "age": "Number?"}
        )

    def test_an_optional_input_field_must_be_present_as_null(self) -> None:
        inputs = copy.deepcopy(INPUTS)
        del inputs["input"]["people"][0]["nickname"]
        with self.assertRaisesRegex(SchemaValidationError, "missing required property"):
            run(checked(), "main", None, inputs=inputs)

    def test_a_map_input_rejects_a_value_of_the_wrong_type(self) -> None:
        inputs = copy.deepcopy(INPUTS)
        inputs["input"]["stock"]["pen"] = "five"
        with self.assertRaisesRegex(SchemaValidationError, r"stock\.pen"):
            run(checked(), "main", None, inputs=inputs)


class OptionalTests(unittest.TestCase):
    def test_a_plain_value_and_null_both_fit_an_optional(self) -> None:
        self.assertEqual(value_of("a", "Text?", 'let a: Text? = "x"'), "x")
        self.assertIsNone(value_of("a", "Text?", "let a: Text? = null"))
        self.assertEqual(value_of('if 1 > 2 { "yes" } else { null }', "Text?"), None)

    def test_or_exists_and_value(self) -> None:
        setup = 'let some: Text? = "x"\nlet none: Text? = null'
        self.assertEqual(value_of('none.or(default: "d")', "Text", setup), "d")
        self.assertEqual(value_of('some.or(default: "d")', "Text", setup), "x")
        self.assertTrue(value_of("some.exists() and not none.exists()", "Bool", setup))
        self.assertTrue(value_of("none == null and some != null", "Bool", setup))
        self.assertEqual(value_of("some.value().upper()", "Text", setup), "X")
        with self.assertRaisesRegex(RuntimeError, "absent value"):
            value_of("none.value()", "Text", setup)

    def test_to_number_reads_decimals_or_gives_nothing(self) -> None:
        # An exponent is read too, so a number Kosika writes as text
        # (6.6743e-11) reads back; a bare "1e" is not a number.
        cases = {'"12"': 12, '" -3.5 "': -3.5, '"1e3"': 1000.0, '"2.5E-3"': 0.0025, '"1e"': None, '"abc"': None, '""': None}
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(value_of(f"{text}.to_number()", "Number?"), expected)

    def test_first_and_last_of_an_empty_array_are_absent(self) -> None:
        setup = "let xs: Number[] = [4, 5]\nlet none: Number[] = []"
        self.assertEqual(
            value_of("xs.first().value() + xs.last().value()", "Number", setup), 9
        )
        self.assertIsNone(value_of("none.first()", "Number?", setup))

    def test_an_optional_cannot_be_used_as_the_plain_value(self) -> None:
        cases = {
            (
                "person.nickname.or(default: person.name)",
                "person.nickname",
            ): "got 'Text?'",
            (
                "person.nickname.or(default: person.name)",
                "person.nickname.upper()",
            ): "may be absent",
            (
                "person.age.or(default: 0)",
                'person.age.or(default: "x")',
            ): "expects 'Number'",
            ("input.people.first().value().name", "input.people.first().name"): (
                "may be absent"
            ),
        }
        for (old, new), message in cases.items():
            with self.subTest(new=new):
                self.assertIn(message, rejected(old, new))

    def test_optional_is_written_once(self) -> None:
        self.assertIn("optional once", rejected("nickname: Text?", "nickname: Text??"))
        with self.assertRaises(CheckError):
            checked(
                "record R { x: Null? }\nfn f() -> R { return {x: null} }\n"
                "workflow main() -> R { r = f() }\n"
            )


class MapTests(unittest.TestCase):
    SETUP = 'let m: Number{} = {"north": 4, south: 2}'

    def test_literals_with_quoted_or_bare_keys(self) -> None:
        self.assertEqual(
            value_of("m", "Number{}", self.SETUP), {"north": 4, "south": 2}
        )
        self.assertEqual(value_of("m", "Text{}", "let m: Text{} = {}"), {})

    def test_methods_return_new_maps_and_keep_insertion_order(self) -> None:
        cases: dict[str, tuple[str, Any]] = {
            'm.set(key: "east", value: 9).keys()': (
                "Text[]",
                ["north", "south", "east"],
            ),
            'm.set(key: "north", value: 0).values()': ("Number[]", [0, 2]),
            'm.remove(key: "north").keys()': ("Text[]", ["south"]),
            'm.remove(key: "absent").length()': ("Number", 2),
            'm.has(key: "south")': ("Bool", True),
            'm.get(key: "west")': ("Number?", None),
            "m.length()": ("Number", 2),
        }
        for expression, (type_name, expected) in cases.items():
            with self.subTest(expression=expression):
                self.assertEqual(value_of(expression, type_name, self.SETUP), expected)

    def test_maps_of_records_and_lists(self) -> None:
        source = """
record Item { price: Number }
record Out { cheap: Text[] tags: Text[] }
fn f() -> Out {
  let items: Item{} = {"pen": {price: 2}, "lamp": {price: 30}}
  let tags: Text[]{} = {"pen": ["small", "blue"]}
  return {
    cheap: for name in items.keys() where items.get(key: name).value().price < 10 { name },
    tags: tags.get(key: "pen").or(default: [])
  }
}
workflow main() -> Out { out = f() }
"""
        self.assertEqual(
            run(checked(source), "main", None, inputs={}),
            {"cheap": ["pen"], "tags": ["small", "blue"]},
        )

    def test_a_map_is_not_a_record(self) -> None:
        cases = {
            ("total: total,", "total: input.stock.pen,"): "a map is read with .get",
            ("value: 3)", 'value: "three")'): "expects 'Number'",
            ("var ages: Number{} = {}", 'var ages: Number{} = {"a": "b"}'): (
                "expects 'Number'"
            ),
            ("var ages: Number{} = {}", 'var ages: Number{} = {"a": 1, "a": 2}'): (
                "duplicate"
            ),
            ("restocked.keys()", "restocked.size()"): "no method 'size'",
        }
        for (old, new), message in cases.items():
            with self.subTest(new=new):
                self.assertIn(message, rejected(old, new))

    def test_a_map_cannot_grow_without_bound(self) -> None:
        full = {str(index): index for index in range(MAX_MAP_ENTRIES)}
        with self.assertRaisesRegex(RuntimeError, "would exceed"):
            call(resolve("Number{}", "set"), full, {"key": "extra", "value": 1})
        self.assertEqual(
            len(call(resolve("Number{}", "set"), full, {"key": "0", "value": 9})),
            MAX_MAP_ENTRIES,
        )

    def test_an_empty_body_is_still_a_body_not_a_map_type(self) -> None:
        with self.assertRaises((CheckError, ParseError)) as raised:
            checked("record R { x: Number }\nworkflow main() -> R { }\n")
        self.assertNotIn("Expected", str(raised.exception))


class TypeRuleTests(unittest.TestCase):
    def test_assignability_and_unification(self) -> None:
        self.assertTrue(assignable("Text", "Text?"))
        self.assertTrue(assignable("Null", "Text?"))
        self.assertFalse(assignable("Text?", "Text"))
        self.assertFalse(assignable("Number", "Text?"))
        self.assertEqual(unify("Text", "Null"), "Text?")
        self.assertEqual(unify("Null", "Text?"), "Text?")
        self.assertEqual(unify("Never", "Number"), "Number")
        self.assertIsNone(unify("Text", "Number"))
        self.assertEqual(base_type("Item[]{}?"), "Item")

    def test_schemas_for_optionals_and_maps(self) -> None:
        program = checked()
        optional = schema_for_type(program, "Person?")
        validate(None, optional)
        validate({"name": "a", "nickname": None, "age": 1}, optional)
        with self.assertRaisesRegex(SchemaValidationError, "matches no allowed form"):
            validate(3, optional)
        mapping = schema_for_type(program, "Number[]{}")
        validate({"a": [1, 2], "b": []}, mapping)
        with self.assertRaises(SchemaValidationError):
            validate({"a": ["x"]}, mapping)


if __name__ == "__main__":
    unittest.main()
