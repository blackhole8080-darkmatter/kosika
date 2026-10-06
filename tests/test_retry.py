"""`retry N`: a rejected answer is shown back to the model with the reason."""

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
from kosika.parser import ParseError, parse
from kosika.runtime import replay, run
from kosika.thinking import ThinkingAnswerError

SOURCE = """
record Ticket { text: Text }
record Out { queue: Text }

model assistant

fn keyword_label(ticket: Ticket) -> Choice {
  return {label: "other", confidence: 0.1}
}

fn build(label: Choice) -> Out {
  return {queue: label.label}
}

workflow main(ticket: Ticket) -> Out {
  label = classify ticket.text into ["billing", "other"] with evidence retry 2 otherwise keyword_label(ticket)
  out   = build(label)
}
"""

INPUTS = {"ticket": {"text": "My card was charged twice."}}
GOOD = {"label": "billing", "confidence": 0.9, "evidence": ["charged twice"]}
OFF_LIST = {"label": "refund", "confidence": 0.9, "evidence": ["charged twice"]}
UNBACKED = {"label": "billing", "confidence": 0.9, "evidence": ["billed two times"]}


def scripted(*answers: Any) -> ScriptedModelProvider:
    return ScriptedModelProvider(
        [
            ModelResponse(content=a if isinstance(a, str) else json.dumps(a))
            for a in answers
        ]
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


class RetryTests(unittest.TestCase):
    def test_a_good_first_answer_is_asked_once(self) -> None:
        provider = scripted(GOOD)
        self.assertEqual(outcome(provider), {"queue": "billing"})
        self.assertEqual(len(provider.requests), 1)

    def test_the_model_sees_its_answer_and_the_reason(self) -> None:
        provider = scripted(OFF_LIST, "not json at all", GOOD)
        self.assertEqual(outcome(provider), {"queue": "billing"})
        self.assertEqual(len(provider.requests), 3)
        last = provider.requests[2].messages
        self.assertEqual([m.role for m in last[2:]], ["assistant", "user"] * 2)
        self.assertEqual(json.loads(last[2].content), OFF_LIST)
        self.assertIn("not one of the choices", last[3].content)
        self.assertEqual(last[4].content, "not json at all")
        self.assertIn("rejected", last[5].content)

    def test_an_unbacked_quote_is_retried_too(self) -> None:
        provider = scripted(UNBACKED, GOOD)
        self.assertEqual(outcome(provider), {"queue": "billing"})
        self.assertIn("does not appear", provider.requests[1].messages[3].content)

    def test_when_retries_run_out_the_fallback_runs(self) -> None:
        provider = scripted(OFF_LIST, OFF_LIST, OFF_LIST)
        self.assertEqual(outcome(provider), {"queue": "other"})
        self.assertEqual(len(provider.requests), 3)

    def test_without_a_fallback_the_last_rejection_stops_the_run(self) -> None:
        source = SOURCE.replace(" otherwise keyword_label(ticket)", "")
        with self.assertRaisesRegex(ThinkingAnswerError, "not one of the choices"):
            outcome(scripted(OFF_LIST, OFF_LIST, OFF_LIST), source)

    def test_every_attempt_counts_against_the_model_call_limit(self) -> None:
        with self.assertRaises(ExecutionError):
            outcome(
                scripted(OFF_LIST, OFF_LIST, GOOD),
                limits=ExecutionLimits(max_model_calls=2),
            )

    def test_the_trace_keeps_rejected_answers_and_replays(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            outcome(scripted(OFF_LIST, GOOD), trace=trace)
            self.assertEqual(replay(trace), {"queue": "billing"})
            events = [
                json.loads(line) for line in trace.read_text("utf-8").splitlines()
            ]
            returned = next(e for e in events if e.get("event") == "model_return")
            self.assertEqual(len(returned["rejected"]), 1)
            self.assertEqual(json.loads(returned["rejected"][0]["content"]), OFF_LIST)
            self.assertIn("not one of", returned["rejected"][0]["reason"])


class LanguageTests(unittest.TestCase):
    def test_only_a_model_step_can_retry(self) -> None:
        source = SOURCE.replace("out   = build(label)", "out   = build(label) retry 2")
        with self.assertRaisesRegex(CheckError, "only a model step"):
            checked(source)

    def test_the_count_is_bounded(self) -> None:
        with self.assertRaisesRegex(CheckError, "from 1 to 5, got 9"):
            checked(SOURCE.replace("retry 2", "retry 9"))
        with self.assertRaisesRegex(ParseError, "Expected 'count'"):
            parse(SOURCE.replace("retry 2", "retry many"))

    def test_the_long_step_form_accepts_retry(self) -> None:
        source = SOURCE.replace(
            '  label = classify ticket.text into ["billing", "other"] with evidence '
            "retry 2 otherwise keyword_label(ticket)\n",
            "  step label {\n"
            '    let label: Choice = classify ticket.text into ["billing", "other"] retry 1\n'
            "    return label\n  }\n",
        )
        plain = {key: value for key, value in GOOD.items() if key != "evidence"}
        self.assertEqual(
            outcome(scripted({**plain, "label": "x"}, plain), source),
            {"queue": "billing"},
        )

    def test_a_program_without_retry_keeps_its_hash_shape(self) -> None:
        plain = parse(SOURCE.replace(" retry 2", ""))
        self.assertNotIn("retries", json.dumps(program_payload(plain)))
        self.assertIn('"retries": 2', json.dumps(program_payload(checked())))


if __name__ == "__main__":
    unittest.main()
