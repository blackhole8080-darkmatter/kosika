import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import kosika.services
from kosika.services import (
    Service,
    ServicesError,
    binding_id,
    command_for,
    prune_runs,
    read_services,
    read_state,
    run_due,
    run_once,
    set_paused,
    status,
    status_text,
)

SERVICES = """
service lookout {
  program "agents/lookout/main.kos"
  say "check"
  every 15 minutes
  allow site "github.com"
  memory "lookout/memory"
  notify from "topic.txt"
}

service gatherer {
  program "agents/gatherer/main.kos"
  allow site "news.google.com"
  allow site "en.wikipedia.org"
  model "qwen/qwen3.8-27b" at "https://api.groq.com/openai/v1" key "TEST_SERVICES_KEY"
}
"""


class ServicesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.file = self.root / "aios.services"
        self.file.write_text(SERVICES, encoding="utf-8")
        (self.root / "topic.txt").write_text("my-secret-topic\n", encoding="utf-8")
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def runner(self, command, env, cwd):
        self.calls.append((command, env))
        said = json.loads(Path(command[command.index("--input") + 1]).read_text())
        return subprocess.CompletedProcess(command, 0, f"{{'text': 'Heard: {said['msg']['text']}'}}\nTrace: x\n", "")

    def test_each_service_gets_exactly_its_own_grants(self) -> None:
        services = read_services(self.file)
        look = command_for(services["lookout"], self.root / "in.json")
        self.assertEqual(look[look.index("--allow-site") + 1], "github.com")
        self.assertEqual(look[look.index("--notify") + 1], "https://ntfy.sh/my-secret-topic")
        self.assertIn("--memory", look)
        self.assertNotIn("--allow-folder", look)
        self.assertNotIn("--allow-shell", look)
        self.assertNotIn("--model", look)
        gather = command_for(services["gatherer"], self.root / "in.json")
        self.assertEqual([gather[i + 1] for i, part in enumerate(gather) if part == "--allow-site"], ["news.google.com", "en.wikipedia.org"])
        self.assertNotIn("--notify", gather)
        self.assertEqual(gather[gather.index("--model") + 1], "qwen/qwen3.8-27b")

    def test_the_shell_can_never_be_granted(self) -> None:
        self.file.write_text('service bad {\n  program "x.kos"\n  allow shell\n}\n', encoding="utf-8")
        with self.assertRaisesRegex(ServicesError, "never the shell"):
            read_services(self.file)

    def test_a_service_runs_when_due_and_not_before(self) -> None:
        start = datetime(2026, 10, 21, 9, 0)
        self.assertEqual(run_due(self.file, start, self.runner), ["lookout"])
        self.assertEqual(read_state(self.file)["last"]["lookout"]["answer"], "Heard: check")
        self.assertEqual(run_due(self.file, datetime.now() + timedelta(minutes=5), self.runner), [])
        self.assertEqual(run_due(self.file, datetime.now() + timedelta(minutes=16), self.runner), ["lookout"])
        self.assertIn("lookout (every 15 minutes): reads github.com, keeps its own memory, sends notes to your phone", status_text(self.file))
        self.assertIn("gatherer (when asked)", status_text(self.file))

    def test_a_paused_service_does_not_run(self) -> None:
        set_paused(self.file, "lookout", True)
        self.assertEqual(run_due(self.file, datetime.now(), self.runner), [])
        self.assertIn("lookout (paused)", status_text(self.file))
        self.assertEqual((self.root / "aios.services.paused.txt").read_text(encoding="utf-8"), "lookout\n")
        set_paused(self.file, "lookout", False)
        self.assertEqual(run_due(self.file, datetime.now(), self.runner), ["lookout"])

    def tracing_runner(self, command, env, cwd):
        """A run that leaves the trace and journal the command named."""

        self.calls.append((command, env))
        Path(command[command.index("--trace") + 1]).write_text('{"step": "greet"}\n', encoding="utf-8")
        Path(command[command.index("--journal") + 1]).write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "{'text': 'Heard: check'}\n", "")

    def test_a_run_keeps_a_trace_and_journal_the_status_names(self) -> None:
        self.assertEqual(run_due(self.file, datetime(2026, 10, 21, 9, 0), self.tracing_runner), ["lookout"])
        service = read_services(self.file)["lookout"]
        command = self.calls[0][0]
        self.assertEqual(command[command.index("--binding-id") + 1], binding_id(service))
        trace = Path(read_state(self.file)["last"]["lookout"]["trace"])
        self.assertEqual(trace.suffix, ".jsonl")
        self.assertEqual(trace.parent, self.root / "aios.services.runs" / "lookout")
        self.assertTrue(trace.is_file())
        self.assertTrue(trace.with_suffix(".journal").is_file())
        shown = status_text(self.file)
        self.assertIn(f"  trace aios.services.runs/lookout/{trace.name}", shown)
        self.assertEqual(status(self.file)[0]["trace"], str(trace))

    def test_a_scheduled_trace_can_be_replayed_without_a_model(self) -> None:
        program = self.root / "hello.kos"
        program.write_text(
            "record Msg { text: Text }\n"
            "record Greeting { text: Text }\n"
            "workflow main(msg: Msg) -> Greeting {\n"
            "  step greet {\n"
            '    let result: Greeting = {text: "Hello, world!"}\n'
            "    return result\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        self.file.write_text(f'service hello {{\n  program "{program.as_posix()}"\n  every 1 hour\n}}\n', encoding="utf-8")
        result = run_once(read_services(self.file)["hello"], folder=self.root / "aios.services.runs" / "hello")
        self.assertTrue(result["ok"], result["answer"])
        trace = Path(result["trace"])
        self.assertTrue(trace.is_file())
        replay = subprocess.run(
            [sys.executable, "-m", "kosika.cli", "replay", str(trace)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=self.root,
        )
        self.assertEqual(replay.returncode, 0, replay.stderr)
        self.assertIn("Hello, world!", replay.stdout)

    def test_a_run_that_never_started_keeps_no_trace_to_point_at(self) -> None:
        def broken(command, env, cwd):
            Path(command[command.index("--journal") + 1]).write_text("", encoding="utf-8")
            Path(command[command.index("--journal") + 1] + ".lock.sqlite").write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(command, 1, "", "the program would not check\n")

        folder = self.root / "runs"
        result = run_once(read_services(self.file)["lookout"], runner=broken, folder=folder)
        self.assertIsNone(result["trace"])
        self.assertEqual(list(folder.iterdir()), [])

    def test_debris_never_costs_a_kept_trace(self) -> None:
        folder = self.root / "runs"
        folder.mkdir()
        (folder / "20261020T090000.jsonl").write_text("{}\n", encoding="utf-8")
        (folder / "20261021T090000.jsonl").write_text("{}\n", encoding="utf-8")
        (folder / "20261022T090000.journal").write_text("", encoding="utf-8")
        (folder / "20261022T090000.journal.lock.sqlite").write_text("", encoding="utf-8")
        self.assertEqual(prune_runs(folder, keep=1), ["20261022T090000", "20261020T090000"])
        self.assertEqual(sorted(file.name for file in folder.iterdir()), ["20261021T090000.jsonl"])

    def test_only_the_newest_runs_are_kept(self) -> None:
        folder = self.root / "aios.services.runs" / "lookout"
        folder.mkdir(parents=True)
        for stem in ("20261020T090000", "20261021T090000", "20261022T090000"):
            (folder / f"{stem}.jsonl").write_text("{}\n", encoding="utf-8")
            (folder / f"{stem}.journal").write_text("", encoding="utf-8")
        self.assertEqual(prune_runs(folder, keep=2), ["20261020T090000"])
        self.assertEqual(sorted(file.name for file in folder.iterdir()), [
            "20261021T090000.journal", "20261021T090000.jsonl",
            "20261022T090000.journal", "20261022T090000.jsonl",
        ])

    def test_a_long_lived_service_stops_adding_runs(self) -> None:
        kept = kosika.services.KEPT_RUNS
        kosika.services.KEPT_RUNS = 2
        self.addCleanup(setattr, kosika.services, "KEPT_RUNS", kept)
        folder = self.root / "aios.services.runs" / "lookout"
        service = read_services(self.file)["lookout"]
        for _ in range(5):
            run_once(service, runner=self.tracing_runner, folder=folder)
        self.assertEqual(len(list(folder.glob("*.jsonl"))), 2)
        self.assertEqual(len(list(folder.glob("*.journal"))), 2)
        self.assertEqual(len(list(folder.iterdir())), 4)

    def test_the_binding_changes_when_a_service_does(self) -> None:
        service = Service("tool", Path("main.kos"), sites=["github.com"])
        self.assertEqual(binding_id(service), binding_id(Service("tool", Path("main.kos"), sites=["github.com"])))
        self.assertNotEqual(binding_id(service), binding_id(Service("tool", Path("main.kos"), sites=["gitlab.com"])))
        self.assertNotEqual(binding_id(service), binding_id(Service("other", Path("main.kos"), sites=["github.com"])))

    def test_a_plain_list_pauses_and_a_plain_status_is_kept(self) -> None:
        (self.root / "aios.services.paused.txt").write_text("// stopped for now\nlookout\n", encoding="utf-8")
        self.assertEqual(run_due(self.file, datetime.now(), self.runner), [])
        (self.root / "aios.services.paused.txt").write_text("", encoding="utf-8")
        run_due(self.file, datetime.now(), self.runner)
        kept = (self.root / "aios.services.status.txt").read_text(encoding="utf-8")
        self.assertIn("lookout (every 15 minutes)", kept)
        self.assertIn("worked: Heard: check", kept)
        self.assertNotIn("paused", json.loads((self.root / "aios.services.state.json").read_text(encoding="utf-8")))

    def test_only_the_named_key_is_passed_on(self) -> None:
        import os

        from kosika.services import environment_for

        os.environ["TEST_SERVICES_KEY"] = "k-1"
        os.environ["OTHER_API_KEY"] = "k-2"
        self.addCleanup(os.environ.pop, "TEST_SERVICES_KEY")
        self.addCleanup(os.environ.pop, "OTHER_API_KEY")
        services = read_services(self.file)
        env = environment_for(services["gatherer"])
        self.assertEqual(env["KOSIKA_MODEL_API_KEY"], "k-1")
        self.assertNotIn("OTHER_API_KEY", env)
        self.assertNotIn("KOSIKA_MODEL_API_KEY", environment_for(services["lookout"]))


if __name__ == "__main__":
    unittest.main()
