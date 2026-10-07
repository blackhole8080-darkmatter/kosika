"""Boards: agents of one run leave messages for each other, across swarm rounds."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika.approval_ledger import ApprovalLedger
from kosika.ast import program_payload
from kosika.board import MAX_POSTS, BoardStore
from kosika.checker import check
from kosika.mindmap import build_mindmap
from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.parser import ParseError, parse
from kosika.runtime import replay, run
from kosika.story import build_story, read_trace, to_text
from kosika.swarm import SwarmApproval, run_swarm, swarm_plan_hash
from kosika.testing import run_tests

SOURCE = """
record Brief { topic: Text }
record Remark { text: Text }
record Outcome { draft: Text posts: Number heard: Text }

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

fn writer_prompt(brief: Brief, seen: DeskBoard) -> Prompt {
  var last = ""
  for post in seen.posts where post.author == "critic" {
    last = post.body.text
  }
  let advice = if last.length() > 0 { " The critic said: " + last } else { "" }
  return {text: "Write one line about " + brief.topic + "." + advice}
}

fn critic_prompt(draft: Reply) -> Prompt {
  return {text: "Criticise briefly: " + draft.text}
}

fn remark(reply: Reply) -> Remark {
  return {text: reply.text}
}

fn outcome(draft: Reply, seen: DeskBoard, ask: Prompt) -> Outcome {
  return {draft: draft.text, posts: seen.posts.length(), heard: ask.text}
}

workflow refine(brief: Brief) -> Outcome {
  before  = read desk
  ask     = writer_prompt(brief, before)
  draft   = ask writer(ask.text)
  said    = remark(draft)
  posted  = post said to desk as writer
  query   = critic_prompt(draft)
  verdict = ask critic(query.text)
  noted   = remark(verdict)
  replied = post noted to desk as critic
  after   = read desk
  result  = outcome(draft, after, ask)
}

