"""An agent that chooses its own tools: over Ollama, told about failures,
answering in the record it must, pausing for approval, and seen while it works."""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from kosika.agent import describe_tool
from kosika.app import App
from kosika.model import (
    ChatMessage,
    ModelRequest,
    ModelResponse,
    ModelTool,
    ModelToolCall,
    OpenAICompatibleProvider,
    ScriptedModelProvider,
)
from kosika.modules import load_program
from kosika.runtime import run
from kosika.story import build_story, read_trace, to_text
from kosika.system_tools import bind_system_tools
from kosika.trace_replay import replay_trace

SCOUT = """
record Msg { text: Text }
record Said { text: Text }
record Question { text: Text }
record Where { path: Text }
record Listing { names: Text[] }
record FileText { text: Text }
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

tool look_at {
  source system "files"
  operation: "peek"
  access: observe
  input: Where
  output: FileText
}

agent scout {
  model: thinker
  can: [list_folder, look_at]
  max_steps: 6
}

fn tell(found: Finding) -> Said {
  return {text: found.answer}
}

workflow main(msg: Msg) -> Said {
  found = ask scout(msg.text)
  said  = tell(found)
}
"""

GIT = """
record Msg { text: Text }
record Said { text: Text }
record Question { text: Text }
record Command { args: Text[] }
record Ran { exit_code: Number stdout: Text stderr: Text }
record Finding { answer: Text sources: Text[] }

model thinker {
  input: Question
  output: Finding
}

tool run_python {
  source system "shell"
  operation: "python"
  access: execute
  input: Command
  output: Ran
}

agent hands {
  model: thinker
  can: [run_python]
  max_steps: 4
}

fn tell(found: Finding) -> Said {
  return {text: found.answer}
}

workflow main(msg: Msg) -> Said {
  found = ask hands(msg.text)
  said  = tell(found)
  require approval
}
"""


def picks(*calls: tuple[str, dict[str, Any]]) -> ModelResponse:
    return ModelResponse(
        content=None,
        tool_calls=tuple(
            ModelToolCall(f"c{index}-{name}-{json.dumps(arguments)}", name, arguments)
            for index, (name, arguments) in enumerate(calls)
        ),
        finish_reason="tool_calls",
    )


def says(text: str) -> ModelResponse:
    return ModelResponse(content=text)


def finding(answer: str, *sources: str) -> ModelResponse:
    return says(json.dumps({"answer": answer, "sources": list(sources)}))


class LoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.folder = Path(self._dir.name)
        self.work = self.folder / "work"
        self.work.mkdir()
        (self.work / "calc.py").write_text(
            "def add(a, b):\n    return a + b\n", "utf-8"
        )
        (self.folder / "main.kos").write_text(SCOUT, encoding="utf-8")
        self.program = load_program(self.folder / "main.kos")

    def go(self, responses: list[ModelResponse], **extra: Any) -> tuple[Any, Any]:
        provider = ScriptedModelProvider(responses)
        result = run(
            self.program,
            "main",
            self.folder / "run.jsonl",
            inputs={"msg": {"text": "What does calc.py do?"}},
            model_bindings={"thinker": provider},
            tool_bindings=bind_system_tools(self.program, folder=self.work),
            **extra,
        )
        return result, provider

    def test_the_agent_looks_then_answers_and_the_run_replays(self) -> None:
        seen: list[tuple[str, Any]] = []
        result, provider = self.go(
            [
                picks(("list_folder", {"path": "."})),
                picks(("look_at", {"path": "calc.py"})),
                finding("It adds two numbers.", "calc.py"),
            ],
            on_event=lambda event, depth: seen.append(
                (event["event"], event.get("tool"))
            ),
        )
        self.assertEqual(result, {"text": "It adds two numbers."})
        # The model was told what each tool is for, in words.
        described = {tool.name: tool.description for tool in provider.requests[0].tools}
        self.assertIn("beginning of a text file", described["look_at"])
        self.assertIn("List the names", described["list_folder"])
        # It saw the file before it answered.
        self.assertIn("def add", provider.requests[2].messages[-1].content)
        # A watcher is told each choice as it is made.
        self.assertEqual(
            [tool for event, tool in seen if event == "agent_tool"],
            ["list_folder", "look_at"],
        )
        replay_trace(self.folder / "run.jsonl")
        # What is told is not written: the trace has no such event.
        kinds = [event["event"] for event in read_trace(self.folder / "run.jsonl")]
        self.assertNotIn("agent_tool", kinds)

    def test_the_story_says_which_tools_it_chose(self) -> None:
        self.go(
            [picks(("look_at", {"path": "calc.py"})), finding("It adds.", "calc.py")]
        )
        story = build_story(read_trace(self.folder / "run.jsonl"))
        step = story["steps"][0]
        self.assertEqual(step["kind"], "agent")
        self.assertEqual(step["turns"], 2)
        self.assertEqual(len(step["used"]), 1)
        self.assertTrue(step["used"][0].startswith("look_at(path: calc.py) gave"))
        self.assertIn("it chose look_at(path: calc.py)", to_text(story))

    def test_a_look_that_fails_is_told_to_the_model_which_tries_again(self) -> None:
        result, provider = self.go(
            [
                picks(("look_at", {"path": "missing.py"})),
                picks(("look_at", {"wrong": 1})),
                picks(("look_at", {"path": "calc.py"})),
                finding("It adds two numbers.", "calc.py"),
            ]
        )
        self.assertEqual(result, {"text": "It adds two numbers."})
        told = [request.messages[-1].content for request in provider.requests[1:3]]
        self.assertIn("is not a file", json.loads(told[0])["error"])
        self.assertIn("Invalid arguments", json.loads(told[1])["error"])
        replay_trace(self.folder / "run.jsonl")

    def test_a_tool_it_was_never_given_still_ends_the_run(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "undeclared tool"):
            self.go([picks(("delete_everything", {}))])

    def test_a_path_outside_the_folder_is_refused_and_told(self) -> None:
        (self.folder / "secret.txt").write_text("hidden", encoding="utf-8")
        result, provider = self.go(
            [picks(("look_at", {"path": "../secret.txt"})), finding("I cannot see it.")]
        )
        said = provider.requests[1].messages[-1].content
        self.assertIn("leaves the granted folder", json.loads(said)["error"])
        self.assertNotIn("hidden", said)

    def test_an_answer_in_plain_words_is_put_into_the_record_once(self) -> None:
        result, provider = self.go(
            [
                picks(("look_at", {"path": "calc.py"})),
                says("It adds two numbers, as calc.py shows."),
                finding("It adds two numbers.", "calc.py"),
            ]
        )
        self.assertEqual(result, {"text": "It adds two numbers."})
        reshaping = provider.requests[2]
        self.assertEqual(reshaping.tools, ())
        self.assertTrue(reshaping.json_object)
        self.assertEqual(
            reshaping.messages[-2].content, "It adds two numbers, as calc.py shows."
        )
        returned = [
            event
            for event in read_trace(self.folder / "run.jsonl")
            if event["event"] == "agent_return"
        ][0]
        # What the model first wrote is kept, not hidden.
        self.assertEqual(
            returned["reformatted_from"], "It adds two numbers, as calc.py shows."
        )
        replay_trace(self.folder / "run.jsonl")
        step = build_story(read_trace(self.folder / "run.jsonl"))["steps"][0]
        self.assertIn("calc.py shows", step["reshaped"])

    def test_an_answer_that_never_fits_still_fails_the_step(self) -> None:
        with self.assertRaises(ValueError):
            self.go([says("no idea"), says("still not json")])

    def test_the_agent_cannot_take_more_turns_than_it_is_given(self) -> None:
        with self.assertRaises(RuntimeError):
            self.go([picks(("list_folder", {"path": "."}))] * 7)

    def test_peek_gives_the_beginning_of_a_long_file(self) -> None:
        (self.work / "long.txt").write_text("x" * 9000, encoding="utf-8")
        tools = bind_system_tools(self.program, folder=self.work)
        declared = next(tool for tool in self.program.tools if tool.name == "look_at")
        text = tools["look_at"](declared, {"path": "long.txt"})["text"]
        self.assertEqual(len(text), 4000 + len("\n[the file goes on]"))
        self.assertTrue(text.endswith("[the file goes on]"))
        short = tools["look_at"](declared, {"path": "calc.py"})["text"]
        self.assertEqual(short, "def add(a, b):\n    return a + b\n")


