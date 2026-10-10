"""An agent that plans first: it looks, asks for all its changes together,
one yes covers exactly that list, and the changes then happen in order."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from kosika.agent import ApprovalRecord
from kosika.app import App
from kosika.checker import CheckError
from kosika.durable import PLAN_TOOL, ApprovalPending, ExecutionSession
from kosika.model import ModelResponse, ModelToolCall, ScriptedModelProvider
from kosika.modules import load_program
from kosika.project_map import _does
from kosika.runtime import run
from kosika.story import read_trace
from kosika.system_tools import bind_system_tools
from kosika.trace_replay import replay_trace

TIDY = """
record Msg { text: Text }
record Said { text: Text }
record Question { text: Text }
record Where { path: Text }
record Listing { names: Text[] }
record NewFile { path: Text text: Text }
record Written { bytes: Number }
record Finding { answer: Text sources: Text[] }

model thinker {
  input: Question
  output: Finding
}

tool list_folder {
  source system "files"
  operation: "list"
  access: observe
  input: Where
  output: Listing
}

tool create_file {
  source system "files"
  operation: "create"
  access: execute
  input: NewFile
  output: Written
}

agent tidy {
  model: thinker
  can: [list_folder, create_file]
  max_steps: 6
  plan: first
}

fn tell(found: Finding) -> Said {
  return {text: found.answer}
}

