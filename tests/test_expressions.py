from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kosika.ast import BinaryExpr, ConditionalExpr, RecordExpr, UnaryExpr
from kosika.checker import CheckError, check
from kosika.mindmap import build_mindmap
from kosika.parser import parse
from kosika.runtime import replay, run

SOURCE = """
record Inputs {
  a: Number
  b: Number
  enabled: Bool
}

record Decision {
  value: Number
  accepted: Bool
  label: Text
}

fn double(value: Number) -> Number {
  return value * 2
}

fn decide(input: Inputs) -> Decision {
  return {
    value: if input.enabled and input.a < input.b {
      input.a + double(value: input.b)
    } else {
      -input.a
    },
    accepted: input.enabled and not (input.a == input.b),
    label: if input.enabled {"enabled"} else {"disabled"}
  }
}

workflow main(input: Inputs) -> Decision {
  step decide {
    let result: Decision = decide(input: input)
    return result
  }
}
"""


class ExpressionTests(unittest.TestCase):
    def test_precedence_conditionals_and_unary_expressions(self) -> None:
        program = parse(SOURCE)
        check(program)
        decision = program.functions[1].return_value
        assert isinstance(decision, RecordExpr)
        value_expression = decision.fields[0][1]
        accepted_expression = decision.fields[1][1]

        self.assertIsInstance(value_expression, ConditionalExpr)
        conditional = value_expression
        assert isinstance(conditional, ConditionalExpr)
        self.assertIsInstance(conditional.condition, BinaryExpr)
        self.assertIsInstance(conditional.when_false, UnaryExpr)
        self.assertIsInstance(accepted_expression, BinaryExpr)

        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "expressions.jsonl"
            result = run(
                program,
                "main",
                trace,
                inputs={"input": {"a": 3, "b": 4, "enabled": True}},
            )
            events = [json.loads(line) for line in trace.read_text().splitlines()]
            function_return = next(
                event for event in events if event["event"] == "function_return"
            )

            self.assertEqual(
                result, {"value": 11, "accepted": True, "label": "enabled"}
            )
            self.assertEqual(function_return["events"][0]["function"], "double")
            self.assertEqual(replay(trace), result)

    def test_conditional_evaluates_only_selected_branch(self) -> None:
        program = parse(SOURCE)
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "false-branch.jsonl"
            result = run(
                program,
                "main",
                trace,
                inputs={"input": {"a": 3, "b": 4, "enabled": False}},
            )
            events = [json.loads(line) for line in trace.read_text().splitlines()]
            function_return = next(
                event for event in events if event["event"] == "function_return"
            )

            self.assertEqual(
                result, {"value": -3, "accepted": False, "label": "disabled"}
            )
            self.assertEqual(function_return["events"], [])
            self.assertEqual(replay(trace), result)

    def test_boolean_operators_short_circuit(self) -> None:
        source = """
        record Input { flag: Bool }
        record Output { result: Bool }
        fn safe(input: Input) -> Output {
          return {result: input.flag or (1 / 0 > 0)}
        }
        workflow main(input: Input) -> Output {
          step safe { let result: Output = safe(input: input) return result }
        }
        """
        program = parse(source)
        self.assertEqual(
            run(program, "main", None, inputs={"input": {"flag": True}}),
            {"result": True},
        )
        with self.assertRaisesRegex(RuntimeError, "Division by zero"):
            run(program, "main", None, inputs={"input": {"flag": False}})

    def test_number_equality_treats_integer_and_decimal_as_same_type(self) -> None:
        source = """
        record Output { same: Bool }
        fn compare() -> Output { return {same: 1 == 1.0} }
        workflow main() -> Output {
          step compare { let result: Output = compare() return result }
        }
        """
        self.assertEqual(run(parse(source), "main", None), {"same": True})

    def test_expression_type_errors_fail_static_checking(self) -> None:
        template = """
        record Result { value: TYPE }
        fn invalid() -> Result { return {value: EXPRESSION} }
        """
        cases = [
            ('"left" - "right"', "Text", "expects Number operands"),
            ("1 and true", "Bool", "expects Bool operands"),
            ("if 1 {true} else {false}", "Bool", "condition expects 'Bool'"),
            ("if true {1} else {false}", "Number", "branches differ"),
            ("1 == true", "Bool", "requires matching types"),
            ("not 1", "Bool", "operator 'not' expects 'Bool'"),
        ]
        for expression, type_name, message in cases:
            with self.subTest(expression=expression):
                source = template.replace("TYPE", type_name).replace(
                    "EXPRESSION", expression
                )
                with self.assertRaisesRegex(CheckError, message):
                    check(parse(source))

    def test_text_concatenation_is_valid(self) -> None:
        source = """
        record Result { value: Text }
        fn join() -> Result { return {value: "left" + "right"} }
        workflow main() -> Result {
          step join { let result: Result = join() return result }
        }
        """
        program = parse(source)
        check(program)
        self.assertEqual(run(program, "main", None), {"value": "leftright"})

    def test_mindmap_includes_calls_from_conditional_branches(self) -> None:
        mindmap = build_mindmap(parse(SOURCE))
        self.assertTrue(
            any(
                edge["source"] == "function:decide"
                and edge["target"] == "function:double"
                and edge["relation"] == "function_call"
                for edge in mindmap["edges"]
            )
        )


if __name__ == "__main__":
    unittest.main()
