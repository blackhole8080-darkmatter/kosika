import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from kosika.services import (
    ServicesError,
    command_for,
    read_services,
    read_state,
    run_due,
    set_paused,
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
        set_paused(self.file, "lookout", False)
        self.assertEqual(run_due(self.file, datetime.now(), self.runner), ["lookout"])

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
