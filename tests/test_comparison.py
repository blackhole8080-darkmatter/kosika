"""The Python comparison is honest: same answers, measured sizes, real refusals."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kosika import cli
from kosika.comparison import (
    COMPARISON_SCHEMA,
    VARIANTS,
    ComparisonError,
    compare,
    measure,
    to_text,
)

ROOT = Path(__file__).parents[1]
TASKS = ROOT / "benchmarks" / "compare"


class ComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = compare(TASKS)

    def test_every_task_has_three_equivalent_solutions(self) -> None:
        # compare() raises unless all three return the expected result.
        self.assertEqual(self.report["schema"], COMPARISON_SCHEMA)
        self.assertEqual(len(self.report["tasks"]), 6)
        self.assertEqual(sum(task["uses_model"] for task in self.report["tasks"]), 2)

    def test_kosika_and_checked_python_refuse_every_bad_case(self) -> None:
        total = self.report["bad_case_count"]
        self.assertEqual(total, 20)
        self.assertEqual(self.report["totals"]["kosika"]["caught"], total)
        self.assertEqual(self.report["totals"]["checked"]["caught"], total)

    def test_plain_python_accepts_some_bad_cases(self) -> None:
        caught = self.report["totals"]["plain"]["caught"]
        self.assertLess(caught, self.report["bad_case_count"])
        accepted = [
            case["label"]
            for task in self.report["tasks"]
            for case in task["bad_cases"]
            if case["plain"] == "accepted"
        ]
        self.assertIn("the model invents a label that was not offered", accepted)

    def test_the_recorded_numbers_match_the_documents(self) -> None:
        totals = self.report["totals"]
        self.assertEqual(
            [totals[variant]["lines"] for variant in VARIANTS], [84, 81, 108]
        )
        self.assertEqual(totals["plain"]["caught"], 9)
        self.assertEqual(self.report["python_helpers"]["lines"], 35)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("| Lines of code, all six tasks | 84 | 81 | 108", readme)

    def test_kosika_is_not_claimed_shorter_where_it_is_not(self) -> None:
        data = [task for task in self.report["tasks"] if not task["uses_model"]]
        model = [task for task in self.report["tasks"] if task["uses_model"]]

        def lines(tasks: list, variant: str) -> int:
            return sum(task["sizes"][variant]["lines"] for task in tasks)

        self.assertGreater(lines(data, "kosika"), lines(data, "plain"))
        self.assertLess(lines(model, "kosika"), lines(model, "plain"))

    def test_measure_ignores_blank_lines_and_comments(self) -> None:
        self.assertEqual(
            measure("// note\n\nlet x = 1\n  // more\nreturn x\n", "//"),
            {"lines": 2, "tokens": 6},
        )
        self.assertEqual(measure('# c\nx = "a b"  # tail\n', "#")["lines"], 1)

    def test_text_report_names_what_plain_python_missed(self) -> None:
        text = to_text(self.report)
        self.assertIn("total", text)
        self.assertIn("Bad cases plain Python accepted without complaint:", text)
        self.assertIn("shared helpers: 35 lines", text)

    def test_a_wrong_reference_solution_fails_the_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory) / "compare"
            shutil.copytree(TASKS, copy, ignore=shutil.ignore_patterns("__pycache__"))
            plain = copy / "01_order_overview" / "plain.py"
            plain.write_text(
                plain.read_text(encoding="utf-8").replace('"Hello, "', '"Hi, "'),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ComparisonError, "the plain solution returned"):
                compare(copy)
            with self.assertRaises(ComparisonError):
                compare(Path(directory) / "empty")


class CliTests(unittest.TestCase):
    def invoke(self, *arguments: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["kosika", "compare-python", *arguments]):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main()
        return code, out.getvalue(), err.getvalue()

    def test_text_json_and_a_missing_folder(self) -> None:
        code, out, _ = self.invoke("--tasks", str(TASKS))
        self.assertEqual(code, 0)
        self.assertIn("06_invoice_extract", out)
        code, out, _ = self.invoke("--tasks", str(TASKS), "--format", "json")
        self.assertEqual(json.loads(out)["bad_case_count"], 20)
        code, _, err = self.invoke("--tasks", str(TASKS / "missing"))
        self.assertEqual(code, 2)
        self.assertIn("failed safely", err)


if __name__ == "__main__":
    unittest.main()