workflow main(msg: Msg) -> Said {
  found = ask tidy(msg.text)
  said  = tell(found)
  require approval
}
"""

LOOK = ("list_folder", {"path": "."})
ONE = ("create_file", {"path": "a.txt", "text": "first"})
TWO = ("create_file", {"path": "notes/b.txt", "text": "second"})


def picks(*calls: tuple[str, dict[str, Any]]) -> ModelResponse:
    return ModelResponse(
        content=None,
        tool_calls=tuple(
            ModelToolCall(f"c{index}-{name}-{json.dumps(arguments)}", name, arguments)
            for index, (name, arguments) in enumerate(calls)
        ),
        finish_reason="tool_calls",
    )


def finding(answer: str) -> ModelResponse:
    return ModelResponse(content=json.dumps({"answer": answer, "sources": []}))


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.top = Path(self._dir.name)
        self.root = self.top / "agents"
        self.work = self.top / "work"
        self.work.mkdir()
        (self.root / "tidy").mkdir(parents=True)
        self.source = self.root / "tidy" / "main.kos"
        self.source.write_text(TIDY, encoding="utf-8")


class RuntimeTests(Base):
    def attempt(
        self,
        provider: ScriptedModelProvider,
        approvals: list[ApprovalRecord],
    ) -> dict[str, Any]:
        program = load_program(self.source)
        session = ExecutionSession(
            self.top / "run.sqlite", binding_id="test", approvals=approvals
        )
        return run(
            program,
            "main",
            self.top / "run.jsonl",
            inputs={"msg": {"text": "make two files"}},
            model_bindings={"thinker": provider},
            tool_bindings=bind_system_tools(program, folder=self.work),
            session=session,
        )

    def yes(self, pending: ApprovalPending) -> ApprovalRecord:
        return ApprovalRecord(
            plan_hash=pending.request["plan_hash"],
            action_hash=pending.request["action_hash"],
            approved_by="tester",
        )

    def test_one_yes_covers_the_whole_list_and_nothing_happens_before_it(self) -> None:
        provider = ScriptedModelProvider(
            [picks(LOOK), picks(ONE, TWO), finding("Made two files.")]
        )
        with self.assertRaises(ApprovalPending) as asked:
            self.attempt(provider, [])
        request = asked.exception.request
        self.assertEqual(request["tool"], PLAN_TOOL)
        self.assertEqual(
            request["arguments"],
            {
                "agent": "tidy",
                "actions": [
                    {"tool": ONE[0], "arguments": ONE[1]},
                    {"tool": TWO[0], "arguments": TWO[1]},
                ],
            },
        )
        self.assertEqual(list(self.work.iterdir()), [])

        result = self.attempt(provider, [self.yes(asked.exception)])
        self.assertEqual(result, {"text": "Made two files."})
        self.assertEqual((self.work / "a.txt").read_text(encoding="utf-8"), "first")
        self.assertEqual(
            (self.work / "notes" / "b.txt").read_text(encoding="utf-8"), "second"
        )
        # The model was asked three times in all: after the yes, what it had
        # already said was given back instead of asking it again.
        self.assertEqual(len(provider.requests), 3)
        replay_trace(self.top / "run.jsonl")
        returned = [
            event
            for event in read_trace(self.top / "run.jsonl")
            if event["event"] == "agent_return"
        ][0]
        asked_for = [
            call["name"]
            for message in returned["messages"]
            for call in message.get("tool_calls") or []
        ]
        self.assertEqual(asked_for, ["list_folder", "create_file", "create_file"])

    def test_the_model_is_told_to_plan(self) -> None:
        provider = ScriptedModelProvider([finding("Nothing to do.")])
        self.assertEqual(self.attempt(provider, []), {"text": "Nothing to do."})
        told = " ".join(
            message.content or ""
            for message in provider.requests[0].messages
            if message.role == "system"
        )
        self.assertIn("You plan before you change anything", told)

    def test_a_yes_for_another_plan_does_not_count(self) -> None:
        provider = ScriptedModelProvider([picks(ONE), finding("Done.")])
        with self.assertRaises(ApprovalPending) as asked:
            self.attempt(provider, [])
        wrong = ApprovalRecord(
            plan_hash=asked.exception.request["plan_hash"],
            action_hash="0" * 64,
            approved_by="tester",
        )
        with self.assertRaises(ApprovalPending):
            self.attempt(provider, [wrong])
        self.assertEqual(list(self.work.iterdir()), [])

    def test_changes_asked_for_a_second_time_do_not_happen(self) -> None:
        provider = ScriptedModelProvider([picks(ONE), picks(TWO), finding("Done.")])
        with self.assertRaises(ApprovalPending) as asked:
            self.attempt(provider, [])
        result = self.attempt(provider, [self.yes(asked.exception)])
        self.assertEqual(result, {"text": "Done."})
        # The approved change happened; the one that was never shown did
        # not, and the model was told why.
        self.assertTrue((self.work / "a.txt").exists())
        self.assertFalse((self.work / "notes").exists())
        told = json.loads(provider.requests[-1].messages[-1].content)
        self.assertIn("already been carried out", told["error"])
        replay_trace(self.top / "run.jsonl")

    def test_when_one_change_fails_the_ones_after_it_do_not_happen(self) -> None:
        (self.work / "a.txt").write_text("already here", encoding="utf-8")
        provider = ScriptedModelProvider([picks(ONE, TWO), finding("Done.")])
        with self.assertRaises(ApprovalPending) as asked:
            self.attempt(provider, [])
        with self.assertRaises(Exception) as failed:
            self.attempt(provider, [self.yes(asked.exception)])
        self.assertNotIsInstance(failed.exception, ApprovalPending)
        self.assertEqual(
            (self.work / "a.txt").read_text(encoding="utf-8"), "already here"
        )
        self.assertFalse((self.work / "notes").exists())

    def test_without_a_way_to_ask_nothing_is_changed(self) -> None:
        program = load_program(self.source)
        provider = ScriptedModelProvider([picks(ONE), finding("Done.")])
        with self.assertRaises(PermissionError):
            run(
                program,
                "main",
                self.top / "run.jsonl",
                inputs={"msg": {"text": "go"}},
                model_bindings={"thinker": provider},
                tool_bindings=bind_system_tools(program, folder=self.work),
            )
        self.assertEqual(list(self.work.iterdir()), [])


class CheckTests(Base):
    def problem(self, old: str, new: str) -> str:
        self.source.write_text(TIDY.replace(old, new), encoding="utf-8")
        with self.assertRaises(CheckError) as failed:
            load_program(self.source)
        return str(failed.exception)

    def test_a_planner_must_be_able_to_change_something(self) -> None:
        self.assertIn(
            "plans first but can change nothing",
            self.problem("can: [list_folder, create_file]", "can: [list_folder]"),
        )

    def test_a_planner_needs_turns_to_plan_and_to_answer(self) -> None:
        self.assertIn("at least 2", self.problem("max_steps: 6", "max_steps: 1"))

    def test_plan_takes_only_first(self) -> None:
        self.source.write_text(
            TIDY.replace("plan: first", "plan: later"), encoding="utf-8"
        )
        with self.assertRaisesRegex(Exception, "Agent plan must be `first`"):
            load_program(self.source)


class MapTests(Base):
    def test_the_map_says_the_agent_shows_its_plan(self) -> None:
        program = load_program(self.source)
        step = program.workflows[0].steps[0]
        self.assertEqual(
            _does(program, step.call),
            "asks the agent tidy, which shows you its whole plan of changes "
            "and waits for one yes",
        )

    def test_the_example_builder_plans_first(self) -> None:
        example = (
            Path(__file__).resolve().parents[1]
            / "examples"
            / "agents"
            / "builder"
            / "main.kos"
        )
        builder = load_program(example).agents[0]
        self.assertTrue(builder.plan_first)
        self.assertEqual(builder.can, ("list_folder", "create_file"))


class StudioTests(Base):
    def until(self, app: App, run_id: str, *states: str) -> dict[str, Any]:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            view = app.poll(run_id, 0)
            if view["state"] in states:
                return view
            time.sleep(0.01)
        self.fail(f"the run never reached {states}")

    def app(self, responses: list[ModelResponse]) -> App:
        provider = ScriptedModelProvider(responses)
        return App(
            self.root,
            bind_models=lambda program: {"thinker": provider},
            allow_folder=self.work,
        )

    def test_studio_shows_the_plan_and_one_yes_carries_it_out(self) -> None:
        app = self.app([picks(LOOK), picks(ONE, TWO), finding("Made two files.")])
        job = app.start("tidy/main.kos", {"text": "make two files"})
        view = self.until(app, job.id, "waiting", "failed")
        self.assertEqual(view["state"], "waiting", view.get("error"))
        request = view["request"]
        self.assertEqual((request["tool"], request["agent"]), ("plan", "tidy"))
        self.assertEqual(
            [(step["tool"], step["operation"]) for step in request["plan"]],
            [("create_file", "create"), ("create_file", "create")],
        )
        self.assertEqual(request["plan"][1]["arguments"]["path"], "notes/b.txt")
        self.assertFalse(request["walled"])
        self.assertEqual(list(self.work.iterdir()), [])
        app.decide(job.id, True)
        done = self.until(app, job.id, "done", "failed", "refused")
        self.assertEqual((done["state"], done["text"]), ("done", "Made two files."))
        self.assertTrue((self.work / "notes" / "b.txt").exists())
        planned = [e for e in done["events"] if e["event"] == "agent_plan"]
        self.assertEqual(planned[0]["actions"], 2)

    def test_no_means_none_of_it_happens(self) -> None:
        app = self.app([picks(ONE, TWO)])
        job = app.start("tidy/main.kos", {"text": "go"})
        self.until(app, job.id, "waiting")
        app.decide(job.id, False)
        view = self.until(app, job.id, "refused", "done", "failed")
        self.assertEqual(view["state"], "refused")
        self.assertEqual(list(self.work.iterdir()), [])

    def test_the_page_can_draw_a_plan(self) -> None:
        page = (
            Path(__file__).resolve().parents[1] / "kosika" / "app_assets" / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn("request.plan", page)
        self.assertIn("Allow this plan", page)
        self.assertIn("One yes covers exactly these", page)


if __name__ == "__main__":
    unittest.main()
