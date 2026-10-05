"""One unnamed argument, `fn main` as the entry point, and related short forms."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika.ast import program_payload
from kosika.checker import CheckError, check
from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.modules import load_program
from kosika.parser import ParseError, parse
from kosika.runtime import replay, run

RECORDS = (
    "record In { words: Text[] n: Number }\nrecord Out { text: Text value: Number }\n"
)


def checked(source: str):
    program = parse(source)
    check(program)
    return program


def result(source: str, inputs: dict[str, Any] | None = None, **host: Any) -> Any:
    return run(checked(source), "main", None, inputs=inputs or {}, **host)


def error_of(source: str) -> str:
    try:
        checked(source)
    except (CheckError, ParseError) as exc:
        return str(exc)
    raise AssertionError("expected the program to be rejected")


INPUT = {"input": {"words": ["b", "a"], "n": 7}}


class SoleArgumentTests(unittest.TestCase):
    def test_a_function_call_may_leave_out_its_only_name(self) -> None:
        source = RECORDS + (
            "fn double(n: Number) -> Number { return n * 2 }\n"
            "fn main(input: In) -> Out {\n"
            '  return {text: "x", value: double(input.n + 1) + double(3)}\n}\n'
        )
        self.assertEqual(result(source, INPUT)["value"], 22)

    def test_methods_take_their_only_argument_unnamed(self) -> None:
        source = RECORDS + (
            "fn main(input: In) -> Out {\n"
            '  let stock: Number{} = {"pen": 5}\n'
            "  return {\n"
            '    text: input.words.sort().join("-") + "a,b".split(",").length().to_text(),\n'
            '    value: stock.get("pen").or(0) + stock.get("ink").or(1) + input.n.min(3)\n'
            "  }\n}\n"
        )
        self.assertEqual(result(source, INPUT), {"text": "a-b2", "value": 9})

    def test_the_named_form_still_works_and_is_the_same_program(self) -> None:
        short = RECORDS + (
            "fn double(n: Number) -> Number { return n * 2 }\n"
            'fn main(input: In) -> Out { return {text: "x", value: double(input.n)} }\n'
        )
        named = short.replace("double(input.n)", "double(n: input.n)")
        self.assertEqual(parse(short), parse(named))

    def test_a_same_named_variable_keeps_its_old_meaning_and_hash(self) -> None:
        source = RECORDS + (
            'fn show(input: In) -> Out { return {text: "x", value: input.n} }\n'
            "workflow main(input: In) -> Out { out = show(input) }\n"
        )
        long_form = source.replace("show(input)", "show(input: input)")
        self.assertEqual(
            json.dumps(program_payload(parse(source)), sort_keys=True),
            json.dumps(program_payload(parse(long_form)), sort_keys=True),
        )

    def test_steps_resolve_models_tools_and_workflows(self) -> None:
        source = """
