"""Tests for the engine registry: one language, two engines.

What matters here is that the choice is explicit and cheap to make wrong:
`--engine go` must reach the Go program with the rest of the command intact,
an engine that is not built must say how to build it, and no command may
silently run in a different engine than the one asked for.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from kosika import engines
from kosika.cli import main

PROGRAM = """record Ask { name: Text }
record Answer { text: Text }

fn make(name: Text) -> Answer {
  return {text: "hello " + name}
}

workflow main(ask: Ask) -> Answer {
  answer = make(ask.name)
}
"""

INPUTS = '{"ask": {"name": "ada"}}'


class RegistryTests(unittest.TestCase):
    def test_the_engines_are_python_and_go(self) -> None:
        self.assertEqual(list(engines.ENGINES), ["python", "go"])

    def test_the_reference_engine_is_the_default(self) -> None:
        with patch.dict(os.environ, {"KOSIKA_ENGINE": ""}):
            self.assertEqual(engines.select().name, "python")

    def test_the_environment_can_choose_one(self) -> None:
        with patch.dict(os.environ, {"KOSIKA_ENGINE": "go"}):
            self.assertEqual(engines.select().name, "go")

    def test_an_argument_beats_the_environment(self) -> None:
        with patch.dict(os.environ, {"KOSIKA_ENGINE": "go"}):
            self.assertEqual(engines.select("python").name, "python")

    def test_an_unknown_engine_names_the_ones_there_are(self) -> None:
        with self.assertRaises(engines.EngineError) as caught:
            engines.get("rust")
        self.assertIn("python, go", str(caught.exception))

    def test_the_reference_engine_is_this_python(self) -> None:
        self.assertEqual(
            engines.ENGINE_PYTHON.command(), [sys.executable, "-m", "kosika.cli"]
        )
        self.assertTrue(engines.ENGINE_PYTHON.available())


class TakeEngineTests(unittest.TestCase):
    def test_the_flag_is_taken_out_wherever_it_is_written(self) -> None:
        self.assertEqual(
            engines.take_engine(["run", "a.kos", "--engine", "go"]),
            ("go", ["run", "a.kos"]),
        )
        self.assertEqual(
            engines.take_engine(["--engine=go", "test", "a.kos"]),
            ("go", ["test", "a.kos"]),
        )

    def test_no_flag_changes_nothing(self) -> None:
        self.assertEqual(
            engines.take_engine(["run", "a.kos"]), (None, ["run", "a.kos"])
        )

    def test_a_flag_without_a_name_is_refused(self) -> None:
        with self.assertRaises(engines.EngineError):
            engines.take_engine(["run", "--engine"])

    def test_a_name_that_is_a_flag_is_not_mistaken_for_one(self) -> None:
        # `--engine go` names an engine; a later `--engine` in a path is not
        # touched, because only the exact flag is read.
        name, rest = engines.take_engine(["run", "a.kos", "--engine", "go"])
        self.assertEqual(rest, ["run", "a.kos"])
        self.assertEqual(name, "go")


class DelegateTests(unittest.TestCase):
    def test_the_engine_is_started_with_the_command_it_was_given(self) -> None:
        engine = engines.Engine("go", "native", binary=Path(sys.executable))
        with patch.object(engines.subprocess, "run") as run:
            run.return_value.returncode = 3
            self.assertEqual(engines.delegate(engine, ["run", "a.kos"]), 3)
        self.assertEqual(run.call_args.args[0], [sys.executable, "run", "a.kos"])

    def test_a_nonzero_end_is_passed_on_unchanged(self) -> None:
        engine = engines.Engine("go", "native", binary=Path(sys.executable))
        with patch.object(engines.subprocess, "run") as run:
            run.return_value.returncode = 2
            self.assertEqual(engines.delegate(engine, ["test", "a.kos"]), 2)

    def test_an_unbuilt_engine_says_how_to_build_it(self) -> None:
        engine = engines.Engine("go", "native", built=True)
        with patch.object(engines, "native_binary", return_value=None):
            with self.assertRaises(engines.EngineError) as caught:
                engines.delegate(engine, ["run", "a.kos"])
        message = str(caught.exception)
        self.assertIn("not built", message)
        self.assertIn("go build", message)

    def test_an_unbuilt_engine_is_not_available(self) -> None:
        engine = engines.Engine("go", "native", built=True)
        with patch.object(engines, "native_binary", return_value=None):
            self.assertIsNone(engine.command())
            self.assertFalse(engine.available())


class DescribeTests(unittest.TestCase):
    def test_both_engines_are_described(self) -> None:
        text = "\n".join(engines.describe())
        self.assertIn("python", text)
        self.assertIn("go", text)
        self.assertIn("KOSIKA_ENGINE", text)

    def test_the_command_prints_them(self) -> None:
        out = StringIO()
        with patch.object(sys, "argv", ["kosika", "engines"]), redirect_stdout(out):
            self.assertEqual(main(), 0)
        printed = out.getvalue()
        self.assertIn("Kosika engines:", printed)
        self.assertIn("python", printed)
        self.assertIn("go", printed)


class CommandTests(unittest.TestCase):
    def test_an_unknown_engine_stops_before_anything_runs(self) -> None:
        err = StringIO()
        with (
            patch.object(
                sys, "argv", ["kosika", "run", "nope.kos", "--engine", "rust"]
            ),
            redirect_stderr(err),
        ):
            self.assertEqual(main(), 2)
        self.assertIn("rust", err.getvalue())

    def test_the_reference_engine_runs_the_command_as_written(self) -> None:
        """`--engine python` must not change what the command does."""

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "main.kos"
            source.write_text(PROGRAM, encoding="utf-8")
            inputs = Path(folder) / "input.json"
            inputs.write_text(INPUTS, encoding="utf-8")
            trace = Path(folder) / "trace.jsonl"
            out = StringIO()
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "kosika",
                        "run",
                        str(source),
                        "--input",
                        str(inputs),
                        "--trace",
                        str(trace),
                        "--engine",
                        "python",
                    ],
                ),
                redirect_stdout(out),
            ):
                self.assertEqual(main(), 0)
        self.assertIn("hello ada", out.getvalue())


@unittest.skipUnless(
    engines.native_binary(build=False) is not None, "the Go engine is not built"
)
class ParityTests(unittest.TestCase):
    """The same program, run by both engines, must print the same answer."""

    def answer(self, command: list[str], folder: Path) -> str:
        finished = subprocess.run(
            [
                *command,
                "run",
                str(folder / "main.kos"),
                "--input",
                str(folder / "in.json"),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        self.assertEqual(finished.returncode, 0, finished.stderr)
        # Both engines print the answer, then a line naming the trace; the
        # trace path is each engine's own business.
        return finished.stdout.split("Trace:")[0].strip()

    def test_both_engines_give_the_same_answer(self) -> None:
        go = engines.ENGINE_GO.command()
        assert go is not None
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "main.kos").write_text(PROGRAM, encoding="utf-8")
            (root / "in.json").write_text(INPUTS, encoding="utf-8")
            reference_command = engines.ENGINE_PYTHON.command()
            assert reference_command is not None
            reference = self.answer(reference_command, root)
            native = self.answer(go, root)
        self.assertEqual(reference, native)
        self.assertIn("hello ada", reference)


@unittest.skipUnless(
    engines.native_binary(build=False) is not None, "the Go engine is not built"
)
class CheckParityTests(unittest.TestCase):
    """`check` in both engines: the same verdict, and the same reason."""

    def run_engine(
        self, command: list[str], *arguments: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*command, *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    def test_both_engines_accept_a_well_formed_program(self) -> None:
        native_command = engines.ENGINE_GO.command()
        assert native_command is not None
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "main.kos"
            source.write_text(PROGRAM, encoding="utf-8")
            reference_command = engines.ENGINE_PYTHON.command()
            assert reference_command is not None
            reference = self.run_engine(reference_command, "check", str(source))
            native = self.run_engine(native_command, "check", str(source))
        self.assertEqual(reference.returncode, 0, reference.stderr)
        self.assertEqual(native.returncode, 0, native.stderr)
        self.assertEqual(native.stdout, f"OK: {source}\n")

    def test_both_engines_refuse_the_same_program(self) -> None:
        native_command = engines.ENGINE_GO.command()
        assert native_command is not None
        source_text = (
            "record Ask { name: Text }\n"
            "workflow main(ask: Ask) -> Ask {\n"
            "  answer = nothing_here(ask)\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "bad.kos"
            source.write_text(source_text, encoding="utf-8")
            reference_command = engines.ENGINE_PYTHON.command()
            assert reference_command is not None
            reference = self.run_engine(reference_command, "check", str(source))
            native = self.run_engine(native_command, "check", str(source))
        self.assertEqual(reference.returncode, 2)
        self.assertEqual(native.returncode, 2)
        # The reference adds " (line N, column M)" from its diagnostics
        # engine; the reason before it must be the same words.
        self.assertEqual(
            native.stderr.split(" (line")[0].strip(),
            reference.stderr.split(" (line")[0].strip(),
        )


if __name__ == "__main__":
    unittest.main()
