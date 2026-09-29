import asyncio
import contextlib
import io
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from kosika import cli, devtools
from kosika.controls import ControlledProvider, ExecutionError, ModelLimits
from kosika.dag import DagNode, replay_dag, run_dag
from kosika.durable import ExecutionSession
from kosika.model import (
    ModelRequest,
    ModelResponse,
    ModelUsage,
    OpenAICompatibleProvider,
    ScriptedModelProvider,
)
from kosika.parser import parse
from kosika.runtime import run
from kosika.stdlib import HTTPReader, ReadOnlyRetry
from tests.test_execution_platform import SOURCE


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_actual_http_transport_and_redirect_denial(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                requests.append(
                    json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                )
                payload = {
                    "choices": [
                        {
                            "message": {"content": '{"text":"transport verified"}'},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 4,
                        "total_tokens": 7,
                    },
                }
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

            def do_GET(self):
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/ok")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{"ok":true}')

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            provider = ControlledProvider(
                OpenAICompatibleProvider(base + "/v1", "fixture", timeout=2),
                ModelLimits(max_output_tokens=17),
            )
            result = run(
                parse(SOURCE),
                "main",
                self.root / "http.jsonl",
                inputs={"q": {"text": "test"}},
                model_bindings={"m": provider},
            )
            self.assertEqual(result, {"text": "transport verified"})
            self.assertEqual(requests[0]["max_tokens"], 17)
            self.assertEqual(provider.telemetry[0]["usage"]["total_tokens"], 7)
            reader = HTTPReader({"ok": base + "/ok", "redirect": base + "/redirect"})
            self.assertEqual(reader.get("ok"), {"ok": True})
            with self.assertRaises(PermissionError):
                reader.get("redirect")
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_parallel_overlap_preflight_and_failed_dependency(self):
        barrier = threading.Barrier(2)

        class Concurrent:
            def complete(self, request):
                barrier.wait(timeout=5)
                return ModelResponse(content='{"text":"parallel"}')

        program = parse(SOURCE)
        nodes = [DagNode(name, "main", {"q": {"text": name}}) for name in ("a", "b")]
        bindings = {name: {"m": Concurrent()} for name in ("a", "b")}
        result = asyncio.run(
            run_dag(
                program, nodes, self.root / "parallel", models=bindings, concurrency=2
            )
        )
        self.assertEqual(len(result), 2)
        with self.assertRaises(PermissionError):
            asyncio.run(
                run_dag(
                    program, nodes, self.root / "unbound", models={"a": bindings["a"]}
                )
            )
        self.assertFalse((self.root / "unbound").exists())
        dependent = ScriptedModelProvider([])
        with self.assertRaises(RuntimeError):
            asyncio.run(
                run_dag(
                    program,
                    [nodes[0], DagNode("child", "main", dependencies={"q": "a"})],
                    self.root / "failed",
                    models={
                        "a": {"m": ScriptedModelProvider([])},
                        "child": {"m": dependent},
                    },
                )
            )
        self.assertEqual(dependent.requests, [])
        self.assertFalse((self.root / "failed/dag.json").exists())
        manifest_path = self.root / "parallel/dag.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["version"] = True
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            replay_dag(manifest_path.parent)

    def test_cli_devtools_and_durable_configuration(self):
        source = self.root / "main.kos"
        source.write_text(SOURCE)
        fixture = self.root / "models.json"
        fixture.write_text('{"m":[{"text":"hello"}]}')
        inputs = self.root / "inputs.json"
        inputs.write_text('{"q":{"text":"question"}}')
        trace = self.root / "trace.jsonl"
        telemetry = self.root / "telemetry.json"

        def command(function, args):
            with (
                patch("sys.argv", ["kosika", *map(str, args)]),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                return function()

        arguments = [
            "run",
            source,
            "--input",
            inputs,
            "--model-fixture",
            fixture,
            "--trace",
            trace,
            "--journal",
            self.root / "run.sqlite",
            "--binding-id",
            "fixture-v1",
            "--telemetry",
            telemetry,
        ]
        self.assertEqual(command(cli.main, arguments), 0)
        self.assertEqual(len(json.loads(telemetry.read_text())["m"]), 1)
        self.assertEqual(command(cli.main, arguments), 0)
        self.assertEqual(json.loads(telemetry.read_text())["m"], [])
        self.assertEqual(command(devtools.main, ["inspect", trace]), 0)
        self.assertEqual(
            command(devtools.main, ["complete", source, "--prefix", "ma"]), 0
        )
        self.assertEqual(command(devtools.main, ["format", source, "--write"]), 0)
        self.assertEqual(command(devtools.main, ["format", source, "--check"]), 0)
        cases = self.root / "cases.json"
        cases.write_text(
            json.dumps(
                [
                    {
                        "name": "basic",
                        "inputs": {"q": {"text": "x"}},
                        "contains": {"text": "hello"},
                        "responses": {"m": [{"text": "hello"}]},
                    }
                ]
            )
        )
        self.assertEqual(
            command(
                devtools.main, ["eval", source, cases, "--output", self.root / "eval"]
            ),
            0,
        )
        cases.write_text('[{"bad":1}]')
        self.assertEqual(
            command(
                devtools.main,
                ["eval", source, cases, "--output", self.root / "invalid"],
            ),
            2,
        )

    def test_session_same_object_concurrency_and_inspection(self):
        session = ExecutionSession(self.root / "journal.sqlite", binding_id="test")
        self.assertEqual(session.inspect(), [])
        errors = []

        def attempt():
            try:
                run(parse(SOURCE), "main", None, session=session)
            except RuntimeError as exc:
                errors.append(str(exc))

        with session.lock():
            thread = threading.Thread(target=attempt)
            thread.start()
            thread.join()
        self.assertIn("another thread", errors[0])
        with session.lock():
            session.bind({"test": 1})
            session.start(("main", "read"), None, {})
        self.assertEqual(session.inspect()[0]["state"], "started")

    def test_budget_overrun_and_invalid_usage_are_terminal(self):
        for usage in (ModelUsage(1, 5, 6), ModelUsage(-1, 2, 1), ModelUsage()):
            provider = ControlledProvider(
                ScriptedModelProvider([ModelResponse(content="ok", usage=usage)]),
                ModelLimits(max_tokens=5),
            )
            with self.assertRaises(ExecutionError):
                provider.complete(ModelRequest(()))
            self.assertIsNotNone(provider.telemetry[0]["error"])
        provider = ControlledProvider(
            ScriptedModelProvider([]), ModelLimits(deadline_seconds=0.01)
        )
        provider._start = 0
        with self.assertRaisesRegex(ExecutionError, "deadline"):
            provider.complete(ModelRequest(()))
        with self.assertRaises(ValueError):
            ReadOnlyRetry(lambda tool, args: None, attempts=4)


if __name__ == "__main__":
    unittest.main()
