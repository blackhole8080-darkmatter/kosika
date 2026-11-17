"""Tests for `use_model ... with evidence`.

The guarantee under test: a plain model step can require its answer to quote
the text it was given, the quote is checked before the value is bound, and the
check is recorded so a trace can be replayed against it. A program that asks
for no evidence must be untouched.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from kosika.ast import ModelCall
from kosika.cli import main
from kosika.evidence import EvidenceError, verify
from kosika.model_evidence import KIND, lower_model_evidence
from kosika.parser import parse

PROGRAM = """record Question { text: Text }
record Answer { text: Text }

model assistant { input: Question output: Answer }

workflow main(question: Question) -> Answer {
  answer = use_model assistant(question.text) with evidence
}
"""

FALLBACK = """record Question { text: Text }
record Answer { text: Text }

model assistant { input: Question output: Answer }

workflow main(question: Question) -> Answer {
  answer = use_model assistant(question.text) with evidence otherwise {text: "not stated"}
}
"""

CHOICE = """record Question { text: Text }

choice Verdict { yes(why: Text) no(why: Text) }

model picker { input: Question output: Verdict }

workflow main(question: Question) -> Verdict {
  answer = use_model picker(question.text) with evidence
}
"""

TEXT = "Rent is 900 euros a month."


class LoweringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.program = parse(PROGRAM)

    def test_the_call_is_replaced_by_a_generated_model(self) -> None:
        call = self.program.workflows[0].steps[0].call
        assert isinstance(call, ModelCall)
        self.assertEqual(call.model_name, "assistant/evidence")

    def test_the_generated_model_asks_the_declared_one(self) -> None:
        generated = [
            model for model in self.program.models if model.name == "assistant/evidence"
        ]
        self.assertEqual(len(generated), 1)
        self.assertEqual(generated[0].provider, "assistant")
        self.assertEqual(generated[0].evidence, KIND)
        self.assertEqual(generated[0].output, "Answer")

    def test_the_quote_rule_is_added_to_the_instructions(self) -> None:
        generated = next(
            model for model in self.program.models if model.name == "assistant/evidence"
        )
        self.assertIn("exact quotes", generated.instruction or "")

    def test_a_program_without_evidence_is_untouched(self) -> None:
        plain = parse(PROGRAM.replace(" with evidence", ""))
        self.assertEqual([model.name for model in plain.models], ["assistant"])
        plain_call = plain.workflows[0].steps[0].call
        assert isinstance(plain_call, ModelCall)
        self.assertIsNone(plain_call.evidence)
        self.assertIs(lower_model_evidence(plain), plain)

    def test_an_undeclared_model_is_left_for_the_checker(self) -> None:
        # The call names a model the program never declares: lowering must not
        # invent one, or the checker's own unknown-model failure would vanish.
        program = parse(PROGRAM.replace("use_model assistant", "use_model ghost"))
        self.assertEqual([model.name for model in program.models], ["assistant"])
        ghost_call = program.workflows[0].steps[0].call
        assert isinstance(ghost_call, ModelCall)
        self.assertEqual(ghost_call.model_name, "ghost")


class InputRuleTests(unittest.TestCase):
    def test_a_quote_from_the_given_text_passes(self) -> None:
        verify(KIND, {"question": TEXT}, {"text": "900"}, ["Rent is 900 euros"])

    def test_an_invented_quote_is_refused(self) -> None:
        with self.assertRaises(EvidenceError):
            verify(KIND, {"question": TEXT}, {"text": "1200"}, ["Rent is 1200 euros"])

    def test_a_quote_is_required(self) -> None:
        with self.assertRaises(EvidenceError):
            verify(KIND, {"question": TEXT}, {"text": "900"}, [])

    def test_case_and_spacing_are_forgiven_but_paraphrase_is_not(self) -> None:
        verify(KIND, {"question": TEXT}, {"text": "900"}, ["rent is 900 EUROS"])
        with self.assertRaises(EvidenceError):
            verify(KIND, {"question": TEXT}, {"text": "900"}, ["the rent"])

    def test_every_text_argument_is_a_source(self) -> None:
        verify(
            KIND,
            {"question": TEXT, "aside": "Paid on the first."},
            {"text": "900"},
            ["Paid on the first"],
        )


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        self.inputs = self.root / "input.json"
        self.inputs.write_text(
            json.dumps({"question": {"text": TEXT}}), encoding="utf-8"
        )

    def run_cli(self, program: str, answer: dict, *extra: str) -> tuple[int, str]:
        source = self.root / "main.kos"
        source.write_text(program, encoding="utf-8")
        fixture = self.root / "answers.json"
        fixture.write_text(json.dumps({"assistant": [answer]}), encoding="utf-8")
        argv = [
            "kosika",
            "run",
            str(source),
            "--input",
            str(self.inputs),
            "--model-fixture",
            str(fixture),
            *extra,
        ]
        out, err = StringIO(), StringIO()
        with (
            patch.object(sys, "argv", argv),
            redirect_stdout(out),
            redirect_stderr(err),
        ):
            code = main()
        return code, out.getvalue() + err.getvalue()

    def test_a_quoted_answer_runs(self) -> None:
        code, output = self.run_cli(
            PROGRAM,
            {"text": "900 euros a month", "evidence": ["Rent is 900 euros"]},
        )
        self.assertEqual(code, 0, output)
        self.assertIn("900 euros a month", output)

    def test_an_invented_quote_stops_the_run(self) -> None:
        code, output = self.run_cli(
            PROGRAM,
            {"text": "1200 euros", "evidence": ["Rent is 1200 euros"]},
        )
        self.assertNotEqual(code, 0, output)
        self.assertIn("without evidence", output)

    def test_otherwise_recovers_from_an_invented_quote(self) -> None:
        code, output = self.run_cli(
            FALLBACK,
            {"text": "1200 euros", "evidence": ["Rent is 1200 euros"]},
        )
        self.assertEqual(code, 0, output)
        self.assertIn("not stated", output)

    def test_a_choice_model_cannot_give_evidence(self) -> None:
        source = self.root / "choice.kos"
        source.write_text(CHOICE, encoding="utf-8")
        out, err = StringIO(), StringIO()
        with (
            patch.object(sys, "argv", ["kosika", "check", str(source)]),
            redirect_stdout(out),
            redirect_stderr(err),
        ):
            code = main()
        self.assertEqual(code, 2)
        self.assertIn("cannot give evidence", out.getvalue() + err.getvalue())

    def test_a_trace_replays_with_its_quotes(self) -> None:
        trace = self.root / "run.jsonl"
        code, output = self.run_cli(
            PROGRAM,
            {"text": "900 euros", "evidence": ["Rent is 900 euros"]},
            "--trace",
            str(trace),
        )
        self.assertEqual(code, 0, output)
        self.assertTrue(trace.exists())
        out, err = StringIO(), StringIO()
        with (
            patch.object(sys, "argv", ["kosika", "replay", str(trace)]),
            redirect_stdout(out),
            redirect_stderr(err),
        ):
            replay_code = main()
        self.assertEqual(replay_code, 0, out.getvalue() + err.getvalue())


if __name__ == "__main__":
    unittest.main()