class DescribeTests(unittest.TestCase):
    def test_every_kind_of_tool_is_put_in_words(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        (Path(folder.name) / "main.kos").write_text(GIT, encoding="utf-8")
        program = load_program(Path(folder.name) / "main.kos")
        shell = describe_tool(program.tools[0])
        self.assertIn("Run the program python", shell)
        self.assertIn("The person is asked before it happens.", shell)


class ApprovalInTheLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name) / "agents"
        self.work = Path(self._dir.name) / "work"
        self.work.mkdir()
        (self.root / "hands").mkdir(parents=True)
        (self.root / "hands" / "main.kos").write_text(GIT, encoding="utf-8")

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
            allow_shell=True,
        )

    def test_a_change_the_agent_chose_waits_for_a_yes_then_happens(self) -> None:
        command = ("run_python", {"args": ["-c", "print(6 * 7)"]})
        # The step begins again after the yes, so the model is asked again.
        app = self.app(
            [picks(command), picks(command), finding("The answer is 42.", "python")]
        )
        job = app.start("hands/main.kos", {"text": "what is 6 times 7?"})
        view = self.until(app, job.id, "waiting", "failed")
        self.assertEqual(view["state"], "waiting", view.get("error"))
        self.assertEqual(view["request"]["tool"], "run_python")
        self.assertEqual(view["request"]["operation"], "python")
        with mock.patch(
            "kosika.system_tools.shutil.which", return_value=sys.executable
        ):
            app.decide(job.id, True)
            done = self.until(app, job.id, "done", "failed", "refused")
        self.assertEqual((done["state"], done["text"]), ("done", "The answer is 42."))
        chosen = [e["tool"] for e in done["events"] if e["event"] == "agent_tool"]
        self.assertEqual(chosen, ["run_python", "run_python"])

    def test_refusing_means_the_program_never_starts(self) -> None:
        app = self.app([picks(("run_python", {"args": ["-c", "print(1)"]}))])
        job = app.start("hands/main.kos", {"text": "go"})
        self.until(app, job.id, "waiting")
        with mock.patch("kosika.system_tools.subprocess.run") as started:
            app.decide(job.id, False)
            view = self.until(app, job.id, "refused", "done", "failed")
        self.assertEqual(view["state"], "refused")
        started.assert_not_called()

    def test_a_second_change_in_one_step_is_refused(self) -> None:
        first = ("run_python", {"args": ["-c", "print(1)"]})
        second = ("run_python", {"args": ["-c", "print(2)"]})
        app = self.app([picks(first), picks(first), picks(second)])
        job = app.start("hands/main.kos", {"text": "go"})
        self.until(app, job.id, "waiting")
        with mock.patch(
            "kosika.system_tools.shutil.which", return_value=sys.executable
        ):
            app.decide(job.id, True)
            view = self.until(app, job.id, "done", "failed", "refused")
        self.assertEqual(view["state"], "failed")
        self.assertIn("a second change needs a step of its own", view["error"])


class OllamaToolTests(unittest.TestCase):
    def ask(self, reply: dict[str, Any], request: ModelRequest) -> tuple[Any, Any]:
        sent: list[dict[str, Any]] = []

        def transport(wire: Any, timeout: float) -> bytes:
            sent.append(json.loads(wire.data.decode("utf-8")))
            return json.dumps(reply).encode("utf-8")

        provider = OpenAICompatibleProvider(
            "http://localhost:1", "m", protocol="ollama", transport=transport
        )
        return provider.complete(request), sent[0]

    def test_tools_are_offered_and_a_choice_is_read(self) -> None:
        tool = ModelTool("look_at", "Read a file.", {"type": "object"})
        reply = {
            "message": {
                "content": "",
                "tool_calls": [
                    {"function": {"name": "look_at", "arguments": {"path": "a.py"}}},
                    {
                        "id": "given",
                        "function": {
                            "name": "look_at",
                            "arguments": '{"path": "b.py"}',
                        },
                    },
                ],
            },
            "done": True,
        }
        response, sent = self.ask(
            reply, ModelRequest(messages=(ChatMessage("user", "hi"),), tools=(tool,))
        )
        self.assertEqual(sent["tools"][0]["function"]["name"], "look_at")
        first, second = response.tool_calls
        self.assertEqual((first.name, first.arguments), ("look_at", {"path": "a.py"}))
        self.assertEqual((second.id, second.arguments), ("given", {"path": "b.py"}))
        self.assertTrue(first.id and first.id != second.id)

    def test_an_earlier_choice_and_its_result_are_sent_back_as_ollama_wants(
        self,
    ) -> None:
        messages = (
            ChatMessage("user", "hi"),
            ChatMessage(
                "assistant",
                "",
                tool_calls=(ModelToolCall("c1", "look_at", {"path": "a"}),),
            ),
            ChatMessage("tool", '{"text": "x"}', name="look_at", tool_call_id="c1"),
        )
        _, sent = self.ask(
            {"message": {"content": "done"}, "done": True},
            ModelRequest(messages=messages),
        )
        self.assertEqual(
            sent["messages"][1]["tool_calls"],
            [{"function": {"name": "look_at", "arguments": {"path": "a"}}}],
        )
        self.assertEqual(sent["messages"][2]["tool_name"], "look_at")
        self.assertNotIn("tools", sent)


if __name__ == "__main__":
    unittest.main()
