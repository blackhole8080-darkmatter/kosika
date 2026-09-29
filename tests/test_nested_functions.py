import tempfile
import unittest
from pathlib import Path

from kosika.checker import CheckError, check
from kosika.devtools import format_source
from kosika.modules import load_program
from kosika.parser import ParseError, parse
from kosika.runtime import replay, run

SOURCE = """
record Result { text: Number }
fn outer(text: Number) -> Result {
  let suffix: Number = 1
  fn read() -> Number { return text + suffix }
  fn shadow(text: Number) -> Number {
    fn deeper() -> Number { return read() + text }
    return deeper()
  }
  return {text: shadow(text: 2)}
}
workflow main() -> Result {
  step first { let one = outer(text: 10) return one }
  step second { let two = outer(text: 20) return two }
}
"""


class NestedFunctionTests(unittest.TestCase):
    def test_helpers_in_distinct_scopes_and_structured_captures(self) -> None:
        source = """
        record Box { items: Number[] }
        record Result { value: Number }
        fn first(box: Box) -> Number {
          fn get() -> Number { return box.items[0] }
          return get()
        }
        fn second(box: Box) -> Number {
          fn get() -> Number { return box.items[1] }
          return get()
        }
        fn total(box: Box) -> Result { return {value: first(box: box) + second(box: box)} }
        workflow main() -> Result {
          step get { let result = total(box: {items: [2, 5]}) return result }
        }
        """
        self.assertEqual(run(parse(source), "main", None), {"value": 7})

    def test_nested_functions_cannot_execute_effects(self) -> None:
        with self.assertRaises(ParseError):
            parse(
                "fn outer() -> Text { fn inner() -> Text { "
                "return execute agent.tool() } return inner() }"
            )

    def test_lexical_capture_shadowing_and_fresh_invocations(self) -> None:
        program = parse(SOURCE)
        check(program)
        self.assertEqual(parse(format_source(SOURCE)), program)
        self.assertIn("outer/shadow/deeper", [fn.name for fn in program.functions])
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            result = run(program, "main", trace)
            self.assertEqual(result, {"text": 23})
            self.assertEqual(replay(trace), result)

    def test_nested_names_do_not_escape_their_scope(self) -> None:
        source = SOURCE.replace("outer(text: 20)", "read()")
        with self.assertRaisesRegex(CheckError, "unknown function"):
            check(parse(source))

    def test_nested_recursion_and_duplicate_names_are_rejected(self) -> None:
        for body in (
            "fn a() -> Text { return a() } return a()",
            "fn a() -> Text { return b() } fn b() -> Text { return a() } return a()",
        ):
            with (
                self.subTest(body=body),
                self.assertRaisesRegex(CheckError, "Recursive"),
            ):
                check(parse("fn outer() -> Text { " + body + " }"))
        with self.assertRaisesRegex(ParseError, "Duplicate nested"):
            parse(
                'fn outer() -> Text { fn a() -> Text { return "a" } '
                'fn a() -> Text { return "b" } return a() }'
            )

    def test_captures_are_type_checked_and_not_dynamic(self) -> None:
        for source in (
            "fn outer(text: Text) -> Number { fn inner() -> Number { return text } return inner() }",
            "fn outer() -> Text { fn inner() -> Text { return missing } return inner() }",
            "fn outer() -> Text { let value: Text = inner() "
            'fn inner() -> Text { return "ok" } return value }',
        ):
            with self.subTest(source=source), self.assertRaises(CheckError):
                check(parse(source))

    def test_module_exports_keep_nested_functions_private(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "library.kos").write_text(
                "export [Result, outer]\n" + SOURCE.split("workflow main")[0],
                encoding="utf-8",
            )
            entry = root / "main.kos"
            entry.write_text(
                'import "library.kos"\nworkflow main() -> Result { '
                "step get { let result = outer(text: 10) return result } }",
                encoding="utf-8",
            )
            self.assertEqual(run(load_program(entry), "main", None), {"text": 13})
            (root / "library.kos").write_text(
                "export [Result, outer, read]\n" + SOURCE.split("workflow main")[0],
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CheckError, "exports"):
                load_program(entry)

    def test_declaration_nesting_is_bounded(self) -> None:
        body = 'return "ok"'
        for index in range(33):
            body = f"fn f{index}() -> Text {{ {body} }} return f{index}()"
        with self.assertRaisesRegex(ParseError, "nesting exceeds"):
            parse("fn outer() -> Text { " + body + " }")
