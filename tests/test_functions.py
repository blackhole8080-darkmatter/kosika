from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kosika.ast import FieldExpr, FunctionCall, FunctionCallExpr, RecordExpr
from kosika.checker import CheckError, check
from kosika.devtools import inspect_trace
from kosika.explain import summarize_trace
from kosika.mindmap import build_mindmap
from kosika.modules import load_program
from kosika.parser import ParseError, parse
from kosika.runtime import replay, run
from kosika.schema import SchemaValidationError

SOURCE = """
record Person {
  name: Text
}

record Greeting {
  text: Text
}

fn make_greeting(person: Person) -> Greeting {
  return {text: person.name}
}

workflow main(person: Person) -> Greeting {
  step prepare {
    let result: Greeting = make_greeting(person: person)
    return result
  }
}
"""


class FunctionTests(unittest.TestCase):
    def test_parse_and_check_pure_function(self) -> None:
        program = parse(SOURCE)
        check(program)

        function = program.functions[0]
        self.assertEqual(function.name, "make_greeting")
        self.assertIsInstance(function.return_value, RecordExpr)
        record = function.return_value
        assert isinstance(record, RecordExpr)
        self.assertIsInstance(record.fields[0][1], FieldExpr)
        self.assertIsInstance(program.workflows[0].steps[0].call, FunctionCall)

    def test_function_executes_and_replays_without_external_authority(self) -> None:
        program = parse(SOURCE)
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "function.jsonl"
            result = run(
                program,
                "main",
                trace,
                inputs={"person": {"name": "Ada"}},
            )
            events = [
                json.loads(line)
                for line in trace.read_text(encoding="utf-8").splitlines()
            ]

            self.assertEqual(result, {"text": "Ada"})
            self.assertEqual(events[0]["trace_version"], 5)
            call = next(event for event in events if event["event"] == "function_call")
            self.assertEqual(call["function"], "make_greeting")
            self.assertEqual(call["arguments"], {"person": {"name": "Ada"}})
            self.assertEqual(replay(trace), result)

    def test_primitive_function_results_flow_between_steps(self) -> None:
        source = SOURCE.replace(
            "fn make_greeting(person: Person) -> Greeting {\n"
            "  return {text: person.name}\n"
            "}",
            "fn person_name(person: Person) -> Text {\n"
            "  return person.name\n"
            "}\n\n"
            "fn make_greeting(text: Text) -> Greeting {\n"
            "  return {text: text}\n"
            "}",
        ).replace(
            "step prepare {\n"
            "    let result: Greeting = make_greeting(person: person)\n"
            "    return result\n"
            "  }",
            "step extract {\n"
            "    let name: Text = person_name(person: person)\n"
            "    return name\n"
            "  }\n"
            "  step prepare {\n"
            "    let result: Greeting = make_greeting(text: name)\n"
            "    return result\n"
            "  }",
        )

        result = run(
            parse(source),
            "main",
            None,
            inputs={"person": {"name": "Grace"}},
        )
        self.assertEqual(result, {"text": "Grace"})

    def test_function_checker_rejects_invalid_contracts_and_calls(self) -> None:
        cases = [
            (SOURCE.replace("person.name", "person.missing"), "unknown field"),
            (
                SOURCE.replace("-> Greeting", "-> Person", 1),
                "unknown record fields",
            ),
            (
                SOURCE.replace("person: Person", "person: Missing", 1),
                "Unknown field type",
            ),
            (
                SOURCE.replace(
                    "make_greeting(person: person)", "missing(person: person)"
                ),
                "unknown function",
            ),
            (
                SOURCE.replace(
                    "make_greeting(person: person)", "make_greeting(person: 7)"
                ),
                "expects 'Person'",
            ),
            (
                SOURCE.replace(
                    "fn make_greeting(person: Person)",
                    "fn make_greeting(person: Person, person: Person)",
                ),
                "duplicate parameter",
            ),
        ]
        for source, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(CheckError, message):
                    check(parse(source))

    def test_function_composition_records_nested_events(self) -> None:
        source = SOURCE.replace(
            "fn make_greeting(person: Person) -> Greeting {\n"
            "  return {text: person.name}\n"
            "}",
            "fn person_name(person: Person) -> Text {\n"
            "  return person.name\n"
            "}\n\n"
            "fn make_greeting(person: Person) -> Greeting {\n"
            "  return {text: person_name(person: person)}\n"
            "}",
        )
        program = parse(source)
        return_value = program.functions[1].return_value
        assert isinstance(return_value, RecordExpr)
        self.assertIsInstance(return_value.fields[0][1], FunctionCallExpr)
        mindmap = build_mindmap(program)
        self.assertTrue(
            any(
                edge["source"] == "function:make_greeting"
                and edge["target"] == "function:person_name"
                and edge["relation"] == "function_call"
                for edge in mindmap["edges"]
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "nested-functions.jsonl"
            result = run(
                program,
                "main",
                trace,
                inputs={"person": {"name": "Ada"}},
            )
            events = [json.loads(line) for line in trace.read_text().splitlines()]
            returned = next(
                event for event in events if event["event"] == "function_return"
            )

            self.assertEqual(result, {"text": "Ada"})
            self.assertEqual(
                [event["event"] for event in returned["events"]],
                ["function_call", "function_return"],
            )
            self.assertEqual(returned["events"][0]["function"], "person_name")
            self.assertEqual(replay(trace), result)
            self.assertIn(
                "prepare/person_name",
                [step["step"] for step in summarize_trace(trace)["steps"]],
            )
            self.assertIn(
                "prepare/person_name",
                [step["step"] for step in inspect_trace(trace)["steps"]],
            )
            returned["events"][0]["function"] = "tampered"
            trace.write_text(
                "\n".join(json.dumps(event) for event in events) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "mismatched"):
                replay(trace)

    def test_function_calls_are_valid_workflow_argument_expressions(self) -> None:
        source = SOURCE.replace(
            "fn make_greeting(person: Person) -> Greeting {\n"
            "  return {text: person.name}\n"
            "}",
            "fn person_name(person: Person) -> Text {\n"
            "  return person.name\n"
            "}\n\n"
            "fn make_greeting(text: Text) -> Greeting {\n"
            "  return {text: text}\n"
            "}",
        ).replace(
            "make_greeting(person: person)",
            "make_greeting(text: person_name(person: person))",
        )
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "argument-functions.jsonl"
            result = run(
                parse(source),
                "main",
                trace,
                inputs={"person": {"name": "Lin"}},
            )
            events = [json.loads(line) for line in trace.read_text().splitlines()]
            outer_call = next(
                event
                for event in events
                if event["event"] == "function_call"
                and event["function"] == "make_greeting"
            )

            self.assertEqual(result, {"text": "Lin"})
            self.assertEqual(
                outer_call["expression_events"][0]["function"], "person_name"
            )
            self.assertEqual(replay(trace), result)

    def test_function_body_cannot_execute_effects(self) -> None:
        effectful = SOURCE.replace(
            "return {text: person.name}",
            "return execute monitor.tool()",
        )
        with self.assertRaises(ParseError):
            parse(effectful)

    def test_recursive_and_overdeep_function_graphs_are_rejected(self) -> None:
        recursive = SOURCE.replace(
            "return {text: person.name}",
            "return make_greeting(person: person)",
        )
        with self.assertRaisesRegex(CheckError, "Recursive function call"):
            check(parse(recursive))

        declarations = []
        for index in range(33):
            body = "value" if index == 32 else f"f{index + 1}(value: value)"
            declarations.append(
                f"fn f{index}(value: Greeting) -> Greeting {{ return {body} }}"
            )
        overdeep = SOURCE.split("fn make_greeting", 1)[0] + "\n".join(declarations)
        with self.assertRaisesRegex(CheckError, "Function call depth exceeds 32"):
            check(parse(overdeep))

    def test_replay_rejects_tampered_function_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "function.jsonl"
            run(
                parse(SOURCE),
                "main",
                trace,
                inputs={"person": {"name": "Ada"}},
            )
            events = [json.loads(line) for line in trace.read_text().splitlines()]
            invalid = {"text": 7}
            next(event for event in events if event["event"] == "function_return")[
                "result"
            ] = invalid
            next(event for event in events if event["event"] == "step_complete")[
                "result"
            ] = invalid
            events[-1]["result"] = invalid
            trace.write_text(
                "\n".join(json.dumps(event) for event in events) + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(SchemaValidationError):
                replay(trace)

    def test_version_four_function_trace_remains_replayable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "function-v4.jsonl"
            result = run(
                parse(SOURCE),
                "main",
                trace,
                inputs={"person": {"name": "Ada"}},
            )
            events = [json.loads(line) for line in trace.read_text().splitlines()]
            events[0]["trace_version"] = 4
            call = next(event for event in events if event["event"] == "function_call")
            returned = next(
                event for event in events if event["event"] == "function_return"
            )
            call.pop("expression_events")
            returned.pop("events")
            trace.write_text(
                "\n".join(json.dumps(event) for event in events) + "\n",
                encoding="utf-8",
            )

            self.assertEqual(replay(trace), result)

    def test_function_modules_and_mindmap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "library.kos").write_text(
                "export [Person, Greeting, make_greeting]\n"
                + SOURCE.split("workflow main", 1)[0],
                encoding="utf-8",
            )
            (root / "main.kos").write_text(
                'import "library.kos"\n'
                "workflow main(person: Person) -> Greeting {\n"
                "  step prepare {\n"
                "    let result: Greeting = make_greeting(person: person)\n"
                "    return result\n"
                "  }\n"
                "}\n",
                encoding="utf-8",
            )
            program = load_program(root / "main.kos")

            mindmap = build_mindmap(program)
            function_nodes = [
                node for node in mindmap["nodes"] if node["kind"] == "function"
            ]
            self.assertEqual(
                [node["label"] for node in function_nodes], ["make_greeting"]
            )
            self.assertTrue(
                any(edge["relation"] == "function_call" for edge in mindmap["edges"])
            )

            (root / "private.kos").write_text(
                "export [Person]\n"
                "record Person { name: Text }\n"
                "fn hidden(person: Person) -> Person { return person }\n",
                encoding="utf-8",
            )
            (root / "private-main.kos").write_text(
                'import "private.kos"\n'
                "record Result { person: Person }\n"
                "fn expose(person: Person) -> Result {\n"
                "  return {person: hidden(person: person)}\n"
                "}\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CheckError, "private"):
                load_program(root / "private-main.kos")


if __name__ == "__main__":
    unittest.main()
