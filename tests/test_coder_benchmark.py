"""The coder benchmark counts what it bought, and what each attempt was told."""

from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).parents[1]


def load() -> ModuleType:
    path = ROOT / "benchmarks" / "coder" / "run.py"
    spec = importlib.util.spec_from_file_location("coder_benchmark", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def attempt(
    number: int,
    passed: bool,
    seconds: float = 30.0,
    abandoned: bool = False,
    excerpts: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": "task",
        "attempt": number,
        "asked": "",
        "passed": passed,
        "state": "done" if passed else "failed",
        "abandoned": abandoned,
        "seconds": seconds,
        "approvals": 1,
        "failures": [] if passed else ["python -m pytest: 1 failed"],
        "excerpts": [] if passed else (excerpts if excerpts is not None else ["assert 1 == 2"]),
        "error": "",
        "said": "",
        "files": [],
    }


class LadderTests(unittest.TestCase):
    def test_the_ladder_is_one_four_sixteen_clipped_to_n(self) -> None:
        module = load()
        self.assertEqual(module.ladder(1), [1])
        self.assertEqual(module.ladder(3), [1, 3])
        self.assertEqual(module.ladder(4), [1, 4])
        self.assertEqual(module.ladder(16), [1, 4, 16])


class SummaryTests(unittest.TestCase):
    def test_each_step_reports_the_attempts_it_paid_for(self) -> None:
        module = load()
        attempts = [
            attempt(1, False, 100.0),
            attempt(2, False, 100.0),
            attempt(3, True, 90.0),
        ]
        summary = module.summarise(attempts, 16)
        self.assertEqual(
            summary["1"], {"passed": False, "attempts": 1, "seconds": 100.0}
        )
        # The run stopped at the pass, so sixteen attempts cost what three did.
        self.assertEqual(
            summary["4"], {"passed": True, "attempts": 3, "seconds": 290.0}
        )
        self.assertEqual(summary["16"], summary["4"])

    def test_a_task_that_never_passes_pays_for_every_attempt(self) -> None:
        module = load()
        summary = module.summarise(
            [attempt(number, False, 10.0) for number in range(1, 17)], 16
        )
        self.assertEqual(
            summary["16"], {"passed": False, "attempts": 16, "seconds": 160.0}
        )
        self.assertEqual(summary["4"]["attempts"], 4)


class TaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.scratch = Path(self.directory.name)
        self.folder = self.scratch / "py"
        self.folder.mkdir()
        self.prepared = 0

    def prepare(self) -> Any:
        def made(args: Any, task: dict[str, Any]) -> tuple[Path, Path, Any]:
            self.prepared += 1
            return self.scratch, self.folder, object()

        return made

    def run_task(
        self, fake: Any, best_of: int = 4, repair: bool = False
    ) -> dict[str, Any]:
        module = self.module
        args = argparse.Namespace(best_of=best_of, repair=repair, keep=False)
        with patch.object(module, "prepare", self.prepare()), patch.object(
            module, "drop", lambda scratch, keep: None
        ), patch.object(module, "run_attempt", fake):
            return module.run_task(args, {"id": "task", "task": "write it"})

    def test_a_task_stops_at_its_first_pass_and_adds_up_the_clock(self) -> None:
        made: list[int] = []

        def fake(
            args: Any, task: dict[str, Any], number: int, folder: Path, app: Any, asked: str
        ) -> dict[str, Any]:
            made.append(number)
            return attempt(number, number == 2)

        result = self.run_task(fake)
        self.assertEqual(made, [1, 2])
        self.assertTrue(result["passed"])
        self.assertEqual(result["seconds"], 60.0)
        self.assertEqual(len(result["attempts"]), 2)
        self.assertEqual(result["summary"]["4"]["attempts"], 2)

    def test_best_of_starts_every_attempt_in_a_fresh_copy(self) -> None:
        def fake(
            args: Any, task: dict[str, Any], number: int, folder: Path, app: Any, asked: str
        ) -> dict[str, Any]:
            return attempt(number, False, 5.0)

        self.run_task(fake, best_of=3)
        self.assertEqual(self.prepared, 3)

    def test_a_task_that_never_passes_says_what_the_last_attempt_said(self) -> None:
        def fake(
            args: Any, task: dict[str, Any], number: int, folder: Path, app: Any, asked: str
        ) -> dict[str, Any]:
            return attempt(number, False, 5.0)

        result = self.run_task(fake)
        self.assertFalse(result["passed"])
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["failures"], ["python -m pytest: 1 failed"])
        self.assertEqual(len(result["attempts"]), 4)
        self.assertEqual(result["seconds"], 20.0)

    def test_a_run_still_working_ends_the_task_instead_of_being_raced(self) -> None:
        made: list[int] = []

        def fake(
            args: Any, task: dict[str, Any], number: int, folder: Path, app: Any, asked: str
        ) -> dict[str, Any]:
            made.append(number)
            return attempt(number, False, 75.0, abandoned=True)

        result = self.run_task(fake)
        self.assertEqual(made, [1])
        self.assertFalse(result["passed"])

    def test_repair_keeps_the_folder_and_says_what_failed(self) -> None:
        heard: list[str] = []

        def fake(
            args: Any, task: dict[str, Any], number: int, folder: Path, app: Any, asked: str
        ) -> dict[str, Any]:
            heard.append(asked)
            if number == 1:
                return attempt(1, False, 20.0, excerpts=["calc.py said:\n  E AssertionError"])
            return attempt(2, True, 20.0)

        result = self.run_task(fake, best_of=2, repair=True)
        self.assertTrue(result["passed"])
        # One folder for the whole task: the second attempt fixes the first.
        self.assertEqual(self.prepared, 1)
        self.assertEqual(heard[0], "write it")
        self.assertIn("attempt 2", heard[1])
        self.assertIn("E AssertionError", heard[1])
        self.assertIn("keep whatever already works", heard[1])

    def test_repair_without_anything_to_say_repeats_the_task(self) -> None:
        heard: list[str] = []

        def fake(
            args: Any, task: dict[str, Any], number: int, folder: Path, app: Any, asked: str
        ) -> dict[str, Any]:
            heard.append(asked)
            return attempt(number, False, 10.0, excerpts=[])

        self.run_task(fake, best_of=2, repair=True)
        self.assertEqual(heard, ["write it", "write it"])


