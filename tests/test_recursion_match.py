import tempfile
import unittest
from pathlib import Path

from kosika.checker import CheckError, check
from kosika.devtools import format_source
from kosika.modules import load_program
from kosika.parser import ParseError, parse
from kosika.runtime import replay, run


def runnable(function: str, call: str = "compute()") -> str:
    return (
        "record Result { value: Number } "
        + function
        + " fn wrap() -> Result { return {value: "
        + call
        + "} } "
        "workflow main() -> Result { step get { let result = wrap() return result } }"
    )


class RecursionMatchTests(unittest.TestCase):
    def test_example_executes_and_replays(self) -> None:
        program = load_program(
            Path(__file__).resolve().parents[1] / "examples/recursion_match.kos"
        )
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            expected = {"factorial": 120, "parity": "odd", "classification": "zero"}
            self.assertEqual(run(program, "main", trace), expected)
            self.assertEqual(replay(trace), expected)

    def test_recursion_limit_is_enforced(self) -> None:
        source = runnable("fn compute() -> Number recurse 3 { return compute() }")
        check(parse(source))
        with self.assertRaisesRegex(RuntimeError, "Recursion limit 3"):
            run(parse(source), "main", None)

    def test_exact_per_function_and_global_depth_boundaries(self) -> None:
        function = (
            "fn compute(n: Number) -> Number recurse 3 { "
            "return match n { case 0 { 9 } case _ { compute(n: n - 1) } } }"
        )
        self.assertEqual(
            run(parse(runnable(function, "compute(n: 2)")), "main", None), {"value": 9}
        )
        with self.assertRaisesRegex(RuntimeError, "Recursion limit 3"):
            run(parse(runnable(function, "compute(n: 3)")), "main", None)
        function = function.replace("recurse 3", "recurse 128")
        self.assertEqual(
            run(parse(runnable(function, "compute(n: 126)")), "main", None), {"value": 9}
        )
        with self.assertRaisesRegex(RuntimeError, "Function call depth exceeds 128"):
            run(parse(runnable(function, "compute(n: 127)")), "main", None)

    def test_branching_recursion_has_total_call_budget(self) -> None:
        source = runnable(
            "fn compute(n: Number) -> Number recurse 32 { "
            "return if n == 0 { 1 } else { compute(n: n - 1) + compute(n: n - 1) } }",
            "compute(n: 14)",
        )
        with self.assertRaisesRegex(RuntimeError, "call budget"):
            run(parse(source), "main", None)

    def test_recursion_requires_valid_explicit_limits(self) -> None:
        for limit in ("0", "129", "-1", "2.5", "true"):
            with self.subTest(limit=limit), self.assertRaises(ParseError):
                parse(f"fn compute() -> Number recurse {limit} {{ return 1 }}")
        source = (
            "fn a() -> Number recurse 8 { return b() + c() } "
            "fn b() -> Number recurse 8 { return a() } "
            "fn c() -> Number { return b() }"
        )
        with self.assertRaisesRegex(CheckError, "Recursive"):
            check(parse(source))

    def test_match_rejects_incomplete_duplicate_and_mismatched_patterns(self) -> None:
        for expression in (
            "match 1 { case 1 { 7 } }",
            "match 1 { case 1 { 7 } case 1.0 { 8 } case _ { 0 } }",
            "match 1 { case true { 7 } case _ { 0 } }",
            "match 1 { case _ { 7 } case 2 { 0 } }",
            'match true { case true { 7 } case false { "no" } }',
            "match null { }",
        ):
            with self.subTest(expression=expression), self.assertRaises(CheckError):
                check(parse("fn compute() -> Number { return " + expression + " }"))

    def test_scalar_patterns_are_lazy_and_lexically_captured(self) -> None:
        for subject, pattern in (
            ('"match"', '"match"'),
            ("null", "null"),
            ("-2", "-2"),
        ):
            fallback = "" if subject == "null" else "case _ { 1 / 0 }"
            source = runnable(
                "fn compute() -> Number { let captured: Number = 9 "
                "fn inner() -> Number { return match "
                + subject
                + " { case "
                + pattern
                + " { captured } "
                + fallback
                + " } } return inner() }"
            )
            self.assertEqual(parse(format_source(source)), parse(source))
            self.assertEqual(run(parse(source), "main", None), {"value": 9})

    def test_escaped_recursive_closure(self) -> None:
        source = (
            "record Result { value: Number } "
            "fn make(base: Number) -> fn(n: Number) -> Number { "
            "fn count(n: Number) -> Number recurse 8 { "
            "return match n { case 0 { base } case _ { 1 + count(n: n - 1) } } } "
            "return ref count } "
            "fn apply(callback: fn(n: Number) -> Number) -> Result { return {value: callback(n: 3)} } "
            "workflow main() -> Result { step make_it { let callback = make(base: 10) return callback } "
            "step use { let result = apply(callback: callback) return result } }"
        )
        self.assertEqual(run(parse(source), "main", None), {"value": 13})
