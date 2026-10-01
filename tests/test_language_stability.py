import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika.checker import CheckError, check
from kosika.modules import load_program
from kosika.parser import ParseError, parse
from kosika.runtime import replay, run
from kosika.trace_replay import replay_events


class LanguageStabilityTests(unittest.TestCase):
    def test_replay_counts_active_frames_not_argument_nesting(self) -> None:
        expression = '{text: "ok"}'
        for _ in range(40):
            expression = f"identity(value: {expression})"
        source = (
            "record Result { text: Text } "
            "fn identity(value: Result) -> Result { return value } "
            "workflow main() -> Result { step get { "
            f"let result = identity(value: {expression}) return result }} }}"
        )
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            result = run(parse(source), "main", trace)
            self.assertEqual(replay(trace), result)
            events = [json.loads(line) for line in trace.read_text().splitlines()]
            call = events[2]
            returned = events[3]
            nested: list[dict[str, Any]] = []
            for _ in range(32):
                nested = [
                    dict(call, expression_events=[]),
                    dict(returned, events=nested),
                ]
            returned["events"] = nested
            with self.assertRaisesRegex(ValueError, "depth limit"):
                replay_events(events)

    def test_full_depth_function_chain_in_workflow_arguments_and_nested_workflows(
        self,
    ) -> None:
        declarations = [
            "record Result { text: Text }",
            "fn identity(value: Result) -> Result { return value }",
        ]
        for index in range(32):
            body = "value" if index == 31 else f"f{index + 1}(value: value)"
            declarations.append(
                f"fn f{index}(value: Result) -> Result {{ return {body} }}"
            )
        for call in ("f0(value: value)", "identity(value: f0(value: value))"):
            with self.subTest(call=call), tempfile.TemporaryDirectory() as directory:
                source = "\n".join(declarations) + (
                    " workflow inner(value: Result) -> Result { "
                    f"step get {{ let result = {call} return result }} }} "
                    "workflow middle(value: Result) -> Result { "
                    "step get { let result = call inner(value: value) return result } } "
                    "workflow main(value: Result) -> Result { "
                    "step get { let result = call middle(value: value) return result } }"
                )
                trace = Path(directory) / "trace.jsonl"
                result = run(
                    parse(source), "main", trace, inputs={"value": {"text": "ok"}}
                )
                self.assertEqual(replay(trace), result)

    def test_call_depth_limit_is_independent_of_declaration_order(self) -> None:
        for kind in ("function", "workflow"):
            for length in (32, 33):
                declarations = [
                    "record Result { text: Text }",
                    "fn identity(value: Result) -> Result { return value }",
                ]
                chain = []
                for index in range(length):
                    if kind == "function":
                        body = (
                            "value"
                            if index == length - 1
                            else f"f{index + 1}(value: value)"
                        )
                        chain.append(
                            f"fn f{index}(value: Result) -> Result {{ return {body} }}"
                        )
                    else:
                        call = (
                            "identity(value: value)"
                            if index == length - 1
                            else f"call f{index + 1}(value: value)"
                        )
                        chain.append(
                            f"workflow f{index}(value: Result) -> Result {{ "
                            f"step get {{ let result = {call} return result }} }}"
                        )
                for reverse in (False, True):
                    with self.subTest(kind=kind, length=length, reverse=reverse):
                        program = parse(
                            "\n".join(
                                declarations + (chain[::-1] if reverse else chain)
                            )
                        )
                        if length == 32:
                            check(program)
                        else:
                            with self.assertRaisesRegex(
                                CheckError, "call depth exceeds 32"
                            ):
                                check(program)

    def test_syntax_like_strings_remain_literal_values(self) -> None:
        for value in ("if", "not", "-", "[", "{", "(", "return", "and", "}"):
            with self.subTest(value=value):
                source = (
                    "record Result { text: Text } "
                    "fn value() -> Result { return {text: "
                    + json.dumps(value)
                    + "} } workflow main() -> Result { "
                    "step get { let result = value() return result } }"
                )
                self.assertEqual(run(parse(source), "main", None), {"text": value})

    def test_quoted_tokens_cannot_supply_syntax(self) -> None:
        for source in (
            '"export" []',
            '"fn" value() -> Text { return "hello" }',
            'fn value() -> Number { return 1 "+" 2 }',
            'fn value() -> Text { "return" "hello" }',
        ):
            with self.subTest(source=source), self.assertRaises(ParseError):
                parse(source)

    def test_workflow_argument_calls_respect_module_exports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / "library.kos"
            source = (
                "record Result { text: Text } "
                'fn hidden() -> Text { return "hello" } '
                "fn wrap(text: Text) -> Result { return {text: text} }"
            )
            library.write_text("export [Result, wrap] " + source, encoding="utf-8")
            entry = root / "main.kos"
            entry.write_text(
                'import "library.kos" workflow main() -> Result { '
                "step get { let result = wrap(text: if true { hidden() } "
                'else { "fallback" }) return result } }',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CheckError, "private"):
                load_program(entry)
            library.write_text(
                "export [Result, wrap, hidden] " + source, encoding="utf-8"
            )
            self.assertEqual(run(load_program(entry), "main", None), {"text": "hello"})