class CheckedTests(unittest.TestCase):
    def test_a_repair_is_not_told_of_a_file_it_may_not_open(self) -> None:
        module = load()
        with tempfile.TemporaryDirectory() as directory:
            failed, why = module.checked(
                Path(directory),
                [["python", "-c", "import sys; sys.path.insert(0, r'{repo}')\n"
                  "from kosika.checker import CheckError\n"
                  "raise CheckError('Step next_count cannot access count')"]],
            )
        text = "\n".join(why)
        self.assertTrue(failed)
        self.assertIn("Step next_count cannot access count", text)
        # The traceback names this benchmark's own checker, which is outside
        # the task's folder. A repair attempt shown that path stopped at once
        # asking for it, before it had changed a line.
        self.assertNotIn(str(module.REPO), text)

    def test_a_repair_is_told_of_a_file_in_its_folder_by_its_short_name(self) -> None:
        module = load()
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "broken.kos").write_text(
                'record Answer { text: Text }\n\nworkflow main() -> Answer {\n  return {text: "x"}\n',
                encoding="utf-8",
            )
            failed, why = module.checked(
                Path(directory),
                [["python", "-c", "import sys; sys.path.insert(0, r'{repo}')\n"
                  "from kosika.modules import load_program\n"
                  "load_program('broken.kos')"]],
            )
        text = "\n".join(why)
        self.assertTrue(failed)
        # The parser names the file it read in full, and the coder's tools
        # take only a path relative to its folder: a repair attempt shown the
        # absolute one stopped at once, before changing a line.
        self.assertIn("broken.kos", text)
        self.assertNotIn(str(Path(directory).resolve()), text)

    def test_a_hung_check_fails_that_check_and_the_notes_still_run(self) -> None:
        module = load()
        ran: list[list[str]] = []

        def hang(command: list[str], **kwargs: Any) -> Any:
            ran.append(command)
            raise module.subprocess.TimeoutExpired(command, 120)

        with tempfile.TemporaryDirectory() as directory, patch.object(
            module.subprocess, "run", hang
        ):
            failed, why = module.checked(
                Path(directory),
                [
                    ["python", "-m", "pytest", "-q"],
                    ["python", "-m", "pytest", "-q", "test_two.py"],
                ],
            )
        # One hung check does not stop the rest, and it is a failure.
        self.assertEqual(len(ran), 2)
        self.assertEqual(len(failed), 2)
        self.assertIn("did not finish in 120 s", failed[0])
        self.assertIn("did not finish in 120 s", why[1])


class LanguageTests(unittest.TestCase):
    def test_a_run_says_which_language_each_task_wrote(self) -> None:
        module = load()
        tally = module.by_language(
            [
                {"language": "python", "passed": False},
                {"language": "kosika", "passed": True},
                {"language": "kosika", "passed": False},
            ]
        )
        self.assertEqual(tally["kosika"], {"passed": 1, "tasks": 2})
        self.assertEqual(tally["python"], {"passed": 0, "tasks": 1})

    def test_every_task_names_its_language_and_the_ordinary_set_has_kosika(self) -> None:
        for name in ("tasks.json", "hard.json"):
            tasks = json.loads((ROOT / "benchmarks" / "coder" / name).read_text(encoding="utf-8"))
            for task in tasks:
                self.assertTrue(task.get("language"), f"{name}: {task['id']} names no language")
        ordinary = json.loads((ROOT / "benchmarks" / "coder" / "tasks.json").read_text(encoding="utf-8"))
        kosika = [task for task in ordinary if task["language"] == "kosika"]
        # Without these, a run cannot say how often the agent writes Kosika.
        self.assertEqual(len(kosika), 2)
        for task in kosika:
            self.assertTrue(any("load_program(" in part for check in task["checks"] for part in check))

    def test_a_kosika_check_is_told_where_the_benchmark_lives(self) -> None:
        module = load()
        seen: list[list[str]] = []

        def ran(command: list[str], **kwargs: Any) -> Any:
            seen.append(command)
            return type("Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with tempfile.TemporaryDirectory() as directory, patch.object(module.subprocess, "run", ran):
            module.checked(Path(directory), [["python", "-c", "sys.path.insert(0, r'{repo}')"]])
        self.assertIn(str(module.REPO), seen[0][-1])
        self.assertNotIn("{repo}", seen[0][-1])


class CompareTests(unittest.TestCase):
    def test_a_saved_run_is_read_into_the_shape_a_comparison_needs(self) -> None:
        module = load()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.json"
            path.write_text(
                json.dumps(
                    {
                        "model": "gemma3:1b",
                        "mode": "repair",
                        "best_of": 4,
                        "summary": {"1": {"passed": 0, "tasks": 2, "seconds": 40.0}},
                        "results": [],
                    }
                ),
                encoding="utf-8",
            )
            report = module.describe_report(path)
        self.assertEqual(
            (report["model"], report["mode"], report["best_of"]),
            ("gemma3:1b", "repair", 4),
        )
        self.assertEqual(report["summary"]["1"]["tasks"], 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
