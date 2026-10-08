"""Kosika Studio: answers as they are written, programs started with a yes,
swarms run live, and agents built in the window."""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from kosika.app import App, AppError, describe, find_agents
from kosika.model import (
    ChatMessage,
    ModelRequest,
    ModelResponse,
    OpenAICompatibleProvider,
    ScriptedModelProvider,
)

RUNNER = """// Starts python, after a yes.
record Msg { text: Text }
record Command { args: Text[] }
record Ran { exit_code: Number stdout: Text stderr: Text }
record Said { text: Text }

tool run_python {
  source system "shell"
  operation: "python"
  access: execute
  input: Command
  output: Ran
}

agent runner {
  can: [run_python]
  max_steps: 1
}

fn command(msg: Msg) -> Command {
  return {args: ["-c", "print(6 * 7)"]}
}

fn told(ran: Ran) -> Said {
  return {text: ran.stdout.trim()}
}

workflow main(msg: Msg) -> Said {
  plan = command(msg)
  ran  = execute runner.run_python(plan.args)
  said = told(ran)
  require approval
}
"""

SWARM = """// A writer and a critic talk on a board.
record Brief { topic: Text }
record Remark { text: Text }
record Outcome { draft: Text posts: Number }

model writer_model
model critic_model

agent writer {
  model: writer_model
  max_steps: 1
}

agent critic {
  model: critic_model
  max_steps: 1
}

board desk of Remark

fn remark(reply: Reply) -> Remark {
  return {text: reply.text}
}

fn outcome(draft: Reply, seen: DeskBoard) -> Outcome {
  return {draft: draft.text, posts: seen.posts.length()}
}

workflow refine(brief: Brief) -> Outcome {
  draft   = ask writer(brief.topic)
  said    = remark(draft)
  posted  = post said to desk as writer
  verdict = ask critic(draft.text)
  noted   = remark(verdict)
  replied = post noted to desk as critic
  after   = read desk
  result  = outcome(draft, after)
}

swarm Pair {
  agents: [writer, critic]
  workflow: refine
  max_rounds: 2
  require approval
}
"""


def reply(text: str) -> ModelResponse:
    return ModelResponse(content=json.dumps({"text": text}))


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name) / "agents"
        self.work = Path(self._dir.name) / "work"
        self.work.mkdir()
        for name, source in (("runner", RUNNER), ("pair", SWARM)):
            (self.root / name).mkdir(parents=True)
            (self.root / name / "main.kos").write_text(source, encoding="utf-8")

    def until(self, app: App, run: str, *states: str) -> dict[str, Any]:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            view = app.poll(run, 0)
            if view["state"] in states:
                return view
            time.sleep(0.01)
        self.fail(f"the run never reached {states}")


class ProgramTests(Base):
    def app(self, allow_shell: bool) -> App:
        return App(
            self.root,
            bind_models=lambda program: {},
            allow_folder=self.work,
            allow_shell=allow_shell,
        )

    def test_a_program_starts_only_after_a_yes_and_the_card_names_it(self) -> None:
        app = self.app(allow_shell=True)
        self.assertTrue(app.overview()["shell"])
        job = app.start("runner/main.kos", {"text": "go"})
        view = self.until(app, job.id, "waiting", "failed")
        self.assertEqual(view["state"], "waiting")
        self.assertEqual(view["request"]["kind"], "system:shell")
        self.assertEqual(view["request"]["operation"], "python")
        self.assertEqual(view["request"]["arguments"], {"args": ["-c", "print(6 * 7)"]})
        with mock.patch(
            "kosika.system_tools.shutil.which", return_value=sys.executable
        ):
            app.decide(job.id, True)
            done = self.until(app, job.id, "done", "failed", "refused")
        self.assertEqual((done["state"], done["text"]), ("done", "42"))

    def test_a_refused_program_never_starts(self) -> None:
        app = self.app(allow_shell=True)
        job = app.start("runner/main.kos", {"text": "go"})
        self.until(app, job.id, "waiting")
        with mock.patch("kosika.system_tools.subprocess.run") as started:
            app.decide(job.id, False)
            view = self.until(app, job.id, "refused", "done", "failed")
        self.assertEqual(view["state"], "refused")
        started.assert_not_called()

    def test_without_the_grant_no_program_can_start_even_with_a_yes(self) -> None:
        app = self.app(allow_shell=False)
        self.assertFalse(app.overview()["shell"])
        job = app.start("runner/main.kos", {"text": "go"})
        with mock.patch("kosika.system_tools.subprocess.run") as started:
            view = self.until(app, job.id, "waiting", "failed", "done")
            if view["state"] == "waiting":
                app.decide(job.id, True)
                view = self.until(app, job.id, "failed", "done", "refused")
        self.assertEqual(view["state"], "failed")
        started.assert_not_called()

    def test_the_shell_grant_needs_a_folder(self) -> None:
        app = App(self.root, bind_models=lambda program: {}, allow_shell=True)
        self.assertFalse(app.allow_shell)


