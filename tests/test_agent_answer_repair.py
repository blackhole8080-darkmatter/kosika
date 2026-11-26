"""A malformed final answer is shown back to the model once, and a run that
fails still leaves a trace of how far it got."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika.agent import AgentLoop, AgentLoopError
from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.parser import parse

SOURCE = """
record Q { text: Text }
record A { text: Text }

model m { input: Q output: A }

agent a {
  model: m
  max_steps: 4
}
"""

PROSE = "I would write that file for you if I could."
ANSWER = '{"text": "ok"}'


def loop(responses: list[str], **kwargs: Any) -> AgentLoop:
    provider = ScriptedModelProvider(
        [ModelResponse(content=text) for text in responses]
    )
    return AgentLoop(
        parse(SOURCE),
        provider,
        lambda declaration, arguments: {},
        **kwargs,
    )


def events(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class AnswerRepairTests(unittest.TestCase):
    def test_prose_is_shown_back_and_the_run_recovers(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            trace = Path(folder) / "trace.jsonl"
            result = loop([PROSE, ANSWER]).run(
                "a", "do the thing", input_value={"text": "hi"}, trace_path=trace
            )
            self.assertEqual(result.structured_output, {"text": "ok"})
            self.assertEqual(result.model_steps, 2)
            seen = [event["event"] for event in events(trace)]
            self.assertIn("agent_answer_rejected", seen)
            self.assertNotIn("agent_run_failed", seen)

    def test_a_second_malformed_answer_ends_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            trace = Path(folder) / "trace.jsonl"
            with self.assertRaises(AgentLoopError) as caught:
                loop([PROSE, PROSE]).run(
                    "a", "do the thing", input_value={"text": "hi"}, trace_path=trace
                )
            self.assertIn("Invalid structured agent output", str(caught.exception))
            # The failed run is still readable afterwards.
            seen = events(trace)
            self.assertEqual(seen[0]["event"], "agent_run_start")
            self.assertEqual(seen[-1]["event"], "agent_run_failed")
            self.assertIn("Invalid structured agent output", seen[-1]["error"])
            self.assertEqual(seen[-1]["agent"], "a")

    def test_no_repair_is_asked_for_when_it_is_turned_off(self) -> None:
        with self.assertRaises(AgentLoopError):
            loop([PROSE], answer_retries=0).run(
                "a", "do the thing", input_value={"text": "hi"}
            )

    def test_a_last_step_is_not_spent_on_a_repair(self) -> None:
        # max_steps: 1 leaves no turn to try again in, so the first bad answer
        # ends the run rather than the loop running out of steps.
        with self.assertRaises(AgentLoopError) as caught:
            loop([PROSE], max_steps=1).run(
                "a", "do the thing", input_value={"text": "hi"}
            )
        self.assertIn("Invalid structured agent output", str(caught.exception))

    def test_the_number_of_repairs_is_bounded_and_whole(self) -> None:
        for bad in (-1, 6, 1.5, True):
            with self.assertRaises(ValueError, msg=repr(bad)):
                loop([ANSWER], answer_retries=bad)  # type: ignore[arg-type]

    def test_a_plain_text_agent_is_not_asked_to_repair(self) -> None:
        # No declared model means no output shape, so there is nothing to get
        # wrong and nothing to show back.
        provider = ScriptedModelProvider([ModelResponse(content="anything at all")])
        plain = AgentLoop(
            parse("agent a { max_steps: 2 }"),
            provider,
            lambda declaration, arguments: {},
        )
        result = plain.run("a", "say something")
        self.assertEqual(result.content, "anything at all")
        self.assertEqual(result.model_steps, 1)


if __name__ == "__main__":
    unittest.main()
