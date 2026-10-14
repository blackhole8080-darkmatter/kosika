"""Both engines are held to the conformance suite.

The reference engine (Python) must print what the suite expects. The native
engine (Go) must print the same, and must split every Kosika file in the
repository into the same tokens. The native half is skipped on a machine
without Go, and says so.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from kosika import conformance
from kosika.lexer import LexError, lex

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "native"


class ReferenceTests(unittest.TestCase):
    def test_the_suite_has_programs_and_each_has_its_expected_report(self) -> None:
        programs = conformance.programs()
        self.assertGreaterEqual(len(programs), 24)
        for program in programs:
            with self.subTest(program.name):
                self.assertTrue(conformance.expected_path(program).is_file())

    def test_the_reference_engine_prints_what_is_expected(self) -> None:
        for program in conformance.programs():
            with self.subTest(program.name):
                text, code = conformance.report(program)
                wanted = conformance.expected_path(program).read_text(encoding="utf-8")
                self.assertEqual(text, wanted)
                # A case passes; a failing program fails, and is not refused;
                # a refused one never runs.
                self.assertEqual(code, conformance.EXIT_CODES[program.parent.name])

    def test_a_passing_case_has_no_failure_and_a_failing_one_has_some(self) -> None:
        for program in conformance.programs():
            with self.subTest(program.name):
                wanted = conformance.expected_path(program).read_text(encoding="utf-8")
                if program.parent.name == "refused":
                    # Refused by the checker, not on the way in: no path.
                    self.assertTrue(wanted.startswith("Check failed: "))
                    self.assertEqual(wanted.count("\n"), 1)
                    self.assertNotIn(".kos", wanted)
                    continue
                self.assertIn(" passed, ", wanted)
                if program.parent.name == "cases":
                    self.assertNotIn("FAIL  ", wanted)
                    self.assertTrue(wanted.rstrip().endswith(" passed, 0 failed"))
                else:
                    self.assertIn("FAIL  ", wanted)


@unittest.skipUnless(
    shutil.which("go"), "Go is not installed, so the native engine is not built"
)
class NativeTests(unittest.TestCase):
    engine: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls._dir = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._dir.cleanup)
        cls.engine = Path(cls._dir.name) / "kosika-native.exe"
        built = subprocess.run(
            ["go", "build", "-o", str(cls.engine), "."],
            cwd=NATIVE,
            capture_output=True,
            text=True,
        )
        if built.returncode != 0:
            raise AssertionError("the native engine does not build:\n" + built.stderr)

    def run_engine(self, *arguments: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run([str(self.engine), *arguments], capture_output=True)

    def test_it_prints_what_the_suite_expects(self) -> None:
        for program in conformance.programs():
            with self.subTest(program.name):
                ran = self.run_engine("test", str(program))
                printed = ran.stdout.decode("utf-8").replace("\r\n", "\n")
                wanted = conformance.expected_path(program).read_text(encoding="utf-8")
                self.assertEqual(printed, wanted, ran.stderr.decode("utf-8", "replace"))
                self.assertEqual(
                    ran.returncode, conformance.EXIT_CODES[program.parent.name]
                )

    def test_it_splits_every_kosika_file_into_the_reference_tokens(self) -> None:
        listed = subprocess.run(
            ["git", "ls-files", "*.kos"], cwd=ROOT, capture_output=True, text=True
        ).stdout.split()
        files = [ROOT / name for name in listed] or conformance.programs()
        self.assertGreater(len(files), 0)
        compared = 0
        for path in files:
            with self.subTest(path.name):
                source = path.read_bytes().decode("utf-8")
                ran = self.run_engine("tokens", str(path))
                got = [
                    [token["kind"], token["value"], token["line"], token["column"]]
                    for token in map(
                        json.loads, ran.stdout.decode("utf-8").splitlines()
                    )
                ]
                try:
                    wanted = [[t.kind, t.value, t.line, t.column] for t in lex(source)]
                except LexError as exc:
                    # Both must stop at the same place.
                    self.assertNotEqual(ran.returncode, 0)
                    self.assertEqual(
                        ran.stderr.decode("utf-8", "replace").split(": ")[0],
                        str(exc).split(": ")[0],
                    )
                    continue
                self.assertEqual(ran.returncode, 0)
                self.assertEqual(got, wanted)
                compared += len(wanted)
        self.assertGreater(compared, 1000)

    def test_what_it_cannot_do_yet_it_refuses_by_name(self) -> None:
        ran = self.run_engine(
            "test", str(ROOT / "benchmarks" / "spec" / "reference" / "20_swarm.kos")
        )
        self.assertEqual(ran.returncode, 1)
        said = ran.stderr.decode("utf-8", "replace")
        self.assertIn("kosika-native cannot read a swarm yet", said)
        self.assertEqual(ran.stdout, b"")
        with tempfile.TemporaryDirectory() as scratch:
            quoting = Path(scratch) / "quoting.kos"
            quoting.write_text(
                "record T { text: Text }\nmodel assistant\n"
                "workflow main(t: T) -> Summary {\n"
                "  s = summarize t.text with evidence\n}\n",
                encoding="utf-8",
            )
            ran = self.run_engine("test", str(quoting))
        self.assertEqual(ran.returncode, 1)
        self.assertIn(
            "kosika-native cannot run a step with evidence yet",
            ran.stderr.decode("utf-8", "replace"),
        )
        with tempfile.TemporaryDirectory() as scratch:
            voting = Path(scratch) / "voting.kos"
            voting.write_text(
                "record A { text: Text }\nmodel assistant\n"
                "workflow main(a: A) -> Summary {\n"
                "  s = summarize a.text vote 3\n}\n",
                encoding="utf-8",
            )
            ran = self.run_engine("test", str(voting))
        self.assertEqual(ran.returncode, 1)
        self.assertIn(
            "kosika-native cannot run a step with vote yet",
            ran.stderr.decode("utf-8", "replace"),
        )

    def test_a_text_that_is_not_kosika_is_refused_with_its_place(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            bad = Path(scratch) / "bad.kos"
            bad.write_text("fn f() -> Number {\n  return 1 +\n}\n", encoding="utf-8")
            ran = self.run_engine("test", str(bad))
        self.assertEqual(ran.returncode, 1)
        self.assertIn("Expected expression at 3:1, got '}'", ran.stderr.decode("utf-8"))

    def test_it_asks_a_model_server_as_the_reference_does(self) -> None:
        # A stand-in model server that answers in either wire format, and
        # keeps what each engine asked of it.
        replies = [
            {"label": "billing", "confidence": 0.9},
            {"number": "A-7", "total": 40},
            {"text": "Charged twice."},
            {"answer": True, "confidence": 0.8, "reason": "It says so."},
        ]
        asked: list[dict] = []

        class Server(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - the http.server name
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                asked.append({"path": self.path, **body})
                reply = json.dumps(replies[(len(asked) - 1) % len(replies)])
                if self.path.endswith("/api/chat"):
                    answer = {"message": {"role": "assistant", "content": reply}, "done": True}
                else:
                    answer = {
                        "choices": [
                            {"message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}
                        ]
                    }
                data = json.dumps(answer).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_: object) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}"
        program = ROOT / "conformance" / "cases" / "thinking.kos"
        with tempfile.TemporaryDirectory() as scratch:
            inputs = Path(scratch) / "input.json"
            inputs.write_text(json.dumps({"ticket": {"text": "Charged twice."}}))
            for protocol, endpoint in (("openai", url + "/v1"), ("ollama", url)):
                with self.subTest(protocol):
                    flags = [
                        "--input", str(inputs), "--model", "small",
                        "--model-url", endpoint, "--model-protocol", protocol,
                    ]
                    asked.clear()
                    reference = subprocess.run(
                        [sys.executable, "-m", "kosika.cli", "run", str(program),
                         *flags, "--trace", str(Path(scratch) / "trace.jsonl")],
                        cwd=ROOT, capture_output=True, text=True,
                    )
                    by_reference = list(asked)
                    asked.clear()
                    native = self.run_engine("run", str(program), *flags)
                    self.assertEqual(native.returncode, 0, native.stderr.decode())
                    self.assertEqual(
                        native.stdout.decode("utf-8").splitlines()[0],
                        reference.stdout.splitlines()[0],
                    )
                    # Each engine asked the same questions, word for word.
                    self.assertEqual(asked, by_reference)

    def test_an_agent_calls_its_tools_as_the_reference_does(self) -> None:
        # A stand-in model that asks for the disk tool, then answers from
        # what the tool gave back; the tool is the example MCP server.
        asked: list[dict] = []

        class Server(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - the http.server name
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                asked.append({"path": self.path, **body})
                ollama = self.path.endswith("/api/chat")
                if not any(m["role"] == "tool" for m in body["messages"]):
                    if ollama:
                        call = {"function": {"name": "check_disk", "arguments": {}}}
                        answer = {"message": {"role": "assistant", "content": "", "tool_calls": [call]}, "done": True}
                    else:
                        call = {"id": "call_1", "type": "function",
                                "function": {"name": "check_disk", "arguments": "{}"}}
                        answer = {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [call]},
                                               "finish_reason": "tool_calls"}]}
                else:
                    told = json.loads(body["messages"][-1]["content"])
                    reply = json.dumps({"text": f"{told['used_percent']} percent used", "did": []})
                    message = {"role": "assistant", "content": reply}
                    answer = (
                        {"message": message, "done": True}
                        if ollama
                        else {"choices": [{"message": message, "finish_reason": "stop"}]}
                    )
                data = json.dumps(answer).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_: object) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}"
        with tempfile.TemporaryDirectory() as scratch:
            program = Path(scratch) / "disk.kos"
            program.write_text(
                "record Question { text: Text }\n"
                "record Answer { text: Text did: Text[] }\n"
                "record DiskStatus { mount: Text used_percent: Number }\n"
                'tool check_disk {\n  source mcp "local_stdio"\n  operation: "get_disk_info"\n'
                "  access: observe\n  output: DiskStatus\n}\n"
                "model thinker {\n  input: Question\n  output: Answer\n}\n"
                "agent watcher {\n  model: thinker\n  can: [check_disk]\n"
                '  instructions: "Answer from the disk."\n}\n'
                "workflow main(question: Question) -> Answer {\n"
                "  answer = ask watcher(question.text)\n}\n",
                encoding="utf-8",
            )
            inputs = Path(scratch) / "input.json"
            inputs.write_text(json.dumps({"question": {"text": "How full is the disk?"}}))
            for protocol, endpoint in (("openai", url + "/v1"), ("ollama", url)):
                with self.subTest(protocol):
                    flags = [
                        "--input", str(inputs), "--model", "small",
                        "--model-url", endpoint, "--model-protocol", protocol,
                        "--mcp-server", str(ROOT / "examples" / "mcp_host_server.py"),
                        "--mcp-manifest", str(ROOT / "examples" / "mcp_manifest.json"),
                    ]
                    asked.clear()
                    reference = subprocess.run(
                        [sys.executable, "-m", "kosika.cli", "run", str(program),
                         *flags, "--trace", str(Path(scratch) / "trace.jsonl")],
                        cwd=ROOT, capture_output=True, text=True,
                    )
                    by_reference = list(asked)
                    asked.clear()
                    native = self.run_engine("run", str(program), *flags)
                    self.assertEqual(native.returncode, 0, native.stderr.decode())
                    self.assertEqual(
                        native.stdout.decode("utf-8").splitlines()[0],
                        reference.stdout.splitlines()[0],
                    )
                    self.assertEqual(len(asked), 2)
                    self.assertEqual(asked, by_reference)

    def test_a_change_waits_for_its_approval_as_in_the_reference(self) -> None:
        # Each engine keeps its own journal and names a run by its own plan
        # hash, so an approval serves only the engine that asked for it. What
        # each asks, and what an approval then lets happen, are the same.
        program = ROOT / "examples" / "approval_mutation.kos"
        server = [
            "--mcp-source", "approval_mutation",
            "--mcp-server", str(ROOT / "examples" / "approval_mutation_mcp_server.py"),
            "--mcp-manifest", str(ROOT / "examples" / "approval_mutation_manifest.json"),
        ]

        def engine(name: str, folder: Path, *flags: str) -> subprocess.CompletedProcess[str]:
            command = (
                [sys.executable, "-m", "kosika.cli", "run", str(program)]
                if name == "reference"
                else [str(self.engine), "run", str(program)]
            )
            environment = {**os.environ, "PYTHONPATH": str(ROOT)}
            return subprocess.run(
                [*command, "--workflow", "apply_change", "--input", "in.json", *server,
                 "--trace", "trace.jsonl", *flags],
                cwd=folder, capture_output=True, text=True, env=environment,
            )

        asked = {}
        for name in ("reference", "native"):
            with self.subTest(name), tempfile.TemporaryDirectory() as scratch:
                folder = Path(scratch)
                (folder / "in.json").write_text(json.dumps({"request": {"change": "rename"}}))
                journal = ["--journal", "run.journal", "--binding-id", "here"]
                # Without a journal the change is refused outright.
                ran = engine(name, folder)
                self.assertEqual(ran.returncode, 2)
                self.assertIn("requires a verified approval record", ran.stderr)
                # With one, the run waits for an approval of this one action.
                ran = engine(name, folder, *journal)
                self.assertEqual(ran.returncode, 3, ran.stderr)
                request = json.loads(ran.stdout)
                asked[name] = {k: request[k] for k in ("status", "tool", "arguments", "step")}
                self.assertFalse((folder / "mutation_sink.json").exists())
                approval = {
                    "plan_hash": request["plan_hash"],
                    "action_hash": request["action_hash"],
                    "approved_by": "tester",
                }
                (folder / "approval.json").write_text(json.dumps(approval))
                ran = engine(name, folder, *journal, "--approval-record", "approval.json")
                self.assertEqual(ran.returncode, 0, ran.stderr)
                self.assertIn("'status': 'applied'", ran.stdout)
                written = (folder / "mutation_sink.json").stat().st_mtime_ns
                # Begun again, the change that already happened is not repeated.
                ran = engine(name, folder, *journal, "--approval-record", "approval.json")
                self.assertEqual(ran.returncode, 0, ran.stderr)
                self.assertEqual((folder / "mutation_sink.json").stat().st_mtime_ns, written)
        self.assertEqual(asked["native"], asked["reference"])

    def test_its_trace_is_the_reference_trace(self) -> None:
        # Apart from the time, the program's hash and how long it took, the
        # native engine writes down a run as the reference does, and each
        # engine's replay accepts the other's trace.
        ignored = {"timestamp", "program_hash", "elapsed_seconds", "function_stack", "plan_hash"}

        def clean(value: object) -> object:
            if isinstance(value, dict):
                return {k: clean(v) for k, v in value.items() if k not in ignored}
            if isinstance(value, list):
                return [clean(v) for v in value]
            return value

        source = (
            "record Ask { text: Text kind: Text }\n"
            "record Said { text: Text n: Number }\n"
            "record Answer { text: Text did: Text[] }\n"
            "record Box {\n  label: Text\n  size: Number\n"
            "  fn grown(self, by: Number) -> Box { return {..self, size: self.size + by} }\n}\n"
            "model writer {\n  input: Ask\n  output: Said\n}\n"
            "model helper {\n  input: Ask\n  output: Answer\n}\n"
            "default model assistant\n"
            'agent plain {\n  model: helper\n  can: []\n  instructions: "Help."\n}\n'
            "fn scaled(base: Number, factor: Number) -> Number {\n"
            "  let shift: Number = 2\n"
            "  fn apply(value: Number) -> Number { return value * factor + shift }\n"
            "  return apply(value: base)\n}\n"
            "fn sized(ask: Ask) -> Said {\n  let box: Box = {label: ask.text, size: 1}\n"
            "  let bigger = box.grown(scaled(2, 3))\n  return {text: bigger.label, n: bigger.size}\n}\n"
            "fn quiet(ask: Ask) -> Said { return {text: ask.text, n: 0} }\n"
            "fn bad(ask: Ask) -> Said {\n  if ask.text != \"\" { fail \"no\" }\n  return {text: \"\", n: 0}\n}\n"
            "workflow inner(ask: Ask) -> Said {\n  said = sized(ask)\n}\n"
            "workflow main(ask: Ask) -> Said {\n"
            "  first = sized(ask)\n"
            "  nested = call inner(ask)\n"
            "  third = bad(ask) otherwise quiet(ask)\n"
            "  retried = use_model writer(text: first.text, kind: ask.kind) retry 2\n"
            '  kind = classify ask.text into ["a", "b"]\n'
            "  brief = summarize ask.text\n"
            "  helped = ask plain(text: ask.text, kind: ask.kind)\n"
            "  routed = match ask.kind {\n"
            '    case "loud" { sized(ask) }\n    case _ { quiet(ask) }\n  }\n}\n'
        )
        fixture = {
            "writer": [{"text": "x"}, {"text": "y", "n": "bad"}, {"text": "ok", "n": 2}],
            "assistant": [{"label": "a", "confidence": 1}, {"text": "short"}],
            "helper": [{"words": 1}, {"text": "fine", "did": ["claimed"]}],
        }
        with tempfile.TemporaryDirectory() as scratch:
            folder = Path(scratch)
            (folder / "p.kos").write_text(source, encoding="utf-8")
            (folder / "fixture.json").write_text(json.dumps(fixture), encoding="utf-8")
            (folder / "input.json").write_text(json.dumps({"ask": {"text": "hi", "kind": "loud"}}))
            flags = ["--input", str(folder / "input.json"), "--model-fixture", str(folder / "fixture.json")]
            subprocess.run(
                [sys.executable, "-m", "kosika.cli", "run", str(folder / "p.kos"), *flags,
                 "--trace", str(folder / "reference.jsonl")],
                cwd=ROOT, capture_output=True, text=True, check=True,
            )
            native = self.run_engine("run", str(folder / "p.kos"), *flags, "--trace", str(folder / "native.jsonl"))
            self.assertEqual(native.returncode, 0, native.stderr.decode())

            def events(name: str) -> list:
                lines = (folder / name).read_text(encoding="utf-8").splitlines()
                return [clean(json.loads(line)) for line in lines]

            self.assertEqual(events("native.jsonl"), events("reference.jsonl"))
            for written, reader in (("native.jsonl", "reference"), ("reference.jsonl", "native")):
                trace = str(folder / written)
                ran = (
                    subprocess.run([sys.executable, "-m", "kosika.cli", "replay", trace],
                                   cwd=ROOT, capture_output=True, text=True)
                    if reader == "reference"
                    else self.run_engine("replay", trace)
                )
                self.assertEqual(ran.returncode, 0, f"{reader} replay of {written}")

    def test_it_reaches_a_pinned_mcp_server_as_the_reference_does(self) -> None:
        program = ROOT / "examples" / "test_host_live.kos"
        flags = [
            "--mcp-server", str(ROOT / "examples" / "mcp_host_server.py"),
            "--mcp-manifest", str(ROOT / "examples" / "mcp_manifest.json"),
        ]
        with tempfile.TemporaryDirectory() as scratch:
            reference = subprocess.run(
                [sys.executable, "-m", "kosika.cli", "run", str(program), *flags,
                 "--trace", str(Path(scratch) / "trace.jsonl")],
                cwd=ROOT, capture_output=True, text=True,
            )
        native = self.run_engine("run", str(program), *flags)
        self.assertEqual(native.returncode, 0, native.stderr.decode())
        self.assertEqual(
            native.stdout.decode("utf-8").splitlines()[0],
            reference.stdout.splitlines()[0],
        )
        # A manifest that pins nothing for the tool is refused by both.
        with tempfile.TemporaryDirectory() as scratch:
            empty = Path(scratch) / "manifest.json"
            pinned = json.loads((ROOT / "examples" / "mcp_manifest.json").read_text())
            pinned["tools"] = {}
            empty.write_text(json.dumps(pinned), encoding="utf-8")
            flags[-1] = str(empty)
            reference = subprocess.run(
                [sys.executable, "-m", "kosika.cli", "run", str(program), *flags,
                 "--trace", str(Path(scratch) / "trace.jsonl")],
                cwd=ROOT, capture_output=True, text=True,
            )
            native = self.run_engine("run", str(program), *flags)
        self.assertEqual(native.returncode, 2)
        self.assertEqual(
            native.stderr.decode("utf-8").strip().splitlines()[-1],
            reference.stderr.strip().splitlines()[-1],
        )

    def test_it_runs_in_an_environment_as_the_reference_does(self) -> None:
        # A project's kosika.env names what its programs may reach; both
        # engines find it above the program, take everything from it, keep
        # their traces and journals where it says, and refuse alike.
        ignored = {"timestamp", "program_hash", "elapsed_seconds", "function_stack", "plan_hash"}

        def clean(value: object) -> object:
            if isinstance(value, dict):
                return {k: clean(v) for k, v in value.items() if k not in ignored}
            if isinstance(value, list):
                return [clean(v) for v in value]
            return value

        examples = ROOT / "examples"
        server = [Path(sys.executable).as_posix(), (examples / "approval_mutation_mcp_server.py").as_posix()]
        settings = (
            "environment test {\n"
            '  models answer from "fixture.json"\n'
            "  budget 3 model calls, 60 seconds\n"
            '  keep ".kosika/test"\n}\n'
            "environment live {\n"
            f"  mcp approval_mutation: {json.dumps(server)}\n"
            f'    pinned "{(examples / "approval_mutation_manifest.json").as_posix()}"\n'
            '  keep ".kosika/live"\n}\n'
        )
        source = (
            "record Ask { text: Text }\nrecord Said { text: Text n: Number }\n"
            "model writer {\n  input: Ask\n  output: Said\n}\n"
            "workflow main(ask: Ask) -> Said {\n  said = use_model writer(text: ask.text)\n}\n"
        )
        seen: dict[str, dict[str, object]] = {}
        for name in ("reference", "native"):
            with self.subTest(name), tempfile.TemporaryDirectory() as scratch:
                project = Path(scratch)
                (project / "kosika.env").write_text(settings, encoding="utf-8")
                (project / "fixture.json").write_text(json.dumps({"writer": [{"text": "ok", "n": 1}]}))
                (project / "programs").mkdir()
                (project / "programs" / "p.kos").write_text(source, encoding="utf-8")
                shutil.copy(examples / "approval_mutation.kos", project / "programs")
                (project / "in.json").write_text(json.dumps({"ask": {"text": "hi"}}))
                (project / "req.json").write_text(json.dumps({"request": {"change": "rename"}}))

                def engine(*arguments: str) -> subprocess.CompletedProcess[str]:
                    command = (
                        [sys.executable, "-m", "kosika.cli", "run"]
                        if name == "reference"
                        else [str(self.engine), "run"]
                    )
                    return subprocess.run(
                        [*command, *arguments], cwd=project, capture_output=True, text=True,
                        env={**os.environ, "PYTHONPATH": str(ROOT)},
                    )

                def said(ran: subprocess.CompletedProcess[str]) -> tuple[int, str, str]:
                    lines = [line for line in ran.stderr.splitlines() if line.strip()]
                    return ran.returncode, ran.stdout.splitlines()[0] if ran.stdout else "", (
                        lines[-1].replace(str(project), "PROJECT") if lines else ""
                    )

                outcome: dict[str, object] = {}
                ran = engine("programs/p.kos", "--env", "test", "--input", "in.json")
                self.assertEqual(ran.returncode, 0, ran.stderr)
                outcome["result"] = ran.stdout.splitlines()[0]
                traces = list((project / ".kosika" / "test" / "traces").iterdir())
                self.assertEqual(len(traces), 1)
                outcome["trace"] = [
                    clean(json.loads(line))
                    for line in traces[0].read_text(encoding="utf-8").splitlines()
                ]
                for refused in (
                    ["--env", "test", "--model-fixture", "fixture.json"],
                    ["--env", "test", "--journal", "j", "--binding-id", "b"],
                    ["--env", "elsewhere"],
                ):
                    outcome[" ".join(refused)] = said(engine("programs/p.kos", "--input", "in.json", *refused))
                # A change waits for its approval in the environment's own
                # journal, and once approved is made, and the journal let go.
                change = ["programs/approval_mutation.kos", "--env", "live",
                          "--workflow", "apply_change", "--input", "req.json"]
                ran = engine(*change)
                self.assertEqual(ran.returncode, 3, ran.stderr)
                request = json.loads(ran.stdout)
                outcome["asked"] = {k: request[k] for k in ("status", "tool", "arguments", "step")}
                approval = {k: request[k] for k in ("plan_hash", "action_hash")}
                (project / "approval.json").write_text(json.dumps({**approval, "approved_by": "tester"}))
                ran = engine(*change, "--approval-record", "approval.json")
                self.assertEqual(ran.returncode, 0, ran.stderr)
                outcome["applied"] = ran.stdout.splitlines()[0]
                self.assertTrue((project / "mutation_sink.json").exists())
                journals = project / ".kosika" / "live" / "journals"
                self.assertFalse(
                    [p for p in journals.iterdir() if not p.name.endswith((".lock.sqlite", ".approvals.sqlite", ".approvals.json"))]
                )
                # A broken environment file is refused with its place.
                (project / "kosika.env").write_text("environment test { budget 3 hours }")
                outcome["broken"] = said(engine("programs/p.kos", "--env", "test", "--input", "in.json"))
                seen[name] = outcome
        for key in seen["reference"]:
            self.assertEqual(seen["native"][key], seen["reference"][key], key)

    def test_an_agent_makes_its_approved_change_as_the_reference_does(self) -> None:
        # A stand-in model asks for a change (twice over, when `second`),
        # then answers from what the change gave back. An agent that plans
        # shows its changes as one plan; one with `plan: off` makes one
        # change a step. Each waits for its approval, makes the change once
        # approved, and does not make it again.
        ignored = {"timestamp", "program_hash", "elapsed_seconds", "function_stack", "plan_hash", "duration_ms"}

        def clean(value: object) -> object:
            if isinstance(value, dict):
                return {k: clean(v) for k, v in value.items() if k not in ignored}
            if isinstance(value, list):
                return [clean(v) for v in value]
            return value

        asked: list[dict] = []
        second = [False]

        class Server(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - the http.server name
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                asked.append(body)
                told = [m for m in body["messages"] if m["role"] == "tool"]
                if not told or (second[0] and len(told) == 1):
                    n = len(told) + 1
                    call = {"id": f"call_{n}", "type": "function",
                            "function": {"name": "apply_change",
                                         "arguments": json.dumps({"change": f"rename{n}"})}}
                    answer = {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [call]},
                                           "finish_reason": "tool_calls"}]}
                else:
                    reply = json.dumps({"text": "done: " + told[-1]["content"], "did": []})
                    answer = {"choices": [{"message": {"role": "assistant", "content": reply},
                                           "finish_reason": "stop"}]}
                data = json.dumps(answer).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_: object) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}/v1"
        examples = ROOT / "examples"
        for plan, twice in (("", False), ("", True), ("  plan: off\n", False), ("  plan: off\n", True)):
            second[0] = twice
            source = (
                "record Ask { text: Text }\nrecord Answer { text: Text did: Text[] }\n"
                "record MutationRequest { change: Text }\n"
                "record MutationResult { status: Text change: Text sink: Text }\n"
                'tool apply_change {\n  source mcp "approval_mutation"\n  operation: "apply_change"\n'
                "  access: execute\n  input: MutationRequest\n  output: MutationResult\n}\n"
                "model thinker {\n  input: Ask\n  output: Answer\n}\n"
                f"agent operator {{\n  model: thinker\n  can: [apply_change]\n{plan}}}\n"
                "workflow main(ask: Ask) -> Answer {\n  answer = ask operator(ask.text)\n}\n"
            )
            seen: dict[str, dict[str, object]] = {}
            for name in ("reference", "native"):
                with self.subTest(plan=plan, second=twice, engine=name), tempfile.TemporaryDirectory() as scratch:
                    folder = Path(scratch)
                    (folder / "p.kos").write_text(source, encoding="utf-8")
                    (folder / "in.json").write_text(json.dumps({"ask": {"text": "rename it"}}))
                    command = (
                        [sys.executable, "-m", "kosika.cli", "run"]
                        if name == "reference"
                        else [str(self.engine), "run"]
                    )
                    flags = [
                        "p.kos", "--input", "in.json", "--model", "small", "--model-url", url,
                        "--mcp-source", "approval_mutation",
                        "--mcp-server", str(examples / "approval_mutation_mcp_server.py"),
                        "--mcp-manifest", str(examples / "approval_mutation_manifest.json"),
                        "--trace", "trace.jsonl",
                    ]

                    def engine(*more: str) -> tuple[int, str, str, list[dict]]:
                        asked.clear()
                        ran = subprocess.run(
                            [*command, *flags, *more], cwd=folder, capture_output=True, text=True,
                            env={**os.environ, "PYTHONPATH": str(ROOT)},
                        )
                        said = [line for line in ran.stderr.splitlines() if line.strip()]
                        return ran.returncode, ran.stdout.strip(), said[-1] if said else "", list(asked)

                    outcome: dict[str, object] = {"without a journal": engine()}
                    journal = ["--journal", "run.journal", "--binding-id", "here"]
                    code, printed, said, models = engine(*journal)
                    self.assertEqual(code, 3, said)
                    request = json.loads(printed)
                    outcome["asked"] = ({k: request[k] for k in ("status", "tool", "arguments", "step")}, models)
                    approval = {k: request[k] for k in ("plan_hash", "action_hash")}
                    (folder / "approval.json").write_text(json.dumps({**approval, "approved_by": "tester"}))
                    outcome["approved"] = engine(*journal, "--approval-record", "approval.json")
                    if outcome["approved"][0] == 0:
                        outcome["trace"] = [
                            clean(json.loads(line))
                            for line in (folder / "trace.jsonl").read_text(encoding="utf-8").splitlines()
                        ]
                    outcome["changed"] = (folder / "mutation_sink.json").read_text(encoding="utf-8")
                    outcome["again"] = engine(*journal, "--approval-record", "approval.json")
                    self.assertEqual((folder / "mutation_sink.json").read_text(encoding="utf-8"), outcome["changed"])
                    seen[name] = outcome
            for key in seen["reference"]:
                self.assertEqual(seen["native"][key], seen["reference"][key], f"{plan!r} {twice} {key}")


if __name__ == "__main__":
    unittest.main()
