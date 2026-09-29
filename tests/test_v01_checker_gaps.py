"""Regression tests for the three undeclared gaps found in the v0.1 checker.

These paths had no coverage, which is how an undefined-name crash survived.
Each test pins the *current* honest behaviour so a future change has to be
deliberate: the checker never crashes, and nothing the checker accepts can
quietly produce a non-deterministic trace.
"""

import json
import tempfile
import unittest
from pathlib import Path

from kosika.checker import CheckError, check
from kosika.parser import ParseError, parse
from kosika.runtime import run

# A non-capturing inline function: the checker used to raise NameError here
# because it called `function_type` and `Field` without importing them.
INLINE = """
record Result { value: Number }
fn apply(op: fn(value: Number) -> Number, value: Number) -> Result {
  return {value: op(value: value)}
}
workflow main() -> Result {
  step use {
    let result = apply(op: fn(value: Number) -> Number { value * 2 }, value: 5)
    return result
  }
}
"""

# An inline function that reads the enclosing scope. The runtime has no
# capture support for inline functions, so this must be rejected, not accepted
# and then failed at run time.
CAPTURING = """
record Result { value: Number }
fn compose(f: fn(value: Number) -> Number) -> fn(value: Number) -> Number {
  return fn(value: Number) -> Number { f(value: value) }
}
fn apply(op: fn(value: Number) -> Number, value: Number) -> Result {
  return {value: op(value: value)}
}
workflow main() -> Result {
  step create {
    let composed = compose(f: fn(value: Number) -> Number { value + 1 })
    return composed
  }
  step use {
    let result = apply(op: composed, value: 5)
    return result
  }
}
"""


class InlineFunctionGapTests(unittest.TestCase):
    def test_non_capturing_inline_function_checks(self) -> None:
        # The import fix: this raised NameError before.
        check(parse(INLINE))

    def test_capturing_inline_function_is_rejected(self) -> None:
        with self.assertRaises(CheckError):
            check(parse(CAPTURING))

    def test_inline_function_is_refused_at_run_time(self) -> None:
        # The checker accepts the non-capturing form but the runtime cannot
        # execute it. That gap is asserted here so it stays visible instead of
        # being discovered in production.
        with self.assertRaises(RuntimeError) as caught:
            run(parse(INLINE), "main", None)
        self.assertIn("unsupported expression", str(caught.exception))

    def test_no_synthetic_function_name_reaches_a_trace(self) -> None:
        # Guards the determinism guarantee: a function value in a trace must
        # name a declared function, never an object identity or a synthetic
        # label. This is the failure mode of a naive inline-function runtime.
        source = (
            "record Result { value: Number }\n"
            "fn identity(value: Number) -> Result { return {value: value} }\n"
            "workflow main() -> Result {\n"
            "  step use { let result = identity(value: 5) return result }\n"
            "}\n"
        )
        declared = {function.name for function in parse(source).functions}
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            run(parse(source), "main", trace)
            named: set[str] = set()
            for line in trace.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                if isinstance(event.get("function"), str):
                    named.add(event["function"])
        self.assertTrue(named)
        self.assertTrue(
            named <= declared, f"synthetic names in trace: {named - declared}"
        )


class MethodCallGapTests(unittest.TestCase):
    def test_method_calls_parse(self) -> None:
        # parse_arguments already consumes the closing paren, so a second
        # expect(")") made every method call a ParseError. Zero-argument calls
        # are the common case, so both shapes are pinned.
        for source in (
            "fn size(text: Text) -> Number { return text.length() }",
            'fn parts(text: Text) -> Text[] { return text.split(sep: ",") }',
            'fn joined(items: Text[]) -> Text { return items.join(sep: ",") }',
        ):
            with self.subTest(source=source):
                parse(source)

    def test_method_calls_are_rejected_with_a_clear_error(self) -> None:
        # Method dispatch has no runtime implementation. It must fail at check
        # time with a CheckError, never NameError, and never pass.
        for source, method in (
            ("fn size(text: Text) -> Number { return text.length() }", "length"),
            ('fn parts(text: Text) -> Text[] { return text.split(sep: ",") }', "split"),
            (
                'fn joined(items: Text[]) -> Text { return items.join(sep: ",") }',
                "join",
            ),
        ):
            with self.subTest(source=source), self.assertRaises(CheckError) as caught:
                check(parse(source))
            self.assertIn(method, str(caught.exception))
            self.assertIn("not implemented", str(caught.exception))

    def test_declare_nested_call_is_not_mistaken_for_a_method(self) -> None:
        # Field access must keep working; only a trailing "(" is a method call.
        source = (
            "record Result { value: Number }\n"
            "fn read(result: Result) -> Number { return result.value }\n"
            "fn wrap(value: Number) -> Result { return {value: value} }\n"
            "workflow main() -> Result {\n"
            "  step use { let n = read(result: {value: 1}) return n }\n"
            "  step final { let result = wrap(value: n) return result }\n"
            "}\n"
        )
        self.assertEqual(run(parse(source), "main", None), {"value": 1})


class ParseErrorIsNotACheckErrorTests(unittest.TestCase):
    def test_parse_error_is_raised_for_broken_syntax(self) -> None:
        with self.assertRaises(ParseError):
            parse("fn broken( -> Number { return 1 }")


if __name__ == "__main__":
    unittest.main()