class SwarmTests(Base):
    def app(self) -> App:
        providers = {
            "writer_model": ScriptedModelProvider(
                [reply("Tea is calm."), reply("Tea is calm.")]
            ),
            "critic_model": ScriptedModelProvider(
                [reply("Too plain."), reply("Too plain.")]
            ),
        }
        return App(self.root, bind_models=lambda program: providers)

    def test_a_program_that_is_only_a_swarm_is_listed(self) -> None:
        self.assertIn(self.root / "pair" / "main.kos", find_agents(self.root))
        app = self.app()
        card = describe(app.agent("pair/main.kos"), detail=False)
        self.assertFalse(card["has_main"])
        self.assertEqual(card["swarms"][0]["agents"], ["writer", "critic"])

    def test_a_swarm_runs_only_with_a_yes(self) -> None:
        with self.assertRaises(AppError) as caught:
            self.app().start_swarm(
                "pair/main.kos",
                {"swarm": "Pair", "inputs": {"brief": {"topic": "tea"}}},
            )
        self.assertEqual(caught.exception.status, 403)
        with self.assertRaises(AppError) as caught:
            self.app().start_swarm(
                "pair/main.kos", {"swarm": "Nope", "inputs": {}, "allow": True}
            )
        self.assertEqual(caught.exception.status, 404)

    def test_a_swarm_is_followed_round_by_round_with_what_was_said(self) -> None:
        app = self.app()
        job = app.start_swarm(
            "pair/main.kos",
            {"swarm": "Pair", "inputs": {"brief": {"topic": "tea"}}, "allow": True},
        )
        view = self.until(app, job.id, "done", "failed")
        self.assertEqual(view["state"], "done", view.get("error"))
        self.assertEqual(view["result"]["result"]["draft"], "Tea is calm.")
        self.assertEqual(view["result"]["rounds"], 2)
        events = view["events"]
        self.assertEqual(
            [e["round"] for e in events if e["event"] == "swarm_round"], [1, 2]
        )
        said = [(e["author"], e["said"]) for e in events if "said" in e]
        self.assertEqual(
            said[:2], [("writer", "Tea is calm."), ("critic", "Too plain.")]
        )
        asked = [e["agent"] for e in events if e["event"] == "agent_call"]
        self.assertEqual(asked, ["writer", "critic", "writer", "critic"])


class BuildingTests(Base):
    def app(self) -> App:
        return App(self.root)

    def test_text_is_checked_without_being_written(self) -> None:
        app = self.app()
        path = self.root / "runner" / "main.kos"
        good = app.check_source("runner/main.kos", RUNNER)
        bad = app.check_source(
            "runner/main.kos",
            RUNNER.replace(
                "-> Said {\n  return {text: ran", "-> Sad {\n  return {text: ran"
            ),
        )
        self.assertEqual((good["ok"], good["workflows"]), (True, 1))
        self.assertFalse(bad["ok"])
        self.assertIn("Sad", bad["error"])
        self.assertEqual(path.read_text(encoding="utf-8"), RUNNER)

    def test_saving_writes_the_file_and_says_whether_it_checks(self) -> None:
        app = self.app()
        path = self.root / "runner" / "main.kos"
        changed = RUNNER.replace("print(6 * 7)", "print(7 * 7)")
        self.assertEqual(app.save_source("runner/main.kos", changed)["ok"], True)
        self.assertEqual(path.read_text(encoding="utf-8"), changed)
        broken = app.save_source("runner/main.kos", "workflow main( -> Nope {")
        self.assertEqual((broken["saved"], broken["ok"]), (True, False))
        # A program that does not check can still be found, opened and mended.
        self.assertEqual(app.save_source("runner/main.kos", RUNNER)["ok"], True)
        with self.assertRaises(AppError):
            app.save_source("../outside/main.kos", RUNNER)

    def test_a_new_agent_starts_from_a_working_example(self) -> None:
        app = self.app()
        made = app.new_agent("my_helper", "notes")
        self.assertEqual(made["id"], "my_helper/main.kos")
        self.assertTrue(
            app.check_source(
                made["id"], (self.root / made["id"]).read_text(encoding="utf-8")
            )["ok"]
        )
        self.assertGreater(len(app.tests(made["id"])["results"]), 0)
        for name, kind in (
            ("my_helper", "notes"),  # already there: nothing is overwritten
            ("../escape", "notes"),
            ("Bad Name", "notes"),
            ("fine", "hello"),
            ("fine", "nope"),
        ):
            with self.subTest(name=name, kind=kind), self.assertRaises(AppError):
                app.new_agent(name, kind)
        self.assertFalse((self.root.parent / "escape").exists())


