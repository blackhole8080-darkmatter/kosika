from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kosika import cli
from kosika.learning_evaluation import (
    LearningEvaluationError,
    evaluate_learning,
)
from kosika.runtime import replay

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "benchmarks" / "learning" / "assistant.kos"
TASKS = ROOT / "benchmarks" / "learning" / "tasks.json"


class LearningEvaluationTests(unittest.TestCase):
    def test_fixed_tasks_are_correct_cold_and_warm_and_report_independence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result"
            report = evaluate_learning(SOURCE, TASKS, output)

            self.assertEqual(report["schema"], "kosika.learning-evaluation/v1")
            self.assertEqual(report["tasks"], 3)
            self.assertEqual(report["cold_passed"], 3)
            self.assertEqual(report["warm_passed"], 3)
            self.assertEqual(report["cold"]["model_steps"], 3)
            self.assertEqual(report["cold"]["from_lessons"], 0)
            self.assertEqual(report["warm"]["model_steps"], 3)
            self.assertEqual(report["warm"]["from_lessons"], 3)
            self.assertEqual(report["warm"]["independence_ratio"], 1.0)
            self.assertEqual(report["warm"]["how"], {"same": 3})

            saved = json.loads((output / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(saved, report)
            for case in report["results"]:
                self.assertEqual(
                    replay(output / case["cold"]["trace"]), case["cold"]["result"]
                )
                self.assertEqual(
                    replay(output / case["warm"]["trace"]), case["warm"]["result"]
                )

    def test_incorrect_cold_fixture_is_reported_not_hidden_by_independence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tasks = json.loads(TASKS.read_text(encoding="utf-8"))
            tasks["tasks"][0]["responses"]["writer"][0]["text"] = "Wrong answer"
            task_file = root / "tasks.json"
            task_file.write_text(json.dumps(tasks), encoding="utf-8")

            report = evaluate_learning(SOURCE, task_file, root / "output")

            self.assertEqual(report["cold_passed"], 2)
            self.assertEqual(report["warm_passed"], 2)
            self.assertEqual(report["warm"]["independence_ratio"], 1.0)
            self.assertFalse(report["results"][0]["warm"]["passed"])

    def test_invalid_or_duplicate_tasks_fail_before_output_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tasks = json.loads(TASKS.read_text(encoding="utf-8"))
            tasks["tasks"][1]["id"] = tasks["tasks"][0]["id"]
            task_file = root / "tasks.json"
            task_file.write_text(json.dumps(tasks), encoding="utf-8")
            output = root / "output"

            with self.assertRaises(LearningEvaluationError):
                evaluate_learning(SOURCE, task_file, output)
            self.assertFalse(output.exists())

    def test_cli_writes_report_and_prints_a_readable_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "report"
            stdout = io.StringIO()
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "kosika",
                        "learn-eval",
                        str(SOURCE),
                        "--tasks",
                        str(TASKS),
                        "--output",
                        str(output_dir),
                    ],
                ),
                contextlib.redirect_stdout(stdout),
            ):
                result = cli.main()

            self.assertEqual(result, 0)
            rendered = stdout.getvalue()
            self.assertIn("cold correct        : 3/3", rendered)
            self.assertIn("warm correct        : 3/3", rendered)
            self.assertIn("model independence  : 1.0", rendered)
            self.assertTrue((output_dir / "report.json").is_file())

    def test_cli_exits_one_when_cold_and_warm_outputs_are_wrong(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tasks = json.loads(TASKS.read_text(encoding="utf-8"))
            tasks["tasks"][0]["responses"]["writer"][0]["text"] = "Wrong answer"
            task_file = root / "wrong-tasks.json"
            task_file.write_text(json.dumps(tasks), encoding="utf-8")
            stdout = io.StringIO()
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "kosika", "learn-eval", str(SOURCE), "--tasks", str(task_file),
                        "--output", str(root / "report"), "--format", "json",
                    ],
                ),
                contextlib.redirect_stdout(stdout),
            ):
                result = cli.main()

            self.assertEqual(result, 1)
            report = json.loads(stdout.getvalue())
            self.assertEqual(report["cold_passed"], 2)
            self.assertEqual(report["warm_passed"], 2)

    def test_existing_output_directory_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "existing"
            output.mkdir()
            marker = output / "keep.txt"
            marker.write_text("preserve", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                evaluate_learning(SOURCE, TASKS, output)
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")


if __name__ == "__main__":
    unittest.main()
