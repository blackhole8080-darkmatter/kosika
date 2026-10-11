"""What an agent changed is written by the runtime, not by the model: an
answer record with a field `did: Text[]` gets the changes that really ran."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika.agent import ApprovalRecord
from kosika.durable import ApprovalPending, ExecutionSession
from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.modules import load_program
from kosika.runtime import run
from kosika.system_tools import bind_system_tools
from kosika.trace_replay import replay_trace
from tests.test_plan_first import TIDY, picks

# The same agent, answering with a record that has `did`.
SOURCE = TIDY.replace(
    "record Finding { answer: Text sources: Text[] }",
    "record Finding { answer: Text did: Text[] }",
).replace(
    "fn tell(found: Finding) -> Said {\n  return {text: found.answer}",
    'fn tell(found: Finding) -> Said {\n  return {text: found.answer + " | " + found.did.join(", ")}',
)


def claims(answer: str, *did: str) -> ModelResponse:
    return ModelResponse(content=json.dumps({"answer": answer, "did": list(did)}))


class DidTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.top = Path(self._dir.name)
        self.source = self.top / "main.kos"
        self.source.write_text(SOURCE, encoding="utf-8")
        self.work = self.top / "work"
        self.work.mkdir()

    def go(self, responses: list[ModelResponse]) -> dict[str, Any]:
        program = load_program(self.source)
        provider = ScriptedModelProvider(responses)
        approvals: list[ApprovalRecord] = []
        for _ in range(3):
            try:
                return run(
                    program,
                    "main",
                    self.top / "run.jsonl",
                    inputs={"msg": {"text": "make files"}},
                    model_bindings={"thinker": provider},
                    tool_bindings=bind_system_tools(program, folder=self.work),
                    session=ExecutionSession(
                        self.top / "run.sqlite", binding_id="t", approvals=approvals
                    ),
                )
            except ApprovalPending as pending:
                approvals.append(
                    ApprovalRecord(
                        plan_hash=pending.request["plan_hash"],
                        action_hash=pending.request["action_hash"],
                        approved_by="tester",
                    )
                )
        self.fail("the run kept asking")

    def test_the_fixture_has_the_field(self) -> None:
        self.assertIn("did: Text[]", SOURCE)
        self.assertIn("found.did.join", SOURCE)

    def test_what_ran_replaces_what_the_model_claimed(self) -> None:
        result = self.go(
            [
                picks(("create_file", {"path": "a.txt", "text": "first"})),
                claims("Made two files.", "a.txt", "b.txt", "c.txt"),
            ]
        )
        self.assertEqual(result, {"text": "Made two files. | create_file a.txt"})
        replay_trace(self.top / "run.jsonl")

    def test_an_agent_that_changed_nothing_is_shown_to_have_done_nothing(self) -> None:
        result = self.go([claims("I created greet.py.", "greet.py")])
        self.assertEqual(result, {"text": "I created greet.py. | "})
        self.assertEqual(list(self.work.iterdir()), [])

    def test_every_change_is_listed_in_the_order_it_ran(self) -> None:
        result = self.go(
            [
                picks(
                    ("create_file", {"path": "a.txt", "text": "first"}),
                    ("create_file", {"path": "notes/b.txt", "text": "second"}),
                ),
                claims("Done."),
            ]
        )
        self.assertEqual(
            result, {"text": "Done. | create_file a.txt, create_file notes/b.txt"}
        )

    def test_a_trace_cannot_claim_a_change_its_calls_do_not_show(self) -> None:
        self.go(
            [
                picks(("create_file", {"path": "a.txt", "text": "first"})),
                claims("Made it."),
            ]
        )
        trace = self.top / "run.jsonl"
        lines = trace.read_text(encoding="utf-8").split("\n")
        changed = [
            line.replace(
                '"create_file a.txt"', '"create_file a.txt", "create_file b.txt"'
            )
            for line in lines
        ]
        self.assertNotEqual(changed, lines)
        trace.write_text("\n".join(changed), encoding="utf-8")
        with self.assertRaises(ValueError):
            replay_trace(trace)

    def test_a_record_without_the_field_is_left_alone(self) -> None:
        self.source.write_text(TIDY, encoding="utf-8")
        program = load_program(self.source)
        provider = ScriptedModelProvider(
            [
                ModelResponse(
                    content=json.dumps({"answer": "Nothing.", "sources": ["x"]})
                )
            ]
        )
        result = run(
            program,
            "main",
            self.top / "run.jsonl",
            inputs={"msg": {"text": "look"}},
            model_bindings={"thinker": provider},
            tool_bindings=bind_system_tools(program, folder=self.work),
            session=ExecutionSession(self.top / "r2.sqlite", binding_id="t"),
        )
        self.assertEqual(result, {"text": "Nothing."})


if __name__ == "__main__":
    unittest.main()
