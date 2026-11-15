"""An agent's plan, checked by a function the program itself writes in Kosika."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika.agent import ApprovalRecord
from kosika.checker import CheckError
from kosika.durable import ApprovalPending, ExecutionSession
from kosika.model import ModelResponse, ModelToolCall, ScriptedModelProvider
from kosika.modules import load_program
from kosika.runtime import run
from kosika.system_tools import bind_system_tools
from kosika.trace_replay import replay_trace

# An agent that writes notes. Its own rule, in Kosika: a note may not be
# made shorter than it is, and no file may be called secret.
NOTES = """
record Msg { text: Text }
record Said { text: Text }
record Question { text: Text }
record Where { path: Text }
record FileText { text: Text }
record NewFile { path: Text text: Text }
record Written { bytes: Number }
record Finding { answer: Text }
record Planned { path: Text text: Text before: Text exists: Bool }

model thinker {
  input: Question
  output: Finding
}

tool read_file {
  source system "files"
  operation: "read"
  access: observe
  input: Where
  output: FileText
}

tool write_file {
  source system "files"
  operation: "write"
  access: execute
  input: NewFile
  output: Written
}

fn careful(request: Text, files: Planned[]) -> Text[] {
  var wrong: Text[] = []
  for file in files {
    if file.exists and file.text.lines().length() < file.before.lines().length() {
      wrong = wrong.append(file.path + ": it would lose lines it has now")
    }
    if file.path.contains("secret") {
      wrong = wrong.append(file.path + ": no file may be called secret, whatever \\"" + request + "\\" says")
    }
  }
  return wrong
}

agent scribe {
  model: thinker
  can: [read_file, write_file]
  max_steps: 6
  check: careful
}

fn tell(found: Finding) -> Said {
  return {text: found.answer}
}

