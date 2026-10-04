"""The write-from-spec benchmark judges programs with Kosika, not a model."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from kosika import cli
from kosika.model import ModelRequest, ModelResponse, ModelUsage, ScriptedModelProvider
from kosika.modules import load_program
from kosika.spec_benchmark import (
    SPEC_REPORT_SCHEMA,
    SpecBenchmarkError,
    SpecTask,
    build_messages,
    command_solver,
    extract_program,
    json_equal,
    judge,
    load_tasks,
    model_solver,
    reference_solver,
    run_benchmark,
    run_task,
    structure_failures,
    to_text,
)

ROOT = Path(__file__).parents[1]
TASKS = ROOT / "benchmarks" / "spec" / "tasks.json"
REFERENCES = ROOT / "benchmarks" / "spec" / "reference"
GUIDE = ROOT / "AGENT_GUIDE.md"


def task_named(task_id: str) -> SpecTask:
    return next(task for task in load_tasks(TASKS) if task.id == task_id)


def reference(task_id: str) -> str:
    return (REFERENCES / f"{task_id}.kos").read_text(encoding="utf-8")


class ReferenceTests(unittest.TestCase):
    def test_every_task_has_a_reference_that_passes_first_try(self) -> None:
        tasks = load_tasks(TASKS)
        self.assertEqual(len(tasks), 20)
        payload = run_benchmark(tasks, reference_solver(REFERENCES))
        self.assertEqual(payload["schema"], SPEC_REPORT_SCHEMA)
        self.assertEqual(payload["passed_first_try"], 20, to_text(payload))
        self.assertEqual(payload["failure_codes"], {})
        self.assertEqual(sorted(payload["levels"]), ["agent", "basic", "model", "tool"])

    def test_guide_examples_that_are_whole_programs_check(self) -> None:
        guide = GUIDE.read_text(encoding="utf-8")
        first = guide.split("```kosika\n", 2)[1].split("```", 1)[0]
        self.assertTrue(judge(task_named("01_greeting"), first).passed)


class JudgeTests(unittest.TestCase):
    def test_a_check_failure_reports_codes_and_text(self) -> None:
        verdict = judge(task_named("01_greeting"), "record Person { name: Text ")
        self.assertFalse(verdict.checked)
        self.assertEqual(verdict.stage, "check")
        self.assertTrue(verdict.codes)
        self.assertIn(verdict.codes[0], verdict.feedback)

    def test_a_wrong_result_shows_the_inputs_and_both_values(self) -> None:
        source = reference("01_greeting").replace('"Hello, "', '"Hi, "')
        verdict = judge(task_named("01_greeting"), source)
        self.assertTrue(verdict.checked)
        self.assertEqual(verdict.stage, "result")
        self.assertIn('"Hi, Ada!"', verdict.feedback)
        self.assertIn('"Hello, Ada!"', verdict.feedback)

    def test_a_run_failure_is_a_miss_not_a_crash(self) -> None:
        source = reference("11_ask_model").replace("assistant", "helper")
        verdict = judge(task_named("11_ask_model"), source)
        self.assertEqual(verdict.stage, "run")
        self.assertEqual(verdict.codes, ("run/PermissionError",))

    def test_structure_is_enforced_on_the_checked_program(self) -> None:
        task = task_named("17_least_privilege")
        verdict = judge(task, reference(task.id).replace("delete_log, shell", "shell"))
        self.assertEqual(verdict.stage, "structure")
        self.assertIn("delete_log", verdict.feedback)
        settings = task_named("14_model_settings")
        verdict = judge(settings, reference(settings.id).replace("0.2", "0.9"))
        self.assertIn("parameters.temperature", verdict.feedback)

    def test_a_check_only_task_is_never_run(self) -> None:
        task = task_named("18_approval_gate")
        self.assertTrue(judge(task, reference(task.id)).passed)
        unapproved = reference(task.id).replace("  require approval\n", "")
        self.assertFalse(judge(task, unapproved).passed)

    def test_empty_and_oversized_programs_are_rejected(self) -> None:
        task = task_named("01_greeting")
        self.assertEqual(judge(task, "  ").codes, ("spec/empty",))
        self.assertEqual(judge(task, "// x\n" * 20000).codes, ("spec/too-large",))

    def test_missing_declarations_and_source_patterns_are_named(self) -> None:
        program = load_program(REFERENCES / "01_greeting.kos")
        failures = structure_failures(
            program,
            "",
            [
                {"in": "models", "name": "absent"},
                {"source_matches": "call\\s+x"},
                {"in": "workflows", "name": "main", "has": {"nope.deep": 1}},
            ],
        )
        self.assertEqual(len(failures), 3)
        with self.assertRaises(SpecBenchmarkError):
            structure_failures(program, "", [{"in": "nothing", "name": "x"}])

    def test_json_equality_keeps_booleans_apart_from_numbers(self) -> None:
        self.assertTrue(json_equal({"a": [1, 2.0]}, {"a": [1.0, 2]}))
        self.assertFalse(json_equal(True, 1))
        self.assertFalse(json_equal([1], [1, 2]))
        self.assertFalse(json_equal({"a": 1}, {"b": 1}))
        self.assertFalse(json_equal("1", 1))


class SolverTests(unittest.TestCase):
    def test_the_last_fenced_block_is_the_program(self) -> None:
        reply = "Draft:\n```kosika\nold\n```\nFixed:\n```\nnew\n```\nDone."
        self.assertEqual(extract_program(reply), "new\n")
        self.assertEqual(extract_program("  bare  "), "bare\n")
        self.assertEqual(extract_program(""), "\n")

    def test_a_rejection_is_shown_to_the_model_on_the_next_attempt(self) -> None:
        task = task_named("01_greeting")
        seen: list[ModelRequest] = []
        good = reference(task.id)

        class Recorder:
            def __init__(self) -> None:
                self.replies = [
                    "```kosika\nrecord Broken {\n```",
                    f"```kosika\n{good}```",
                ]

            def complete(self, request: ModelRequest) -> ModelResponse:
                seen.append(request)
                return ModelResponse(
                    content=self.replies.pop(0),
                    usage=ModelUsage(prompt_tokens=10, completion_tokens=5),
                )

        result = run_task(task, model_solver(Recorder(), "GUIDE TEXT", model="m"))
        self.assertTrue(result.passed)
        self.assertFalse(result.first_try)
        self.assertEqual(len(result.attempts), 2)
        self.assertEqual(result.attempts[0].prompt_tokens, 10)
        self.assertIn("GUIDE TEXT", seen[0].messages[0].content)
        self.assertEqual(len(seen[0].messages), 2)
        self.assertEqual(len(seen[1].messages), 4)
        self.assertIn("was rejected", seen[1].messages[3].content)
        self.assertEqual(seen[1].temperature, 0.0)

    def test_attempts_are_bounded_and_failures_are_counted(self) -> None:
        task = task_named("01_greeting")
        provider = ScriptedModelProvider(
            [ModelResponse(content="not kosika") for _ in range(2)]
        )
        payload = run_benchmark([task], model_solver(provider, "guide"), max_attempts=2)
        self.assertEqual(payload["passed"], 0)
        self.assertEqual(payload["usage"]["attempts"], 2)
        self.assertEqual(payload["failure_stages"], {"check": 2})
        self.assertIn("not passed: 01_greeting", to_text(payload))
        with self.assertRaises(SpecBenchmarkError):
            run_task(task, model_solver(provider, "guide"), max_attempts=0)

    def test_a_provider_failure_ends_the_task_without_ending_the_run(self) -> None:
        def broken(_task: SpecTask, _history: Any) -> tuple[str, dict[str, int]]:
            raise RuntimeError("endpoint down")

        payload = run_benchmark(load_tasks(TASKS)[:2], broken, max_attempts=3)
        self.assertEqual(payload["usage"]["attempts"], 2)
        self.assertEqual(payload["failure_codes"], {"solver/RuntimeError": 2})

    def test_messages_include_every_earlier_rejection(self) -> None:
        task = task_named("01_greeting")
        verdict = judge(task, "nope")
        messages = build_messages("guide", task, [("nope", verdict)] * 2)
        self.assertEqual(
            [item.role for item in messages][2:], ["assistant", "user"] * 2
        )


class CommandSolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.script = Path(self.directory.name) / "author.py"

    def command(self, body: str) -> list[str]:
        self.script.write_text(body, encoding="utf-8")
        return [sys.executable, str(self.script)]

    def test_a_command_reads_the_prompt_and_prints_the_program(self) -> None:
        task = task_named("01_greeting")
        answer = Path(self.directory.name) / "answer.kos"
        answer.write_text(reference(task.id), encoding="utf-8")
        command = self.command(
            "import pathlib, sys\n"
            "prompt = sys.stdin.read()\n"
            "assert 'GUIDE TEXT' in prompt and 'Spec:' in prompt\n"
            "assert not list(pathlib.Path.cwd().iterdir())\n"
            f"print(pathlib.Path({str(answer)!r}).read_text(encoding='utf-8'))\n"
        )
        result = run_task(task, command_solver(command, "GUIDE TEXT"))
        self.assertTrue(result.first_try, result.attempts[0].verdict.feedback)
        self.assertEqual(result.attempts[0].source, reference(task.id))

    def test_a_failing_command_is_a_solver_failure(self) -> None:
        command = self.command("import sys\nsys.exit('boom')\n")
        result = run_task(task_named("01_greeting"), command_solver(command, "g"))
        self.assertEqual(result.attempts[0].verdict.stage, "solver")
        self.assertIn("boom", result.attempts[0].verdict.feedback)
        self.assertEqual(len(result.attempts), 1)

    def test_missing_commands_are_refused(self) -> None:
        with self.assertRaises(SpecBenchmarkError):
            command_solver([], "g")
        with self.assertRaises(SpecBenchmarkError):
            command_solver(["kosika-no-such-program-xyz"], "g")


class TaskFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "tasks.json"

    def write(self, payload: Any) -> Path:
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        return self.path

    def test_invalid_task_files_are_refused(self) -> None:
        task = {"id": "a", "level": "basic", "spec": "s", "expected": 1}
        cases = [
            {"schema": "other", "tasks": [task]},
            {"schema": "kosika.spec-tasks/v1", "tasks": []},
            {"schema": "kosika.spec-tasks/v1", "tasks": [{**task, "extra": 1}]},
            {"schema": "kosika.spec-tasks/v1", "tasks": [{**task, "spec": ""}]},
            {"schema": "kosika.spec-tasks/v1", "tasks": [task, task]},
            {
                "schema": "kosika.spec-tasks/v1",
                "tasks": [{"id": "a", "level": "basic", "spec": "s"}],
            },
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(SpecBenchmarkError):
                    load_tasks(self.write(payload))
        with self.assertRaises(SpecBenchmarkError):
            load_tasks(Path(self.directory.name) / "missing.json")


class CliTests(unittest.TestCase):
    def invoke(self, *arguments: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        argv = ["kosika", "spec-eval", "--tasks", str(TASKS), *arguments]
        with patch.object(sys, "argv", argv):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main()
        return code, out.getvalue(), err.getvalue()

    def test_the_reference_solver_passes_and_writes_a_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            code, out, err = self.invoke(
                "--reference-dir",
                str(REFERENCES),
                "--limit",
                "3",
                "--output",
                str(output),
            )
            self.assertEqual(code, 0)
            self.assertIn("passed first try     : 3", out)
            self.assertIn("01_greeting: first try", err)
            self.assertEqual(json.loads(output.read_text("utf-8"))["tasks"], 3)

    def test_one_task_as_json(self) -> None:
        code, out, _ = self.invoke(
            "--reference-dir", str(REFERENCES), "--task", "20_swarm", "--format", "json"
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["results"][0]["id"], "20_swarm")

    def test_bad_setups_fail_safely(self) -> None:
        for arguments in (
            ("--task", "nope"),
            ("--limit", "0"),
            ("--solver", "model"),
            ("--solver", "command"),
            ("--reference-dir", "does-not-exist", "--attempts", "9"),
        ):
            with self.subTest(arguments=arguments):
                code, _, err = self.invoke(*arguments)
                self.assertEqual(code, 2)
                self.assertIn("failed safely", err)

    def test_a_model_solver_that_fails_exits_one(self) -> None:
        provider = ScriptedModelProvider([ModelResponse(content="nope")])
        with patch.object(cli, "OpenAICompatibleProvider", lambda *_, **__: provider):
            code, out, err = self.invoke(
                "--solver",
                "model",
                "--base-url",
                "http://x",
                "--model",
                "m",
                "--guide",
                str(GUIDE),
                "--limit",
                "1",
                "--attempts",
                "1",
            )
        self.assertEqual(code, 1)
        self.assertIn("01_greeting: failed (check)", err)
        self.assertIn("passed in 1 attempt(s): 0", out)


if __name__ == "__main__":
    unittest.main()