swarm Studio {
  agents: [writer, critic]
  workflow: refine
  max_rounds: 3
  require approval
}
"""

BRIEF = {"brief": {"topic": "tea"}}


def scripted(*texts: str) -> ScriptedModelProvider:
    return ScriptedModelProvider(
        [ModelResponse(content=json.dumps({"text": text})) for text in texts]
    )


def checked(source: str = SOURCE):
    program = parse(source)
    check(program)
    return program


def error_of(source: str) -> str:
    try:
        checked(source)
    except ValueError as exc:
        return str(exc)
    raise AssertionError("expected the program to be rejected")


class SingleRunTests(unittest.TestCase):
    def go(self, trace: Path | None = None) -> Any:
        return run(
            checked(),
            "refine",
            trace,
            inputs=BRIEF,
            model_bindings={
                "writer_model": scripted("Tea is calm."),
                "critic_model": scripted("Too plain."),
            },
        )

    def test_posts_are_read_back_in_order_with_their_authors(self) -> None:
        result = self.go()
        self.assertEqual(result["draft"], "Tea is calm.")
        self.assertEqual(result["posts"], 2)
        self.assertEqual(result["heard"], "Write one line about tea.")

    def test_a_board_needs_no_grant_and_starts_empty_every_run(self) -> None:
        self.assertEqual(self.go()["posts"], 2)
        self.assertEqual(self.go()["posts"], 2)

    def test_the_trace_records_every_post_and_replays(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "t.jsonl"
            result = self.go(trace)
            self.assertEqual(replay(trace), result)
            text = to_text(build_story(read_trace(trace)))
            self.assertIn("writer posted to board desk", text)
            self.assertIn("critic posted to board desk", text)
            self.assertIn("read board desk: 2 post(s)", text)

    def test_the_store_is_bounded(self) -> None:
        store = BoardStore()
        for _ in range(MAX_POSTS):
            store.post("desk", "writer", {"text": "x"})
        with self.assertRaisesRegex(RuntimeError, "is full"):
            store.post("desk", "writer", {"text": "x"})
        self.assertEqual(store.read("other"), {"posts": []})


class SwarmTests(unittest.TestCase):
    def test_a_later_round_hears_what_an_earlier_round_said(self) -> None:
        program = checked()
        writer = scripted(
            "Tea is calm.", "Tea warms a cold day.", "Tea warms a cold day."
        )
        critic = scripted("Too plain.", "Better.", "Better.")
        with tempfile.TemporaryDirectory() as directory:
            outcome = run_swarm(
                program,
                "Studio",
                approval=SwarmApproval(swarm_plan_hash(program, "Studio"), "aryan"),
                ledger=ApprovalLedger(Path(directory) / "ledger.sqlite"),
                inputs=BRIEF,
                model_bindings={"writer_model": writer, "critic_model": critic},
            )
        # Round 2's writer was told what round 1's critic posted.
        second = writer.requests[1].messages[-1].content
        self.assertIn("The critic said: Too plain.", second)
        third = writer.requests[2].messages[-1].content
        self.assertIn("The critic said: Better.", third)
        self.assertEqual(outcome.result["draft"], "Tea warms a cold day.")
        self.assertEqual(outcome.result["posts"], 6)
        self.assertEqual(len(outcome.rounds), 3)


class LanguageTests(unittest.TestCase):
    def test_a_board_is_ordinary_tools_underneath(self) -> None:
        program = checked()
        tools = {tool.name: tool for tool in program.tools}
        self.assertEqual(sorted(tools), ["post_desk", "read_desk"])
        self.assertEqual(tools["post_desk"].source, "board:desk")
        self.assertIsNone(tools["read_desk"].input)
        labels = {node["label"] for node in build_mindmap(program)["nodes"]}
        self.assertIn("post_desk", labels)

    def test_a_post_must_be_signed_by_a_declared_agent(self) -> None:
        message = error_of(SOURCE.replace("to desk as critic", "to desk as stranger"))
        self.assertIn("signed 'stranger', which is not a declared agent", message)

    def test_only_the_declared_record_can_be_posted(self) -> None:
        self.assertIn(
            "body", error_of(SOURCE.replace("post said to desk", "post brief to desk"))
        )

    def test_declaration_mistakes_are_reported(self) -> None:
        self.assertIn(
            "holds unknown record 'Nope'",
            error_of(SOURCE.replace("board desk of Remark", "board desk of Nope")),
        )
        self.assertIn(
            "declared more than once",
            error_of(
                SOURCE.replace(
                    "board desk of Remark", "board desk of Remark\nboard desk of Remark"
                )
            ),
        )
        self.assertIn(
            "post_desks",
            error_of(
                SOURCE.replace(
                    "post said to desk as writer", "post said to desks as writer"
                )
            ),
        )

    def test_malformed_steps_are_parse_errors(self) -> None:
        with self.assertRaisesRegex(ParseError, "Expected 'as'"):
            parse(SOURCE.replace("post said to desk as writer", "post said to desk"))
        with self.assertRaisesRegex(ParseError, "Expected 'to'"):
            parse(
                SOURCE.replace(
                    "post said to desk as writer", "post said desk as writer"
                )
            )

    def test_post_and_read_stay_usable_as_function_names(self) -> None:
        checked(
            SOURCE
            + "\nfn read(n: Number) -> Number {\n  return n\n}\nfn post(n: Number) -> Number {\n  return read(n)\n}\n"
        )

    def test_a_program_without_a_board_keeps_its_hash_shape(self) -> None:
        plain = parse("record A { x: Text }\nfn f(a: A) -> A {\n  return a\n}\n")
        self.assertNotIn("boards", json.dumps(program_payload(plain)))
        self.assertIn('"boards"', json.dumps(program_payload(checked())))

    def test_a_test_in_the_language_can_use_a_board(self) -> None:
        tests = """
test "the critic is heard" {
  answers writer_model: [{text: "Tea is calm."}]
  answers critic_model: [{text: "Too plain."}]
  result = refine({topic: "tea"})
  expect result.posts == 2
}
"""
        results = run_tests(checked(SOURCE + tests))
        self.assertTrue(results[0].passed, results[0].message)


if __name__ == "__main__":
    unittest.main()