class StreamingTests(unittest.TestCase):
    def provider(self, protocol: str, lines: list[bytes]) -> tuple[Any, list[str]]:
        provider = OpenAICompatibleProvider(
            "http://localhost:1", "m", protocol=protocol
        )
        heard: list[str] = []
        provider.on_text = heard.append
        self.sent: list[dict[str, Any]] = []

        def stream(request: Any) -> Any:
            self.sent.append(json.loads(request.data.decode("utf-8")))
            return iter(lines)

        provider._lines = stream  # type: ignore[method-assign]
        return provider, heard

    def ask(self, provider: Any) -> ModelResponse:
        return provider.complete(
            ModelRequest(messages=(ChatMessage(role="user", content="hi"),))
        )

    def test_ollama_pieces_are_heard_and_put_back_together(self) -> None:
        lines = [
            json.dumps({"message": {"content": piece}, "done": False}).encode() + b"\n"
            for piece in ('{"text": "', "Par", 'is."}')
        ]
        lines.append(
            json.dumps(
                {
                    "message": {"content": ""},
                    "done": True,
                    "eval_count": 5,
                    "prompt_eval_count": 3,
                }
            ).encode()
        )
        provider, heard = self.provider("ollama", lines)
        response = self.ask(provider)
        self.assertEqual(heard, ['{"text": "', "Par", 'is."}'])
        self.assertEqual(response.content, '{"text": "Paris."}')
        self.assertEqual(response.finish_reason, "stop")
        assert response.usage is not None
        self.assertEqual(response.usage.total_tokens, 8)
        self.assertIs(self.sent[0]["stream"], True)

    def test_openai_pieces_are_heard_and_put_back_together(self) -> None:
        def data(payload: Any) -> bytes:
            return b"data: " + json.dumps(payload).encode() + b"\n"

        lines = [
            data({"choices": [{"delta": {"content": "Hel"}}]}),
            b"\n",
            data({"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]}),
            data(
                {
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 2,
                        "total_tokens": 4,
                    },
                }
            ),
            b"data: [DONE]\n",
        ]
        provider, heard = self.provider("openai", lines)
        response = self.ask(provider)
        self.assertEqual((heard, response.content), (["Hel", "lo"], "Hello"))
        assert response.usage is not None
        self.assertEqual(response.usage.total_tokens, 4)

    def test_nothing_streams_unless_someone_is_listening(self) -> None:
        sent: list[dict[str, Any]] = []

        def transport(request: Any, timeout: float) -> bytes:
            sent.append(json.loads(request.data.decode("utf-8")))
            return json.dumps({"message": {"content": "x"}, "done": True}).encode()

        provider = OpenAICompatibleProvider(
            "http://localhost:1", "m", protocol="ollama", transport=transport
        )
        self.assertEqual(
            provider.complete(
                ModelRequest(messages=(ChatMessage(role="user", content="hi"),))
            ).content,
            "x",
        )
        self.assertIs(sent[0]["stream"], False)

    def test_a_listener_that_breaks_cannot_break_the_answer(self) -> None:
        lines = [json.dumps({"message": {"content": "ok"}, "done": True}).encode()]
        provider, _ = self.provider("ollama", lines)

        def broken(piece: str) -> None:
            raise RuntimeError("the display fell over")

        provider.on_text = broken
        self.assertEqual(self.ask(provider).content, "ok")

    def test_a_broken_stream_is_a_provider_failure(self) -> None:
        from kosika.model import ModelProviderError

        provider, _ = self.provider("ollama", [b"not json\n"])
        with self.assertRaises(ModelProviderError):
            self.ask(provider)


if __name__ == "__main__":
    unittest.main()
