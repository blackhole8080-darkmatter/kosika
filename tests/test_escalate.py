"""`escalate`: a refused answer moves the step to a stronger model."""

from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika.ast import program_payload
from kosika.checker import CheckError
from kosika.cli import _workflow_model_bindings
from kosika.model import ModelProviderError, ModelResponse, ScriptedModelProvider
from kosika.modules import load_program
from kosika.parser import parse
from kosika.runtime import run
from kosika.story import build_story, read_trace, to_text
from kosika.trace_replay import replay_trace

SOURCE = """
record Msg { text: Text }
record Said { text: Text }
record Draft { text: Text }

default model fast {
  input: Msg
  output: Draft
}

model strong {
  input: Msg
  output: Draft
}

fn short_enough(reply: Draft) -> Text {
  if reply.text.length() > 12 { return "Too long." }
  return ""
}

fn gave_up(msg: Msg) -> Draft {
  return {text: "(none)"}
}

fn say(reply: Draft) -> Said {
  return {text: reply.text}
}

workflow main(msg: Msg) -> Said {
  reply = use_model fast(msg.text) verify short_enough(reply) escalate strong otherwise gave_up(msg)
  said  = say(reply)
}
"""


def says(*texts: str) -> ScriptedModelProvider:
    return ScriptedModelProvider(
        [ModelResponse(content=json.dumps({"text": text})) for text in texts]
    )


class EscalateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.folder = Path(self._dir.name)

    def program(self, source: str = SOURCE) -> Any:
        (self.folder / "main.kos").write_text(source, encoding="utf-8")
        return load_program(self.folder / "main.kos")

    def go(self, fast: Any, strong: Any, source: str = SOURCE) -> Any:
        return run(
            self.program(source),
            "main",
            self.folder / "run.jsonl",
            inputs={"msg": {"text": "hi"}},
            model_bindings={"fast": fast, "strong": strong},
        )

    def test_an_accepted_answer_never_reaches_the_strong_model(self) -> None:
        fast, strong = says("Fine."), says("Unused.")
        self.assertEqual(self.go(fast, strong), {"text": "Fine."})
        self.assertEqual((len(fast.requests), len(strong.requests)), (1, 0))

    def test_a_refused_answer_moves_the_same_question_to_the_strong_model(self) -> None:
        fast, strong = says("This is far too long."), says("Short.")
        self.assertEqual(self.go(fast, strong), {"text": "Short."})
        self.assertEqual((len(fast.requests), len(strong.requests)), (1, 1))
        self.assertEqual(
            fast.requests[0].messages[1].content, strong.requests[0].messages[1].content
        )
        returned = [
            event
            for event in read_trace(self.folder / "run.jsonl")
            if event["event"] == "model_return"
        ][0]
        self.assertEqual(returned["escalated"]["to"], "strong")
        self.assertIn("Too long.", returned["escalated"]["because"])
        replay_trace(self.folder / "run.jsonl")
        story = build_story(read_trace(self.folder / "run.jsonl"))
        self.assertIn("moved to model strong", story["steps"][0]["escalated"])
        self.assertIn("moved to model strong", to_text(story))

    def test_the_strong_model_is_held_to_the_same_check(self) -> None:
        fast, strong = says("This is far too long."), says("Also far too long.")
        self.assertEqual(self.go(fast, strong), {"text": "(none)"})
        without = SOURCE.replace(" otherwise gave_up(msg)", "")
        with self.assertRaisesRegex(ModelProviderError, "checked and refused"):
            self.go(says("This is far too long."), says("Also far too long."), without)

    def test_retries_apply_to_each_model_in_turn(self) -> None:
        source = SOURCE.replace("escalate strong", "retry 1 escalate strong")
        fast = says("far too long, one", "far too long, two")
        strong = says("far too long, three", "Short.")
        self.assertEqual(self.go(fast, strong, source), {"text": "Short."})
        self.assertEqual((len(fast.requests), len(strong.requests)), (2, 2))

    def test_a_provider_that_fails_is_not_a_reason_to_escalate(self) -> None:
        strong = says("Short.")
        self.assertEqual(self.go(ScriptedModelProvider([]), strong), {"text": "(none)"})
        self.assertEqual(len(strong.requests), 0)

    def test_the_strong_model_must_be_bound_before_anything_runs(self) -> None:
        fast = says("Fine.")
        with self.assertRaisesRegex(PermissionError, "'strong' has no authorized"):
            run(
                self.program(),
                "main",
                None,
                inputs={"msg": {"text": "hi"}},
                model_bindings={"fast": fast},
            )
        self.assertEqual(len(fast.requests), 0)

    def test_what_can_escalate_and_to_what(self) -> None:
        for change, reason in (
            (("escalate strong", "escalate nobody"), None),
            (("escalate strong", "escalate fast"), "already asks"),
            (
                ("  said  = say(reply)\n", "  said  = say(reply) escalate strong\n"),
                "only a model step",
            ),
        ):
            with (
                self.subTest(change=change[1]),
                self.assertRaises(CheckError) as caught,
            ):
                self.program(SOURCE.replace(*change))
            if reason:
                self.assertIn(reason, str(caught.exception))

    def test_a_program_without_escalate_hashes_as_before(self) -> None:
        plain = parse(SOURCE.replace(" escalate strong", ""))
        self.assertNotIn("escalate", json.dumps(program_payload(plain)))
        self.assertIn("escalate", json.dumps(program_payload(parse(SOURCE))))


class ModelSpecTests(unittest.TestCase):
    def bind(self, spec: str) -> dict[str, Any]:
        args = argparse.Namespace(
            model=spec,
            model_url=None,
            model_binding=None,
            model_fixture=None,
            models=None,
            model_protocol="ollama",
            model_timeout=5.0,
        )
        return _workflow_model_bindings(args, parse(SOURCE))

    def test_one_model_answers_for_every_name(self) -> None:
        bound = self.bind("small")
        self.assertEqual(
            {name: p.model for name, p in bound.items()},
            {"fast": "small", "strong": "small"},
        )
        self.assertIs(bound["fast"], bound["strong"])

    def test_a_name_can_be_given_a_model_of_its_own(self) -> None:
        bound = self.bind("small, strong=big")
        self.assertEqual(
            {name: p.model for name, p in bound.items()},
            {"fast": "small", "strong": "big"},
        )

    def test_a_spec_that_names_nothing_real_is_refused(self) -> None:
        for bad in ("small,nobody=big", "small,strong", "small,=big", "strong=big"):
            with self.subTest(spec=bad), self.assertRaises(ValueError):
                self.bind(bad)


if __name__ == "__main__":
    unittest.main()
