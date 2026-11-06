"""Tests for qualifying a virtual model from recorded evidence.

The contract: a combination (profile, model, question set) is qualified only
when a recorded run shows it answered, showed no wrong answer, and finished.
These tests pin that boundary in both directions, because a qualification that
passes too easily is worse than none.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from kosika.cli import main
from kosika.virtual_eval import (
    CLASSES,
    VirtualEvalError,
    compare,
    load_recorded,
    qualify,
    read_ledger,
    record_eval,
    render_text,
    score,
)


def recorded(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "when": "20260101-000000",
        "label": "fixed",
        "set": "first",
        "model": "qwen2.5:7b",
        "embed_model": "",
        "questions": [
            {"outcomes": ["right"], "said": ["a"]},
            {"outcomes": ["right"], "said": ["b"]},
            {"outcomes": ["wrong shown"], "said": ["c"]},
            {"outcomes": ["missed"], "said": ["d"]},
        ],
    }
    payload.update(overrides)
    return payload


class RecordedRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)

    def write(self, payload: object, name: str = "run.json") -> Path:
        path = self.root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_it_counts_every_recorded_outcome(self) -> None:
        run = load_recorded(self.write(recorded()))
        self.assertEqual(run.asked, 4)
        self.assertEqual(run.counts, {"right": 2, "wrong shown": 1, "missed": 1, "failed": 0})
        self.assertEqual(run.model, "qwen2.5:7b")
        self.assertEqual(run.question_set, "first")

    def test_several_attempts_per_question_are_all_counted(self) -> None:
        payload = recorded(questions=[{"outcomes": ["right", "missed"]}])
        self.assertEqual(load_recorded(self.write(payload)).asked, 2)

    def test_an_unknown_outcome_is_refused(self) -> None:
        payload = recorded(questions=[{"outcomes": ["great"]}])
        with self.assertRaises(VirtualEvalError):
            load_recorded(self.write(payload))

    def test_a_run_without_questions_is_refused(self) -> None:
        with self.assertRaises(VirtualEvalError):
            load_recorded(self.write(recorded(questions=[])))

    def test_a_run_without_a_model_is_refused(self) -> None:
        with self.assertRaises(VirtualEvalError):
            load_recorded(self.write(recorded(model="")))


class QualificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)

    def score_of(self, payload: object) -> object:
        path = self.root / "run.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return score(path)

    def clean(self) -> dict[str, object]:
        return recorded(
            questions=[{"outcomes": ["right"]}, {"outcomes": ["right"]}]
        )

    def test_a_clean_run_qualifies(self) -> None:
        ok, reasons = qualify(self.score_of(self.clean()))
        self.assertTrue(ok)
        self.assertEqual(reasons, [])

    def test_a_shown_wrong_answer_refuses_it(self) -> None:
        ok, reasons = qualify(self.score_of(recorded()))
        self.assertFalse(ok)
        self.assertTrue(any("wrong answer" in reason for reason in reasons))

    def test_allowing_a_wrong_answer_is_explicit(self) -> None:
        ok, reasons = qualify(self.score_of(recorded()), require_no_wrong=False)
        self.assertTrue(ok, reasons)

    def test_a_failed_answer_refuses_it(self) -> None:
        payload = recorded(questions=[{"outcomes": ["right", "failed"]}])
        ok, reasons = qualify(self.score_of(payload))
        self.assertFalse(ok)
        self.assertTrue(any("failed to run" in reason for reason in reasons))

    def test_the_floor_refuses_a_low_accuracy(self) -> None:
        ok, reasons = qualify(self.score_of(recorded()), min_right=0.9)
        self.assertFalse(ok)
        self.assertTrue(any("below the floor" in reason for reason in reasons))

    def test_the_summary_names_every_class(self) -> None:
        text = render_text(self.score_of(recorded()))
        for name in CLASSES:
            self.assertIn(name, text)


class IdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        self.profile = self.root / "profile.json"
        self.profile.write_text('{"name": "notes"}', encoding="utf-8")
        (self.root / "run.json").write_text(json.dumps(recorded()), encoding="utf-8")

    def run_payload(self, **overrides: object) -> dict[str, object]:
        path = self.root / "run.json"
        path.write_text(json.dumps(recorded(**overrides)), encoding="utf-8")
        return load_recorded(path)

    def test_editing_the_profile_changes_the_identity(self) -> None:
        self.run_payload()
        first = score(self.root / "run.json", profile=self.profile).identity
        self.profile.write_text('{"name": "notes", "instructions": "be brief"}')
        second = score(self.root / "run.json", profile=self.profile).identity
        self.assertNotEqual(first, second)

    def test_a_different_model_is_a_different_combination(self) -> None:
        first = score(self.root / "run.json").identity
        path = self.root / "other.json"
        path.write_text(json.dumps(recorded(model="qwen2.5:1.5b")), encoding="utf-8")
        self.assertNotEqual(first, score(path).identity)

    def test_the_label_and_timestamp_do_not_change_the_identity(self) -> None:
        first = score(self.root / "run.json").identity
        path = self.root / "relabelled.json"
        path.write_text(
            json.dumps(recorded(label="heldout", when="20991231-235959")),
            encoding="utf-8",
        )
        self.assertEqual(first, score(path).identity)


class LedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        self.state = self.root / ".kosika" / "virtual-models.json"

    def score_of(self, payload: object, name: str = "run.json") -> object:
        path = self.root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return score(path)

    def test_an_empty_ledger_reads_as_empty(self) -> None:
        self.assertEqual(read_ledger(self.state), [])

    def test_the_first_record_has_no_previous_entry(self) -> None:
        self.assertIsNone(record_eval(self.state, self.score_of(recorded())))

    def test_recording_twice_returns_the_earlier_entry(self) -> None:
        first = self.score_of(recorded(), "a.json")
        record_eval(self.state, first)
        later = self.score_of(recorded(questions=[{"outcomes": ["right"]}]), "b.json")
        previous = record_eval(self.state, later)
        assert previous is not None
        self.assertEqual(previous["counts"]["wrong shown"], 1)
        changes = compare(previous, later)
        self.assertIn("wrong shown 1 -> 0", changes)
        self.assertEqual(len(read_ledger(self.state)), 2)

    def test_a_broken_ledger_is_refused_rather_than_overwritten(self) -> None:
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.state.write_text('{"schema": "wrong"}', encoding="utf-8")
        with self.assertRaises(VirtualEvalError):
            record_eval(self.state, self.score_of(recorded()))


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        self.state = self.root / "state.json"

    def run_cli(self, payload: object, *extra: str) -> tuple[int, str]:
        path = self.root / "run.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        argv = [
            "kosika",
            "virtual-model",
            "eval",
            str(path),
            "--state",
            str(self.state),
            *extra,
        ]
        stream = StringIO()
        with patch.object(sys, "argv", argv), redirect_stdout(stream):
            code = main()
        return code, stream.getvalue()

    def test_json_output_reports_the_counts_and_the_verdict(self) -> None:
        code, output = self.run_cli(recorded(), "--json", "--gate")
        payload = json.loads(output)
        self.assertEqual(code, 1)
        self.assertFalse(payload["qualified"])
        self.assertEqual(payload["counts"]["wrong shown"], 1)
        self.assertEqual(payload["asked"], 4)
        self.assertTrue(payload["reasons"])

    def test_a_clean_run_passes_the_gate_and_is_recorded(self) -> None:
        clean = recorded(questions=[{"outcomes": ["right"]}, {"outcomes": ["right"]}])
        code, output = self.run_cli(clean, "--json", "--gate", "--record")
        self.assertEqual(code, 0, output)
        payload = json.loads(output)
        self.assertTrue(payload["qualified"])
        self.assertTrue(self.state.exists())

    def test_an_out_of_range_floor_is_a_usage_error(self) -> None:
        code, _ = self.run_cli(recorded(), "--min-right", "1.5")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
