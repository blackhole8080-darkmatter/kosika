"""Type inference on locals, and `var` with assignment.

Covers the two ergonomics blockers: `let` no longer needs an annotation, and a
`var` binding is assignable. The properties worth pinning are that inference
does not weaken checking, that only `var` may be assigned, and that a write
inside a loop reaches the enclosing binding instead of a discarded copy.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kosika.ast import Block, LocalBinding
from kosika.checker import CheckError, check
from kosika.parser import ParseError, parse
from kosika.runtime import replay, run

# The accumulate case is the reason `var` exists: a loop body that updates a
# value declared outside it.
ACCUMULATE = """
record Box { values: Number[] step: Number }
record Out { total: Number label: Text }
fn accumulate(box: Box) -> Out {
  var total = 0
  var label = "sum"
  for value in box.values {
    total = total + value * box.step
  }
  return {total: total, label: label}
}
workflow main(box: Box) -> Out {
  step go { let out: Out = accumulate(box: box) return out }
}
"""


class InferenceTests(unittest.TestCase):
    def test_annotation_is_optional_and_records_inferred_source_state(self):
        program = parse("fn f() -> Number { let x = 1 return x }")
        check(program)
        binding = program.functions[0].bindings[0]
        self.assertIsNone(binding.type_name)
        self.assertFalse(binding.mutable)

    def test_inference_covers_every_literal_kind(self):
        cases = {"1": "Number", '"s"': "Text", "true": "Bool", "null": "Null"}
        for literal, expected in cases.items():
            with self.subTest(literal=literal):
                program = parse(
                    f"fn f() -> {expected} {{ let x = {literal} return x }}"
                )
                check(program)

    def test_inference_works_without_an_explicit_return_annotation(self):
        # Return annotations stay required, but a local never needs one.
        program = parse("fn f() -> Number { let a = 2 let b = a * 3 return b }")
        check(program)
        self.assertEqual(
            [b.type_name for b in program.functions[0].bindings], [None, None]
        )

    def test_inference_still_rejects_a_known_type_mismatch(self):
        with self.assertRaisesRegex(CheckError, "expects"):
            check(parse("fn f() -> Number { let x: Text = 1 return 1 }"))

    def test_inferred_local_feeds_the_annotated_binding_that_uses_it(self):
        source = """
        record R { v: Number }
        fn f() -> R { let n = 41 let r: R = {v: n + 1} return r }
        workflow main() -> R { step s { let out: R = f() return out } }
        """
        program = parse(source)
        check(program)
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                run(program, "main", Path(directory) / "r.jsonl"), {"v": 42}
            )

    def test_annotated_and_inferred_bindings_are_equivalent(self):
        with tempfile.TemporaryDirectory() as directory:
            annotated = parse(
                "fn f() -> R { let a: Number = 2 let b: Number = a * 3 return {v: b} }\n"
                "workflow main() -> R { step s { let o: R = f() return o } }"
                "record R { v: Number }"
            )
            inferred = parse(
                "fn f() -> R { let a = 2 let b = a * 3 return {v: b} }\n"
                "workflow main() -> R { step s { let o: R = f() return o } }"
                "record R { v: Number }"
            )
            a = run(annotated, "main", Path(directory) / "a.jsonl")
            b = run(inferred, "main", Path(directory) / "b.jsonl")
            self.assertEqual(a, b)
            self.assertEqual(a, {"v": 6})


class VarTests(unittest.TestCase):
    def test_var_marks_the_binding_mutable(self):
        program = parse("fn f() -> Number { var x = 1 return x }")
        check(program)
        self.assertTrue(program.functions[0].bindings[0].mutable)

    def test_accumulation_across_a_loop_reaches_the_enclosing_binding(self):
        program = parse(ACCUMULATE)
        check(program)
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "run.jsonl"
            result = run(
                program,
                "main",
                trace,
                inputs={"box": {"values": [1, 2, 3], "step": 10}},
            )
            self.assertEqual(result, {"total": 60, "label": "sum"})
            with patch("kosika.runtime._execute_function", side_effect=AssertionError):
                self.assertEqual(replay(trace), result)

    def test_mutation_replays_identically(self):
        # A trace of a mutating program must still replay without re-running it.
        program = parse(ACCUMULATE)
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "run.jsonl"
            first = run(
                program, "main", trace, inputs={"box": {"values": [2, 4], "step": 3}}
            )
            events = [json.loads(line) for line in trace.read_text().splitlines()]
            self.assertTrue(events)
            self.assertEqual(replay(trace), first)

    def test_statement_only_block_parses_to_a_block(self):
        program = parse(
            "fn f() -> Number { var t = 0 repeat 3 times { t = t + 1 } return t }"
        )
        check(program)
        statements = program.functions[0].statements
        self.assertEqual(len(statements), 1)
        body = statements[0].body  # type: ignore[union-attr]
        self.assertIsInstance(body, Block)
        self.assertEqual([type(s).__name__ for s in body.statements], ["Assignment"])
        self.assertIsNone(body.result)
        self.assertEqual(body.statements[0].target, "t")  # type: ignore[union-attr]

    def test_block_with_a_final_value_produces_it(self):
        program = parse(
            "fn f() -> Number { var t = 0 repeat 3 times { t = t + 1 t } return 0 }"
        )
        check(program)

    def test_block_local_declaration_is_scoped_to_the_block(self):
        source = """
        fn f() -> Number {
          var total = 0
          repeat 2 times { let step = 5 total = total + step }
          return total
        }
        """
        program = parse(source)
        check(program)
        with self.assertRaisesRegex(CheckError, "unknown variable"):
            check(parse(source.replace("return total", "return step")))


class MutabilityRejectionTests(unittest.TestCase):
    def test_assigning_to_a_let_is_rejected(self):
        with self.assertRaisesRegex(CheckError, "declare it with 'var'"):
            check(parse("fn f() -> Number { let x = 1 x = 2 return x }"))

    def test_assigning_to_a_parameter_is_rejected(self):
        with self.assertRaisesRegex(CheckError, "declare it with 'var'"):
            check(parse("fn f(x: Number) -> Number { x = 1 return x }"))

    def test_assigning_to_an_unknown_name_is_rejected(self):
        with self.assertRaisesRegex(CheckError, "unknown variable"):
            check(parse("fn f() -> Number { x = 1 return 1 }"))

    def test_assigning_a_different_type_is_rejected(self):
        with self.assertRaisesRegex(CheckError, "assigns"):
            check(parse('fn f() -> Number { var x = 1 x = "s" return x }'))

    def test_block_local_cannot_shadow_an_enclosing_binding(self):
        with self.assertRaisesRegex(CheckError, "shadows"):
            check(
                parse(
                    "fn f() -> Number { var x = 1 repeat 2 times { let x = 2 } return x }"
                )
            )

    def test_assignment_is_not_valid_in_return_position(self):
        with self.assertRaises(ParseError):
            parse("fn f() -> Number { var x = 1 return x = 2 }")

    def test_mutating_binding_still_needs_a_declared_output_type(self):
        with self.assertRaises(ParseError):
            parse("fn f() { var x = 1 return x }")

    def test_inferred_literal_needing_a_type_is_asked_for_one(self):
        # Array and record literals have no inferable element/record type, so
        # omitting the annotation must produce a clear request, not a crash or
        # a silently-None type.
        for source, pattern in (
            ("fn f() -> Text { let x = [1, 2] return x }", "annotate"),
            ("fn f() -> Text { let x = {a: 1} return x }", "annotate"),
        ):
            with (
                self.subTest(source=source),
                self.assertRaisesRegex(CheckError, pattern),
            ):
                check(parse(source))

    def test_inferred_binding_does_not_break_module_reference_analysis(self):
        # An inferred binding has no annotation in the AST, and module
        # reference analysis reads the AST. It must skip them rather than
        # handing None to the type parser.
        from kosika.modules import load_program

        with tempfile.TemporaryDirectory() as directory:
            main = Path(directory) / "main.kos"
            main.write_text(
                "record Out { v: Number }\n"
                "fn wrap(n: Number) -> Out { return {v: n} }\n"
                "fn f() -> Number { let x = 1 let y = x + 1 return y }\n"
                "workflow main() -> Out { step s { let o: Out = wrap(n: f()) return o } }\n"
            )
            program = load_program(main)
            check(program)


class AstStabilityTests(unittest.TestCase):
    def test_body_without_statements_keeps_the_original_ast_shape(self):
        program = parse("fn f() -> Number { let x: Number = 1 return x }")
        function = program.functions[0]
        self.assertEqual(function.statements, ())
        self.assertEqual(
            function.bindings,
            (LocalBinding("x", "Number", function.bindings[0].value),),
        )

    def test_call_collection_includes_statements(self):
        source = """
        fn inner(x: Number) -> Number { return x }
        fn outer(n: Number) -> Number {
          var t = 0
          repeat 2 times { t = inner(x: t) + 1 }
          return t
        }
        """
        program = parse(source)
        check(program)
        from kosika.ast import function_calls

        names = {call.function_name for call in function_calls(program.functions[1])}
        self.assertIn("inner", names)


if __name__ == "__main__":
    unittest.main()
