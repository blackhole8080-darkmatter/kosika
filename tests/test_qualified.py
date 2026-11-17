"""Tests for holding a run to a recorded qualification.

The rule under test: a model may answer only if the ledger holds an
evaluation of it that showed no wrong answer to a user and did not fail. A
model with no record is refused, because "nobody checked" is not a pass.
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

from kosika import qualified
from kosika.cli import main
from kosika.parser import parse
from kosika.virtual_eval import LEDGER_SCHEMA, VirtualEvalError

PROGRAM = """record Ask { text: Text }
record Answer { text: Text }

model assistant { input: Ask output: Answer }

workflow main(ask: Ask) -> Answer {
  answer = use_model assistant(ask.text)
}
"""


def ledger(path: Path, *entries: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema": LEDGER_SCHEMA, "evaluations": list(entries)}),
        encoding="utf-8",
    )


def entry(model: str, **counts: int) -> dict:
    base = {"right": 0, "wrong shown": 0, "missed": 0, "failed": 0}
    base.update(counts)
    return {
        "schema": "kosika.virtual-model-eval/v1",
        "identity": "0" * 64,
        "model": model,
        "asked": sum(base.values()),
        "counts": base,
    }


class AskedModelsTests(unittest.TestCase):
    def test_a_declared_model_is_the_one_asked(self) -> None:
        program = parse(PROGRAM)
        self.assertEqual(qualified.asked_models(program), ("assistant",))

    def test_a_generated_model_is_followed_to_the_one_it_was_made_from(self) -> None:
        program = parse(
            PROGRAM.replace("with evidence", "").replace(
                "use_model assistant(ask.text)",
                "use_model assistant(ask.text) with evidence",
            )
        )
        self.assertEqual(qualified.asked_models(program), ("assistant",))

    def test_a_declared_deployment_is_what_is_asked(self) -> None:
        program = parse(
            PROGRAM.replace(
                "model assistant { input: Ask output: Answer }",
                'model assistant { input: Ask output: Answer deployment: "qwen2.5:7b" }',
            )
        )
        self.assertEqual(qualified.asked_models(program), ("qwen2.5:7b",))

    def test_the_override_names_what_runs(self) -> None:
        self.assertEqual(qualified.override_names("fast,strong=big"), ("fast", "big"))
        self.assertEqual(qualified.override_names(None), ())

    def test_a_program_that_asks_no_model_asks_nothing_of_the_ledger(self) -> None:
        program = parse(
            "record Ask { text: Text }\n"
            "workflow main(ask: Ask) -> Ask {\n  answer = ask\n}\n"
        )
        self.assertEqual(qualified.asked_models(program), ())


class QualifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.state = Path(self.workspace.name) / "virtual-models.json"

    def test_no_ledger_is_not_a_pass(self) -> None:
        verdict = qualified.qualify(self.state, ("assistant",))
        self.assertFalse(verdict.allowed)
        self.assertIn("no recorded evaluation of assistant", verdict.as_text())
        self.assertIn("virtual-model eval", verdict.as_text())

    def test_a_recorded_pass_lets_the_run_go_ahead(self) -> None:
        ledger(self.state, entry("assistant", right=40))
        verdict = qualified.qualify(self.state, ("assistant",))
        self.assertTrue(verdict.allowed, verdict.as_text())

    def test_a_shown_wrong_answer_is_refused(self) -> None:
        ledger(self.state, entry("assistant", right=39, **{"wrong shown": 1}))
        verdict = qualified.qualify(self.state, ("assistant",))
        self.assertFalse(verdict.allowed)
        self.assertIn("1 wrong answer", verdict.as_text())

    def test_a_failed_evaluation_is_refused(self) -> None:
        ledger(self.state, entry("assistant", right=30, failed=10))
        verdict = qualified.qualify(self.state, ("assistant",))
        self.assertFalse(verdict.allowed)
        self.assertIn("failed 10 answer", verdict.as_text())

    def test_an_evaluation_that_asked_nothing_is_refused(self) -> None:
        ledger(self.state, entry("assistant"))
        verdict = qualified.qualify(self.state, ("assistant",))
        self.assertFalse(verdict.allowed)
        self.assertIn("recorded no answers", verdict.as_text())

    def test_the_latest_evaluation_of_that_model_is_the_one_that_counts(self) -> None:
        ledger(
            self.state,
            entry("assistant", **{"wrong shown": 3}),
            entry("assistant", right=40),
        )
        self.assertTrue(qualified.qualify(self.state, ("assistant",)).allowed)

    def test_another_models_pass_does_not_count(self) -> None:
        ledger(self.state, entry("other", right=40))
        verdict = qualified.qualify(self.state, ("assistant",))
        self.assertFalse(verdict.allowed)

    def test_every_model_the_run_asks_must_be_qualified(self) -> None:
        ledger(self.state, entry("fast", right=40))
        verdict = qualified.qualify(self.state, ("fast", "strong"))
        self.assertFalse(verdict.allowed)
        self.assertIn("no recorded evaluation of strong", verdict.as_text())

    def test_a_broken_ledger_is_reported_rather_than_ignored(self) -> None:
        self.state.write_text("{not json", encoding="utf-8")
        with self.assertRaises(VirtualEvalError):
            qualified.qualify(self.state, ("assistant",))


class CommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        self.source = self.root / "main.kos"
        self.source.write_text(PROGRAM, encoding="utf-8")
        self.inputs = self.root / "input.json"
        self.inputs.write_text(
            '{"ask": {"text": "Rent is 900 euros"}}', encoding="utf-8"
        )
        self.fixture = self.root / "answers.json"
        self.fixture.write_text(
            json.dumps({"assistant": [{"text": "900"}]}), encoding="utf-8"
        )
        self.state = self.root / "state.json"

    def run_cli(self, *extra: str) -> tuple[int, str]:
        argv = [
            "kosika",
            "run",
            str(self.source),
            "--input",
            str(self.inputs),
            "--model-fixture",
            str(self.fixture),
            "--trace",
            str(self.root / "trace.jsonl"),
            "--qualifications",
            str(self.state),
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

    def test_without_the_flag_nothing_is_asked_of_the_ledger(self) -> None:
        code, printed = self.run_cli()
        self.assertEqual(code, 0, printed)

    def test_an_unqualified_model_is_refused_before_it_answers(self) -> None:
        code, printed = self.run_cli("--require-qualified")
        self.assertEqual(code, 2)
        self.assertIn("no recorded evaluation of assistant", printed)

    def test_a_qualified_model_runs(self) -> None:
        ledger(self.state, entry("assistant", right=40))
        code, printed = self.run_cli("--require-qualified")
        self.assertEqual(code, 0, printed)
        self.assertIn("900", printed)

    def test_a_model_that_showed_a_wrong_answer_is_refused(self) -> None:
        ledger(self.state, entry("assistant", right=39, **{"wrong shown": 1}))
        code, printed = self.run_cli("--require-qualified")
        self.assertEqual(code, 2)
        self.assertIn("wrong answer", printed)


if __name__ == "__main__":
    unittest.main()
