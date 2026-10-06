"""`vote N`: ask several times and accept only what more than half agree on."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika.ast import program_payload
from kosika.checker import CheckError, check
from kosika.controls import ExecutionError
from kosika.execution import ExecutionLimits
from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.parser import parse
from kosika.runtime import replay, run
from kosika.thinking import ThinkingAnswerError

SOURCE = """
record Ticket { text: Text }
record Out { queue: Text sure: Number }

model assistant

fn keyword_label(ticket: Ticket) -> Choice {
  return {label: "other", confidence: 0.1}
}

fn build(label: Choice) -> Out {
  return {queue: label.label, sure: label.confidence}
}

workflow main(ticket: Ticket) -> Out {
  label = classify ticket.text into ["billing", "bug", "other"] vote 3 otherwise keyword_label(ticket)
  out   = build(label)
}
"""

INPUTS = {"ticket": {"text": "My card was charged twice."}}


def label(name: str, confidence: float = 0.9) -> dict[str, Any]:
    return {"label": name, "confidence": confidence}


def scripted(*answers: Any) -> ScriptedModelProvider:
    return ScriptedModelProvider(
        [ModelResponse(content=json.dumps(answer)) for answer in answers]
    )


def checked(source: str = SOURCE):
    program = parse(source)
    check(program)
    return program


def outcome(
    provider: ScriptedModelProvider, source: str = SOURCE, **options: Any
) -> Any:
    return run(
        checked(source),
        "main",
        options.pop("trace", None),
        inputs=INPUTS,
        model_bindings={"assistant": provider},
        **options,
    )


NO_FALLBACK = SOURCE.replace(" otherwise keyword_label(ticket)", "")


class VoteTests(unittest.TestCase):
    def test_the_majority_wins_and_every_ballot_is_asked(self) -> None:
        provider = scripted(label("bug", 0.4), label("billing", 0.8), label("billing"))
        self.assertEqual(outcome(provider), {"queue": "billing", "sure": 0.8})
        self.assertEqual(len(provider.requests), 3)

    def test_agreement_is_on_the_label_not_the_confidence(self) -> None:
        provider = scripted(label("bug", 0.1), label("bug", 0.2), label("bug", 0.3))
        self.assertEqual(outcome(provider)["sure"], 0.1)

    def test_a_three_way_split_fails_the_step(self) -> None:
        split = (label("bug"), label("billing"), label("other"))
        with self.assertRaisesRegex(ThinkingAnswerError, "did not agree: of 3 asked"):
            outcome(scripted(*split), NO_FALLBACK)
        self.assertEqual(outcome(scripted(*split))["queue"], "other")

    def test_a_rejected_ballot_is_a_vote_for_nothing(self) -> None:
        bad = label("refund")
        self.assertEqual(
            outcome(scripted(bad, label("bug"), label("bug")), NO_FALLBACK)["queue"],
            "bug",
        )
        with self.assertRaisesRegex(ThinkingAnswerError, "1 for .*2 rejected"):
            outcome(scripted(bad, bad, label("bug")), NO_FALLBACK)

    def test_each_ballot_can_retry(self) -> None:
        source = NO_FALLBACK.replace("vote 3", "vote 3 retry 1")
        provider = scripted(
            label("refund"), label("bug"), label("bug"), label("billing")
        )
        self.assertEqual(outcome(provider, source)["queue"], "bug")
        self.assertEqual(len(provider.requests), 4)

    def test_every_ballot_counts_against_the_model_call_limit(self) -> None:
        with self.assertRaises(ExecutionError):
            outcome(
                scripted(label("bug"), label("bug"), label("bug")),
                limits=ExecutionLimits(max_model_calls=2),
            )

    def test_decide_votes_on_the_answer_and_extract_on_the_record(self) -> None:
        source = """
record Ticket { text: Text }
record Order { id: Text }
record Out { refund: Bool id: Text }
model assistant
fn build(refund: Decision, order: Order) -> Out {
  return {refund: refund.answer, id: order.id}
}
workflow main(ticket: Ticket) -> Out {
  refund = decide "Refund?" given ticket.text vote 3
  order  = extract Order from ticket.text vote 3
  out    = build(refund, order)
}
"""

        def decision(answer: bool, reason: str) -> dict[str, Any]:
            return {"answer": answer, "confidence": 0.5, "reason": reason}

        provider = scripted(
            decision(True, "a"),
            decision(False, "b"),
            decision(True, "c"),
            {"id": "A1"},
            {"id": "B2"},
            {"id": "A1"},
        )
        self.assertEqual(outcome(provider, source), {"refund": True, "id": "A1"})

    def test_the_trace_keeps_the_tally_and_replays(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            outcome(scripted(label("bug"), label("billing"), label("bug")), trace=trace)
            self.assertEqual(replay(trace)["queue"], "bug")
            events = [
                json.loads(line) for line in trace.read_text("utf-8").splitlines()
            ]
            returned = next(e for e in events if e.get("event") == "model_return")
            self.assertEqual(returned["votes"]["asked"], 3)
            self.assertEqual(returned["votes"]["agreed"], 2)
            self.assertEqual(returned["votes"]["tally"], {'"billing"': 1, '"bug"': 2})


class LanguageTests(unittest.TestCase):
    def test_only_a_model_step_can_vote(self) -> None:
        with self.assertRaisesRegex(CheckError, "only a model step"):
            checked(
                SOURCE.replace("out   = build(label)", "out   = build(label) vote 3")
            )

    def test_a_summary_cannot_be_voted_on(self) -> None:
        source = SOURCE.replace(
            "  out   = build(label)",
            "  brief = summarize ticket.text vote 3\n  out   = build(label)",
        )
        with self.assertRaisesRegex(CheckError, "cannot agree"):
            checked(source)

    def test_the_count_must_be_odd_and_small(self) -> None:
        for count in ("1", "2", "4", "11"):
            with self.subTest(count=count):
                with self.assertRaisesRegex(CheckError, "3, 5, 7, or 9"):
                    checked(SOURCE.replace("vote 3", f"vote {count}"))

    def test_a_program_without_a_vote_keeps_its_hash_shape(self) -> None:
        plain = parse(SOURCE.replace(" vote 3", ""))
        self.assertNotIn("votes", json.dumps(program_payload(plain)))
        self.assertIn('"votes": 3', json.dumps(program_payload(checked())))


if __name__ == "__main__":
    unittest.main()
