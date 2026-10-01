import contextlib
import io
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from kosika.agent import ApprovalRecord
from kosika.cli import main
from kosika.controls import ExecutionError
from kosika.durable import ApprovalPending, ExecutionSession
from kosika.execution import ExecutionLimits
from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.parser import parse
from kosika.runtime import replay, run

PURE = """
record Result { value: Number }
fn value() -> Result { return {value: 7} }
workflow child() -> Result { step get { let result = value() return result } }
workflow main() -> Result {
  step first { let result = call child() return result }
  step second { let other = call child() return other }
}
"""

TOOLS = """
record Result { value: Number }
tool read { source mcp "host" operation: "read" access: observe output: Result }
agent reader { can: [read] }
workflow main() -> Result {
  step first { let result = execute reader.read() return result }
  step second { let other = execute reader.read() return other }
}
"""


class ExecutionRuntimeTests(unittest.TestCase):
    def test_cancelled_completed_mutation_is_not_reexecuted_on_resume(self) -> None:
        source = (
            TOOLS.replace("access: observe", "access: execute")
            .replace(
                "workflow main() -> Result {",
                "workflow main() -> Result { require approval",
            )
            .replace(
                "step second { let other = execute reader.read() return other }", ""
            )
        )
        program = parse(source)
        cancelled = threading.Event()
        calls = []

        def execute(*_):
            calls.append(True)
            cancelled.set()
            return {"value": 3}

        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "journal.sqlite"
            trace = Path(directory) / "trace.jsonl"
            with self.assertRaises(ApprovalPending) as pending:
                run(
                    program,
                    "main",
                    trace,
                    tool_bindings={"read": execute},
                    session=ExecutionSession(journal, binding_id="test"),
                )
            self.assertEqual(
                json.loads(trace.read_text().splitlines()[-1])["event"], "run_paused"
            )
            request = pending.exception.request
            approval = ApprovalRecord(
                request["plan_hash"], "tester", action_hash=request["action_hash"]
            )
            with self.assertRaises(ExecutionError) as stopped:
                run(
                    program,
                    "main",
                    trace,
                    cancel=cancelled,
                    tool_bindings={"read": execute},
                    session=ExecutionSession(
                        journal, binding_id="test", approvals=[approval]
                    ),
                )
            self.assertEqual(stopped.exception.code, "cancelled")
            cancelled.clear()
            self.assertEqual(
                run(
                    program,
                    "main",
                    trace,
                    cancel=cancelled,
                    tool_bindings={"read": execute},
                    session=ExecutionSession(journal, binding_id="test"),
                ),
                {"value": 3},
            )
            self.assertEqual(len(calls), 1)
            self.assertEqual(replay(trace), {"value": 3})

    def test_pure_computation_has_cancellation_checkpoints(self) -> None:
        class CheckpointCancellation(threading.Event):
            checks = 0

            def is_set(self):
                self.checks += 1
                return self.checks >= 100

        source = """record Result { value: Number }
        fn compute(n: Number) -> Number recurse 32 {
          return if n == 0 { 1 } else { compute(n: n - 1) + compute(n: n - 1) }
        }
        fn wrap() -> Result { return {value: compute(n: 14)} }
        workflow main() -> Result { step get { let result = wrap() return result } }"""
        events: list[dict[str, Any]] = []
        with self.assertRaises(ExecutionError) as stopped:
            run(
                parse(source),
                "main",
                None,
                cancel=CheckpointCancellation(),
                _events_out=events,
            )
        self.assertEqual(stopped.exception.code, "cancelled")
        self.assertTrue(events[-1]["execution"]["function_stack"])
        self.assertLess(events[-1]["execution"]["used"]["function_calls"], 10000)

    def test_trace_write_error_does_not_mask_original_failure(self) -> None:
        cancelled = threading.Event()
        cancelled.set()
        with patch("kosika.runtime._write_trace", side_effect=PermissionError("disk")):
            with self.assertRaises(ExecutionError) as stopped:
                run(parse(PURE), "main", "unused.jsonl", cancel=cancelled)
        self.assertEqual(stopped.exception.code, "cancelled")
        self.assertIn("Could not persist", stopped.exception.__notes__[0])

    def test_function_budget_is_shared_across_nested_workflows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            with self.assertRaises(ExecutionError) as failure:
                run(
                    parse(PURE),
                    "main",
                    trace,
                    limits=ExecutionLimits(max_function_calls=1),
                )
            self.assertEqual(failure.exception.code, "budget")
            events = [json.loads(line) for line in trace.read_text().splitlines()]
            self.assertEqual(events[-1]["event"], "run_failed")
            self.assertEqual(events[-1]["execution"]["used"]["function_calls"], 1)
            self.assertEqual(events[-1]["scope"], ["main", "second", "child", "get"])
            nested = next(
                event for event in events if event["event"] == "workflow_failed"
            )
            self.assertEqual(nested["events"][-1]["event"], "run_failed")
            with self.assertRaises(ValueError):
                replay(trace)
            self.assertEqual(
                run(
                    parse(PURE),
                    "main",
                    trace,
                    limits=ExecutionLimits(max_function_calls=2),
                ),
                {"value": 7},
            )
            self.assertEqual(replay(trace), {"value": 7})

    def test_step_budget_is_shared_and_resets_for_new_runs(self) -> None:
        with self.assertRaises(ExecutionError):
            run(parse(PURE), "main", None, limits=ExecutionLimits(max_steps=3))
        self.assertEqual(
            run(parse(PURE), "main", None, limits=ExecutionLimits(max_steps=4)),
            {"value": 7},
        )

    def test_precancelled_run_has_terminal_trace_and_no_effects(self) -> None:
        cancelled = threading.Event()
        cancelled.set()
        called = []
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            with self.assertRaises(ExecutionError) as failure:
                run(
                    parse(TOOLS),
                    "main",
                    trace,
                    cancel=cancelled,
                    tool_bindings={"read": lambda *_: called.append(True)},
                )
            self.assertEqual(failure.exception.code, "cancelled")
            self.assertEqual(called, [])
            self.assertEqual(
                json.loads(trace.read_text().splitlines()[-1])["event"], "run_cancelled"
            )

    def test_cancel_after_tool_records_outcome_before_stopping(self) -> None:
        cancelled = threading.Event()
        calls = []

        def execute(*_):
            calls.append(True)
            cancelled.set()
            return {"value": 3}

        events: list[dict[str, Any]] = []
        with self.assertRaises(ExecutionError):
            run(
                parse(TOOLS),
                "main",
                None,
                cancel=cancelled,
                tool_bindings={"read": execute},
                _events_out=events,
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(events[-2]["event"], "step_complete")
        self.assertEqual(events[-1]["event"], "run_cancelled")

    def test_tool_budget_prevents_second_dispatch(self) -> None:
        calls = []

        def execute(*_):
            calls.append(True)
            return {"value": 3}

        with self.assertRaises(ExecutionError):
            run(
                parse(TOOLS),
                "main",
                None,
                limits=ExecutionLimits(max_tool_calls=1),
                tool_bindings={"read": execute},
            )
        self.assertEqual(len(calls), 1)

    def test_deadline_is_cooperative_after_host_return(self) -> None:
        clock = [0.0]

        def execute(*_):
            clock[0] = 2.0
            return {"value": 3}

        with patch("kosika.execution.time.monotonic", side_effect=lambda: clock[0]):
            with self.assertRaises(ExecutionError) as failure:
                run(
                    parse(TOOLS),
                    "main",
                    None,
                    limits=ExecutionLimits(deadline_seconds=1),
                    tool_bindings={"read": execute},
                )
        self.assertEqual(failure.exception.code, "deadline")

    def test_model_budget_is_shared_between_model_bindings(self) -> None:
        source = """record Input { } record Result { value: Number }
        model first { input: Input output: Result } model second { input: Input output: Result }
        workflow main() -> Result {
          step a { let result = use_model first() return result }
          step b { let result = use_model second() return result }
        }"""
        first = ScriptedModelProvider([ModelResponse(content='{"value": 1}')])
        second = ScriptedModelProvider([ModelResponse(content='{"value": 2}')])
        with self.assertRaises(ExecutionError) as failure:
            run(
                parse(source),
                "main",
                None,
                model_bindings={"first": first, "second": second},
                limits=ExecutionLimits(max_model_calls=1),
            )
        self.assertEqual(failure.exception.code, "budget")
        self.assertEqual(len(second.requests), 0)

    def test_failure_telemetry_excludes_exception_message(self) -> None:
        def execute(*_):
            raise RuntimeError("SECRET_PROVIDER_CREDENTIAL")

        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            with self.assertRaisesRegex(RuntimeError, "SECRET_PROVIDER_CREDENTIAL"):
                run(parse(TOOLS), "main", trace, tool_bindings={"read": execute})
            self.assertNotIn("SECRET_PROVIDER_CREDENTIAL", trace.read_text())
            self.assertEqual(
                json.loads(trace.read_text().splitlines()[-1])["error"]["type"],
                "RuntimeError",
            )

    def test_cli_configures_budget_and_writes_failure_trace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "main.kos"
            trace = Path(directory) / "trace.jsonl"
            source.write_text(PURE, encoding="utf-8")
            with (
                contextlib.redirect_stderr(io.StringIO()),
                patch.object(
                    sys,
                    "argv",
                    [
                        "kosika",
                        "run",
                        str(source),
                        "--trace",
                        str(trace),
                        "--max-function-calls",
                        "1",
                    ],
                ),
            ):
                code = main()
            self.assertEqual(code, 2)
            self.assertEqual(
                json.loads(trace.read_text().splitlines()[-1])["error"]["code"],
                "budget",
            )

    def test_limits_validate_configuration(self) -> None:
        for options in (
            {"max_steps": 0},
            {"max_function_calls": True},
            {"max_tool_calls": 1.2},
            {"max_model_calls": 1000001},
            {"deadline_seconds": float("nan")},
            {"deadline_seconds": True},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                ExecutionLimits(**options)
