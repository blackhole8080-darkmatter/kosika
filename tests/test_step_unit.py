"""A `step` remembers what it made, across runs and across engines.

Within one run, conformance/cases/step_unit.kos holds both engines to the
same reuse. This holds the memory folder to it: a run with `--memory FOLDER`
keeps each step's result in FOLDER/steps, a later run of the same program
gives it again without asking the model, and either engine reads what the
other kept. A reused call is marked in the trace and still replays.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kosika.modules import load_program
from kosika.remembered import Remembered, step_key
from kosika.runtime import replay

ROOT = Path(__file__).resolve().parent.parent

BOOK = """\
record Chapter { title: Text body: Text }
record Notes { title: Text gist: Text }
record Reading { text: Text }

model reader {
  input: Chapter
  output: Notes
}

step read_chapter(chapter: Chapter) -> Notes {
  notes = use_model reader(title: chapter.title, body: chapter.body)
}

fn show(n: Notes) -> Reading { return {text: n.title + ": " + n.gist} }

workflow main(chapter: Chapter) -> Reading {
  first  = call read_chapter(chapter)
  second = call read_chapter(chapter)
  out    = show(second)
}
"""


def trace_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]


def step_calls(events: list[dict]) -> list[dict]:
    return [event for event in events if event.get("event") == "workflow_call"]


class StepUnitTests(unittest.TestCase):
    engine: Path | None = None

    @classmethod
    def setUpClass(cls) -> None:
        cls._dir = tempfile.TemporaryDirectory()
        if shutil.which("go"):
            cls.engine = Path(cls._dir.name) / "kosika-native.exe"
            built = subprocess.run(
                ["go", "build", "-o", str(cls.engine), "."],
                cwd=ROOT / "native",
                capture_output=True,
                text=True,
            )
            if built.returncode != 0:
                raise AssertionError(
                    "the native engine does not build:\n" + built.stderr
                )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._dir.cleanup()

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.program = self.root / "book.kos"
        self.program.write_text(BOOK, encoding="utf-8")
        (self.root / "input.json").write_text(
            json.dumps({"chapter": {"title": "One", "body": "Once upon a time"}}),
            encoding="utf-8",
        )
        self.memory = self.root / "memory"

    def answers(self, gist: str) -> Path:
        path = self.root / f"answers-{gist}.json"
        path.write_text(
            json.dumps({"reader": [{"title": "One", "gist": gist}]}), encoding="utf-8"
        )
        return path

    def run_with(self, engine: str, gist: str, trace: Path, *more: str) -> str:
        source, given = str(self.program), str(self.root / "input.json")
        if engine == "python":
            command = [
                sys.executable,
                "-m",
                "kosika.cli",
                "run",
                source,
                "--input",
                given,
            ]
        else:
            assert self.engine is not None
            command = [
                str(self.engine),
                "run",
                source,
                "--workflow",
                "main",
                "--input",
                given,
            ]
        command += [
            "--model-fixture",
            str(self.answers(gist)),
            "--trace",
            str(trace),
            *more,
        ]
        ran = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        return ran.stdout

    def engines(self) -> list[str]:
        return ["python", "native"] if self.engine is not None else ["python"]

    def test_a_run_reuses_its_own_step_and_says_so(self) -> None:
        trace = self.root / "one.jsonl"
        self.run_with("python", "begins", trace)
        first, second = step_calls(trace_events(trace))
        self.assertNotIn("reused", first)
        self.assertTrue(second["reused"])
        self.assertEqual(replay(trace), {"text": "One: begins"})

    def test_a_later_run_gives_what_a_step_made_without_its_model(self) -> None:
        for maker in self.engines():
            for user in self.engines():
                with self.subTest(made_by=maker, used_by=user):
                    shutil.rmtree(self.memory, ignore_errors=True)
                    keep = ["--memory", str(self.memory)]
                    self.run_with(maker, "begins", self.root / "made.jsonl", *keep)
                    (kept,) = (self.memory / "steps").glob("*.json")
                    self.assertEqual(
                        json.loads(kept.read_text("utf-8"))["step"], "read_chapter"
                    )
                    # The model would now answer otherwise; the step is not
                    # made again, so it is not asked.
                    again = self.root / "again.jsonl"
                    out = self.run_with(user, "ends", again, *keep)
                    self.assertIn("One: begins", out)
                    self.assertTrue(
                        all(c.get("reused") for c in step_calls(trace_events(again)))
                    )
                    self.assertEqual(replay(again), {"text": "One: begins"})

    def test_without_a_memory_folder_nothing_outlives_the_run(self) -> None:
        self.run_with("python", "begins", self.root / "a.jsonl")
        out = self.run_with("python", "ends", self.root / "b.jsonl")
        self.assertIn("One: ends", out)

    def test_a_changed_program_makes_its_steps_again(self) -> None:
        keep = ["--memory", str(self.memory)]
        self.run_with("python", "begins", self.root / "a.jsonl", *keep)
        self.program.write_text(BOOK + "\n// edited\n", encoding="utf-8")
        out = self.run_with("python", "ends", self.root / "b.jsonl", *keep)
        self.assertIn("One: ends", out)

    def test_a_kept_result_that_no_longer_fits_is_made_again(self) -> None:
        keep = ["--memory", str(self.memory)]
        self.run_with("python", "begins", self.root / "a.jsonl", *keep)
        (kept,) = (self.memory / "steps").glob("*.json")
        kept.write_text(json.dumps({"result": {"wrong": 1}, "events": []}), "utf-8")
        out = self.run_with("python", "ends", self.root / "b.jsonl", *keep)
        self.assertIn("One: ends", out)

    def test_both_engines_name_a_kept_result_alike(self) -> None:
        program = load_program(self.program)
        from kosika.identity import program_identity

        key = step_key(
            program_identity(program), "read_chapter", {"chapter": {"title": "One"}}
        )
        store = Remembered(self.memory)
        store.keep(key, "read_chapter", {"chapter": {"title": "One"}}, {"x": 1}, [])
        self.assertEqual(Remembered(self.memory).recall(key), ({"x": 1}, []))
        self.assertEqual((self.memory / "steps" / f"{key}.json").exists(), True)


if __name__ == "__main__":
    unittest.main()