workflow main(msg: Msg) -> Said {
  found = ask scribe(msg.text)
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


def writes(path: str, text: str) -> tuple[str, dict[str, Any]]:
    return ("write_file", {"path": path, "text": text})


DONE = ModelResponse(content=json.dumps({"answer": "Done."}))


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.top = Path(self._dir.name)
        self.work = self.top / "work"
        self.work.mkdir()
        (self.work / "note.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
        self.source = self.top / "main.kos"
        self.source.write_text(NOTES, encoding="utf-8")

    def attempt(
        self, provider: ScriptedModelProvider, approvals: list[ApprovalRecord]
    ) -> dict[str, Any]:
        program = load_program(self.source)
        return run(
            program,
            "main",
            self.top / "run.jsonl",
            inputs={"msg": {"text": "tidy my notes"}},
            model_bindings={"thinker": provider},
            tool_bindings=bind_system_tools(program, folder=self.work),
            session=ExecutionSession(
                self.top / "run.sqlite", binding_id="test", approvals=approvals
            ),
        )

    def yes(self, pending: ApprovalPending) -> ApprovalRecord:
        return ApprovalRecord(
            plan_hash=pending.request["plan_hash"],
            action_hash=pending.request["action_hash"],
            approved_by="tester",
        )


class RunTests(Base):
    def test_a_plan_the_programs_own_check_refuses_goes_back_to_the_model(self) -> None:
        provider = ScriptedModelProvider(
            [
                picks(writes("note.txt", "one\n")),
                picks(writes("note.txt", "one\ntwo\nthree\nfour\n")),
                DONE,
            ]
        )
        with self.assertRaises(ApprovalPending) as asked:
            self.attempt(provider, [])
        # The person is asked only about the plan that passed, and is not
        # warned of anything.
        plan = asked.exception.request["arguments"]
        self.assertEqual(
            plan["actions"][0]["arguments"]["text"], "one\ntwo\nthree\nfour\n"
        )
        self.assertNotIn("warnings", plan)
        # The model was told, in the program's own words, what was wrong.
        told = json.dumps([m.content for m in provider.requests[1].messages])
        self.assertIn("note.txt: it would lose lines it has now", told)
        self.assertEqual(
            (self.work / "note.txt").read_text(encoding="utf-8"), "one\ntwo\nthree\n"
        )

        result = self.attempt(provider, [self.yes(asked.exception)])
        self.assertEqual(result, {"text": "Done."})
        self.assertEqual(
            (self.work / "note.txt").read_text(encoding="utf-8"),
            "one\ntwo\nthree\nfour\n",
        )
        self.assertEqual(len(provider.requests), 3)
        replay_trace(self.top / "run.jsonl")

    def test_a_plan_still_wrong_is_shown_with_the_checks_own_words(self) -> None:
        provider = ScriptedModelProvider(
            [
                picks(writes("secret.txt", "x")),
                picks(writes("secret.txt", "y")),
                picks(writes("secret.txt", "z")),
                DONE,
            ]
        )
        with self.assertRaises(ApprovalPending) as asked:
            self.attempt(provider, [])
        self.assertEqual(
            asked.exception.request["arguments"]["warnings"],
            ['secret.txt: no file may be called secret, whatever "tidy my notes" says'],
        )

    def test_the_check_sees_the_file_as_it_was_even_after_the_plan_has_run(
        self,
    ) -> None:
        # After the yes, the step begins again and the plan is checked once
        # more. The file is longer now, but the check is still given the
        # file as it was when the plan was made, so nothing changes.
        provider = ScriptedModelProvider(
            [picks(writes("note.txt", "one\ntwo\nthree\nfour\nfive\n")), DONE]
        )
        with self.assertRaises(ApprovalPending) as asked:
            self.attempt(provider, [])
        first = self.attempt(provider, [self.yes(asked.exception)])
        again = self.attempt(provider, [self.yes(asked.exception)])
        self.assertEqual((first, again), ({"text": "Done."}, {"text": "Done."}))

    def test_a_new_file_is_given_as_not_there(self) -> None:
        provider = ScriptedModelProvider([picks(writes("fresh.txt", "a\n")), DONE])
        with self.assertRaises(ApprovalPending) as asked:
            self.attempt(provider, [])
        self.assertNotIn("warnings", asked.exception.request["arguments"])


class DeclarationTests(unittest.TestCase):
    def load(self, source: str) -> Any:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "main.kos"
            path.write_text(source, encoding="utf-8")
            return load_program(path)

    def refused(self, source: str) -> str:
        with self.assertRaises(CheckError) as caught:
            self.load(source)
        return str(caught.exception)

    def test_the_check_is_kept_on_the_agent(self) -> None:
        self.assertEqual(self.load(NOTES).agents[0].check, "careful")

    def test_it_must_be_a_function_of_the_program(self) -> None:
        self.assertIn(
            "check 'missing' is not a function declared in the program",
            self.refused(NOTES.replace("check: careful", "check: missing")),
        )

    def test_it_must_take_the_request_and_the_files_and_give_sentences(self) -> None:
        for change in (
            (
                "fn careful(request: Text, files: Planned[]) -> Text[]",
                "fn careful(files: Planned[]) -> Text[]",
            ),
            (
                "record Planned { path: Text text: Text before: Text exists: Bool }",
                "record Planned { path: Text text: Text }",
            ),
        ):
            with self.subTest(change=change[1]):
                broken = NOTES.replace(*change)
                if "files: Planned[]) -> Text[]" in change[1]:
                    broken = broken.replace(
                        'whatever \\"" + request + "\\" says', "ever"
                    )
                    broken = broken.replace("file.exists and ", "")
                else:
                    broken = broken.replace(
                        "file.exists and file.text.lines().length() < file.before.lines().length()",
                        "file.text.is_empty()",
                    )
                said = self.refused(broken)
                self.assertIn(
                    "must be a function `fn NAME(request: Text, files: R[]) -> Text[]`",
                    said,
                )
                self.assertIn(
                    "{path: Text, text: Text, before: Text, exists: Bool}", said
                )

    def test_an_agent_that_makes_no_plan_may_still_name_a_check(self) -> None:
        # It is never called, and the program still checks: taking a tool
        # away from an agent must give that mistake's own message, not this.
        only_reads = NOTES.replace("can: [read_file, write_file]", "can: [read_file]")
        self.assertEqual(self.load(only_reads).agents[0].check, "careful")


class LinesTests(unittest.TestCase):
    def test_a_text_as_its_lines(self) -> None:
        source = (
            "record Out { values: Text[] count: Number }\n"
            "fn f() -> Out {\n"
            '  return {values: "a\\nb\\r\\nc\\n".lines(), count: "".lines().length()}\n'
            "}\n"
            "workflow main() -> Out { out = f() }\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "main.kos"
            path.write_text(source, encoding="utf-8")
            result = run(load_program(path), "main", None, inputs={})
        self.assertEqual(result, {"values": ["a", "b", "c"], "count": 0})


if __name__ == "__main__":
    unittest.main()
