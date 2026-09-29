import asyncio
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from kosika.agent import ApprovalRecord
from kosika.controls import (
    ControlledProvider,
    ExecutionError,
    ModelLimits,
    TransientFailure,
)
from kosika.dag import DagNode, replay_dag, run_dag
from kosika.devtools import completions, format_source, inspect_trace
from kosika.durable import ApprovalPending, ExecutionSession
from kosika.evaluation import EvaluationCase, evaluate
from kosika.model import ModelRequest, ModelResponse, ModelUsage, ScriptedModelProvider
from kosika.modules import load_program
from kosika.parser import parse
from kosika.runtime import replay, run
from kosika.stdlib import HTTPReader, JsonFiles, ReadOnlyRetry, SQLiteReader

SOURCE = """record R { text: Text }
model m { input: R output: R }
workflow main(q: R) -> R {
 step answer { let r: R = use_model m(text: q.text) return r }
}"""


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_budget_cancellation_usage_and_retries(self):
        provider = ControlledProvider(
            ScriptedModelProvider(
                [ModelResponse(content="ok", usage=ModelUsage(2, 3, 5))]
            ),
            ModelLimits(max_requests=1, max_tokens=5),
            input_cost_per_token=0.1,
            output_cost_per_token=0.2,
        )
        self.assertEqual(provider.complete(ModelRequest(())).content, "ok")
        self.assertEqual(provider.tokens, 5)
        self.assertAlmostEqual(provider.telemetry[0]["estimated_cost"], 0.8)
        with self.assertRaises(ExecutionError):
            provider.complete(ModelRequest(()))
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaisesRegex(ExecutionError, "cancelled"):
            ControlledProvider(ScriptedModelProvider([]), cancel=cancelled).complete(
                ModelRequest(())
            )
        missing = ControlledProvider(
            ScriptedModelProvider([ModelResponse(content="ok")]),
            ModelLimits(max_tokens=10),
        )
        with self.assertRaisesRegex(ExecutionError, "missing usage"):
            missing.complete(ModelRequest(()))

        class Flaky:
            calls = 0

            def complete(self, request):
                self.calls += 1
                if self.calls == 1:
                    raise TransientFailure("safe fixture retry")
                return ModelResponse(content="ok")

        flaky = ControlledProvider(Flaky(), ModelLimits(attempts=2))
        self.assertEqual(flaky.complete(ModelRequest(())).content, "ok")
        self.assertEqual(len(flaky.telemetry), 2)
        for limits in (
            {"max_requests": True},
            {"attempts": 4},
            {"deadline_seconds": float("nan")},
        ):
            with self.assertRaises(ValueError):
                ModelLimits(**limits)

    def test_durable_resume_reuses_complete_steps_and_rejects_changed_plan(self):
        program = parse(SOURCE)
        journal = self.root / "run.sqlite"
        trace = self.root / "run.jsonl"
        provider = ScriptedModelProvider([ModelResponse(content='{"text":"saved"}')])
        options = {
            "inputs": {"q": {"text": "hello"}},
            "model_bindings": {"m": provider},
        }
        result = run(
            program,
            "main",
            trace,
            session=ExecutionSession(journal, binding_id="fixture-v1"),
            **options,
        )
        self.assertEqual(
            run(
                program,
                "main",
                trace,
                session=ExecutionSession(journal, binding_id="fixture-v1"),
                **options,
            ),
            result,
        )
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(replay(trace), result)
        with self.assertRaisesRegex(ExecutionError, "different"):
            run(
                program,
                "main",
                trace,
                session=ExecutionSession(journal, binding_id="changed"),
                **options,
            )
        with ExecutionSession(journal, binding_id="fixture-v1").lock():
            with self.assertRaisesRegex(ExecutionError, "Another process"):
                run(
                    program,
                    "main",
                    trace,
                    session=ExecutionSession(journal, binding_id="fixture-v1"),
                    **options,
                )

    def test_approval_pause_resume_and_uncertain_outcome(self):
        program = parse("""record R { text: Text }
        tool save { source mcp "host" operation: "save" access: execute input: R output: R }
        agent writer { can: [save] }
        workflow main(q: R) -> R { require approval
          step write { let r: R = execute writer.save(text: q.text) return r }
        }""")
        journal = self.root / "write.sqlite"
        calls = []

        def save(tool, args):
            calls.append(args)
            return args

        options = {
            "inputs": {"q": {"text": "approved"}},
            "tool_bindings": {"save": save},
        }
        with self.assertRaises(ApprovalPending) as caught:
            run(
                program,
                "main",
                None,
                session=ExecutionSession(journal, binding_id="host-v1"),
                **options,
            )
        self.assertEqual(calls, [])
        pending = caught.exception.request
        approval = ApprovalRecord(
            pending["plan_hash"], "tester", action_hash=pending["action_hash"]
        )
        session = ExecutionSession(journal, binding_id="host-v1", approvals=[approval])
        self.assertEqual(
            run(program, "main", None, session=session, **options), {"text": "approved"}
        )
        run(
            program,
            "main",
            None,
            session=ExecutionSession(journal, binding_id="host-v1"),
            **options,
        )
        self.assertEqual(len(calls), 1)
        failed = self.root / "failed.sqlite"

        def crash(tool, args):
            raise RuntimeError("unknown outcome")

        bad_options = {**options, "tool_bindings": {"save": crash}}
        with self.assertRaises(ApprovalPending) as caught:
            run(
                program,
                "main",
                None,
                session=ExecutionSession(failed, binding_id="crash"),
                **bad_options,
            )
        pending = caught.exception.request
        approval = ApprovalRecord(
            pending["plan_hash"], "tester", action_hash=pending["action_hash"]
        )
        with self.assertRaisesRegex(RuntimeError, "unknown outcome"):
            run(
                program,
                "main",
                None,
                session=ExecutionSession(
                    failed, binding_id="crash", approvals=[approval]
                ),
                **bad_options,
            )
        with self.assertRaisesRegex(ExecutionError, "reconciliation"):
            run(
                program,
                "main",
                None,
                session=ExecutionSession(
                    failed, binding_id="crash", approvals=[approval]
                ),
                **bad_options,
            )

    def test_async_dag_replay_and_cycle_rejection(self):
        program = parse(SOURCE)
        nodes = [
            DagNode("first", "main", {"q": {"text": "hello"}}),
            DagNode("second", "main", dependencies={"q": "first"}),
        ]
        models = {
            name: {
                "m": ScriptedModelProvider(
                    [ModelResponse(content=json.dumps({"text": name}))]
                )
            }
            for name in ("first", "second")
        }
        result = asyncio.run(run_dag(program, nodes, self.root / "dag", models=models))
        self.assertEqual(result["second"], {"text": "second"})
        self.assertEqual(replay_dag(self.root / "dag"), result)
        with self.assertRaisesRegex(ValueError, "cycle"):
            asyncio.run(
                run_dag(
                    program,
                    [
                        DagNode("a", "main", dependencies={"q": "b"}),
                        DagNode("b", "main", dependencies={"q": "a"}),
                    ],
                    self.root / "cycle",
                )
            )
        self.assertFalse((self.root / "cycle").exists())

    def test_developer_tools_and_evaluations(self):
        source = self.root / "main.kos"
        source.write_text(SOURCE)
        formatted = format_source(SOURCE)
        self.assertEqual(parse(formatted), parse(SOURCE))
        self.assertEqual(format_source(formatted), formatted)
        self.assertIn("main", completions(source, "ma"))
        cases = [
            EvaluationCase(
                "pass_case", {"q": {"text": "x"}}, contains={"text": "good"}
            ),
            EvaluationCase(
                "fail_case", {"q": {"text": "x"}}, expected={"text": "other"}
            ),
        ]
        report = evaluate(
            load_program(source),
            "main",
            cases,
            self.root / "eval",
            lambda case: {
                "m": ScriptedModelProvider([ModelResponse(content='{"text":"good"}')])
            },
        )
        self.assertFalse(report["passed"])
        self.assertTrue(report["cases"][0]["passed"])
        summary = inspect_trace(self.root / "eval/pass_case.jsonl")
        self.assertEqual(summary["mode"], "exact_replay")
        self.assertEqual(summary["steps"][0]["kind"], "model_call")

    def test_host_adapter_allowlists_and_read_only_database(self):
        data = self.root / "data.json"
        data.write_text('{"value": 1}')
        files = JsonFiles({"data": data})
        self.assertEqual(files.read("data"), {"value": 1})
        with self.assertRaises(PermissionError):
            files.read("../secret")
        database = self.root / "data.sqlite"
        connection = sqlite3.connect(database)
        connection.execute("CREATE TABLE records (name TEXT)")
        connection.execute("INSERT INTO records VALUES (?)", ("safe",))
        connection.commit()
        connection.close()
        reader = SQLiteReader(
            database,
            {
                "find": "SELECT name FROM records WHERE name=:name",
                "write": "DELETE FROM records",
            },
        )
        self.assertEqual(
            reader.query("find", {"name": "safe"}), {"rows": [{"name": "safe"}]}
        )
        with self.assertRaises(sqlite3.DatabaseError):
            reader.query("write", {})
        with self.assertRaises(PermissionError):
            reader.query("unknown", {})
        with self.assertRaises(ValueError):
            HTTPReader({"bad": "http://example.com"})
        with self.assertRaises(PermissionError):
            HTTPReader({"local": "http://127.0.0.1:1"}).get("unknown")
        tool = parse(
            'record R { text: Text } tool x { source mcp "host" operation: "x" access: execute output: R }'
        ).tools[0]
        with self.assertRaises(PermissionError):
            ReadOnlyRetry(lambda tool, args: {})(tool, {})