record Question { text: Text }
record Answer { text: Text }
record City { name: Text }
record Weather { celsius: Number }
model assistant { input: Question output: Answer }
tool get_weather { source mcp "weather" operation: "now" access: observe input: City output: Weather }
agent forecaster { can: [get_weather] }
workflow ask_it(question: Question) -> Answer { answer = use_model assistant("Say hi") }
workflow main(question: Question) -> Weather {
  answer  = call ask_it(question)
  weather = execute forecaster.get_weather("Lisbon")
}
"""
        provider = ScriptedModelProvider(
            [ModelResponse(content=json.dumps({"text": "hi"}))]
        )
        answer = result(
            source,
            {"question": {"text": "q"}},
            model_bindings={"assistant": provider},
            tool_bindings={
                "get_weather": lambda _tool, arguments: {
                    "celsius": len(arguments["name"])
                }
            },
        )
        self.assertEqual(answer, {"celsius": 6})

    def test_a_call_that_cannot_be_named_is_refused(self) -> None:
        two = RECORDS + (
            "fn add(a: Number, b: Number) -> Number { return a + b }\n"
            'fn main(input: In) -> Out { return {text: "x", value: add(3)} }\n'
        )
        self.assertIn("argument needs a name; write name: value", error_of(two))
        method = RECORDS + (
            'fn main(input: In) -> Out { return {text: "abc".slice(1), value: 1} }\n'
        )
        self.assertIn("takes (start: Number, end: Number)", error_of(method))

    def test_a_wrong_type_is_still_a_type_error(self) -> None:
        source = RECORDS + (
            "fn double(n: Number) -> Number { return n * 2 }\n"
            'fn main(input: In) -> Out { return {text: "x", value: double("seven")} }\n'
        )
        self.assertIn("expects 'Number', got 'Text'", error_of(source))


class MainFunctionTests(unittest.TestCase):
    SOURCE = RECORDS + (
        "fn main(input: In) -> Out {\n"
        '  return {text: input.words.join(","), value: input.n}\n}\n'
    )

    def test_fn_main_is_an_entry_point(self) -> None:
        self.assertEqual(result(self.SOURCE, INPUT), {"text": "b,a", "value": 7})

    def test_it_is_the_workflow_it_stands_for(self) -> None:
        long_form = self.SOURCE + (
            "workflow main(input: In) -> Out {\n"
            "  step result {\n    let result = main(input: input)\n    return result\n  }\n}\n"
        )
        self.assertEqual(parse(self.SOURCE), parse(long_form))

    def test_it_traces_replays_and_loads_from_a_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "main.kos"
            path.write_text(self.SOURCE, encoding="utf-8")
            trace = Path(directory) / "trace.jsonl"
            answer = run(load_program(path), "main", trace, inputs=INPUT)
            self.assertEqual(replay(trace), answer)

    def test_an_existing_workflow_main_is_left_alone(self) -> None:
        source = RECORDS + (
            'fn main(input: In) -> Out { return {text: "fn", value: 1} }\n'
            'fn other(input: In) -> Out { return {text: "other", value: 2} }\n'
            "workflow main(input: In) -> Out { out = other(input) }\n"
        )
        self.assertEqual(result(source, INPUT)["text"], "other")

    def test_a_main_that_is_not_record_shaped_gets_no_workflow(self) -> None:
        source = RECORDS + "fn main(n: Number) -> Number { return n }\n"
        program = checked(source)
        self.assertEqual(program.workflows, [])
        with self.assertRaisesRegex(ValueError, "Unknown workflow 'main'"):
            run(program, "main", None, inputs={})


class LoopAndNumberTests(unittest.TestCase):
    def test_a_loop_body_may_build_records_when_the_type_is_known(self) -> None:
        source = """
record Row { name: Text size: Number }
record In { words: Text[] }
record Out { rows: Row[] }
fn main(input: In) -> Out {
  let rows: Row[] = for word in input.words { {name: word, size: word.length()} }
  return {rows: rows.sort_by("size")}
}
"""
        self.assertEqual(
            result(source, {"input": {"words": ["ccc", "a"]}}),
            {"rows": [{"name": "a", "size": 1}, {"name": "ccc", "size": 3}]},
        )

    def test_a_record_in_a_loop_still_needs_a_known_type(self) -> None:
        source = (
            "record In { words: Text[] }\nrecord Out { n: Number }\n"
            "fn main(input: In) -> Out {\n"
            "  let rows = for word in input.words { {name: word} }\n"
            "  return {n: rows.length()}\n}\n"
        )
        self.assertIn("cannot infer the type of a record literal", error_of(source))

    def test_min_and_max_of_two_numbers(self) -> None:
        source = RECORDS + (
            "fn main(input: In) -> Out {\n"
            '  return {text: "x", value: input.n.min(3) * 10 + input.n.max(other: 9)}\n}\n'
        )
        self.assertEqual(result(source, INPUT)["value"], 39)


if __name__ == "__main__":
    unittest.main()
