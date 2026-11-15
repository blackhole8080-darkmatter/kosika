import tempfile
import unittest
from pathlib import Path

from kosika.checker import CheckError, check
from kosika.modules import load_program
from kosika.parser import parse
from kosika.runtime import replay, run
from kosika.schema import SchemaValidationError, schema_for_type, validate

SOURCE = """
choice Filing {
  filed(invoice: Text)
  refused(reason: Text)
  pending
}

record Ticket { filing: Filing }
record Said { text: Text }

fn describe(f: Filing) -> Text {
  return match f {
    case filed(invoice) { "filed " + invoice }
    case refused(reason) { "refused: " + reason }
    case pending { "waiting" }
  }
}

workflow main(ticket: Ticket) -> Said {
  said = describe(ticket.filing)
  out = {text: said}
}
"""


class ChoiceTests(unittest.TestCase):
    def test_alternatives_match_and_bind_their_fields(self) -> None:
        program = parse(SOURCE)
        check(program)
        cases = {
            "filed": ({"$case": "filed", "invoice": "A-1"}, "filed A-1"),
            "refused": ({"$case": "refused", "reason": "no"}, "refused: no"),
            "pending": ({"$case": "pending"}, "waiting"),
        }
        for filing, expected in cases.values():
            with tempfile.TemporaryDirectory() as directory:
                trace = Path(directory) / "trace.jsonl"
                result = run(
                    program,
                    "main",
                    trace,
                    inputs={"ticket": {"filing": filing}},
                )
                self.assertEqual(result, {"text": expected})
                self.assertEqual(replay(trace), result)

    def test_a_choice_field_has_a_schema_that_rejects_other_values(self) -> None:
        program = parse(SOURCE)
        check(program)
        schema = schema_for_type(program, "Filing")
        validate({"$case": "filed", "invoice": "A-1"}, schema)
        validate({"$case": "pending"}, schema)
        for value in (
            {"$case": "unknown"},
            {"$case": "filed"},
            {"$case": "filed", "invoice": "A-1", "extra": 1},
        ):
            with self.subTest(value=value):
                with self.assertRaises(SchemaValidationError):
                    validate(value, schema)

    def test_an_unknown_boundary_value_is_refused_before_the_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "matches no allowed form"):
                run(
                    parse(SOURCE),
                    "main",
                    Path(directory) / "trace.jsonl",
                    inputs={"ticket": {"filing": {"$case": "unknown"}}},
                )

    def test_every_alternative_must_be_matched(self) -> None:
        source = """
        choice Filing { filed(invoice: Text) pending }
        fn describe(f: Filing) -> Text {
          return match f { case filed(invoice) { invoice } }
        }
        """
        with self.assertRaisesRegex(CheckError, "add case pending"):
            check(parse(source))

    def test_a_case_must_name_an_alternative_of_its_subject(self) -> None:
        source = """
        choice Filing { filed(invoice: Text) pending }
        fn describe(f: Filing) -> Text {
          return match f { case refused(reason) { reason } case _ { "x" } }
        }
        """
        with self.assertRaisesRegex(CheckError, "has no alternative 'refused'"):
            check(parse(source))

    def test_a_case_binds_exactly_the_fields_its_alternative_carries(self) -> None:
        source = """
        choice Filing { filed(invoice: Text) pending }
        fn describe(f: Filing) -> Text {
          return match f { case filed(a, b) { a } case pending { "p" } }
        }
        """
        with self.assertRaisesRegex(CheckError, "binds that many names"):
            check(parse(source))

    def test_a_function_may_return_a_constructor(self) -> None:
        # A constructor is written like a call, but it is not a function to
        # recurse into or to reach.
        source = """
        choice Filing { filed(invoice: Text) pending }
        fn file(invoice: Text) -> Filing { return filed(invoice) }
        test "a constructor comes back out" {
          filing = file("A-1")
          expect filing.invoice == "A-1"
        }
        """
        check(parse(source))

    def test_alternative_names_are_unique_across_choices(self) -> None:
        source = """
        choice One { ready }
        choice Two { ready }
        """
        with self.assertRaisesRegex(CheckError, "both name 'ready'"):
            check(parse(source))

    def test_alternative_cannot_share_a_choice_name(self) -> None:
        source = """
        choice One { Two }
        choice Two { ready }
        """
        with self.assertRaisesRegex(CheckError, "has the name of a"):
            check(parse(source))

    def test_alternative_cannot_share_a_function_name(self) -> None:
        source = """
        choice One { ready }
        fn ready() -> Text { return "x" }
        """
        with self.assertRaisesRegex(CheckError, "has the name of a"):
            check(parse(source))

    def test_a_choice_and_its_alternatives_survive_an_import(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "lib").mkdir()
            (root / "lib" / "types.kos").write_text(
                'export [Filing]\nchoice Filing { filed(invoice: Text) pending }\n',
                encoding="utf-8",
            )
            (root / "main.kos").write_text(
                'import "lib/types.kos"\n'
                "fn describe(f: Filing) -> Text {\n"
                "  return match f {\n"
                '    case filed(invoice) { "filed " + invoice }\n'
                '    case pending { "waiting" }\n'
                "  }\n"
                "}\n"
                "record Ticket { filing: Filing }\n"
                "workflow main(ticket: Ticket) -> Said {\n"
                "  said = describe(ticket.filing)\n"
                '  out = {text: said}\n'
                "}\n"
                "record Said { text: Text }\n",
                encoding="utf-8",
            )
            program = load_program(root / "main.kos")
            trace = root / "trace.jsonl"
            result = run(
                program,
                "main",
                trace,
                inputs={"ticket": {"filing": {"$case": "pending"}}},
            )
            self.assertEqual(result, {"text": "waiting"})
            self.assertEqual(replay(trace), result)


if __name__ == "__main__":
    unittest.main()
