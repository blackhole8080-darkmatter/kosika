"""`budget`: ceilings a workflow puts on its own run."""

from __future__ import annotations

import json
import unittest
from typing import Any

from kosika.ast import program_payload
from kosika.checker import CheckError, check
from kosika.controls import ExecutionError
from kosika.execution import ExecutionLimits, within_budget
from kosika.mindmap import build_mindmap
from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.parser import ParseError, parse
from kosika.runtime import run

SOURCE = """
record Ticket { text: Text }
record Out { queue: Text brief: Text }

model assistant

fn keyword_label(ticket: Ticket) -> Choice {
  return {label: "other", confidence: 0.1}
}

fn build(label: Choice, brief: Summary) -> Out {
  return {queue: label.label, brief: brief.text}
}

workflow inner(ticket: Ticket) -> Summary {
  budget 50 model calls
  brief = summarize ticket.text
}

workflow main(ticket: Ticket) -> Out {
  budget 2 model calls, 30 seconds
  label = classify ticket.text into ["billing", "other"] retry 2 otherwise keyword_label(ticket)
  brief = call inner(ticket)
  out   = build(label, brief)
}
"""

INPUTS = {"ticket": {"text": "My card was charged twice."}}
LABEL = {"label": "billing", "confidence": 0.9}
BAD = {"label": "refund", "confidence": 0.9}
BRIEF = {"text": "Charged twice."}


def scripted(*answers: Any) -> ScriptedModelProvider:
    return ScriptedModelProvider(
        [ModelResponse(content=json.dumps(answer)) for answer in answers]
    )


def checked(source: str = SOURCE):
    program = parse(source)
    check(program)
    return program


def outcome(*answers: Any, source: str = SOURCE, **options: Any) -> Any:
    return run(
        checked(source),
        "main",
        None,
        inputs=INPUTS,
        model_bindings={"assistant": scripted(*answers)},
        **options,
    )


class BudgetTests(unittest.TestCase):
    def test_a_run_inside_its_budget_completes(self) -> None:
        self.assertEqual(
            outcome(LABEL, BRIEF), {"queue": "billing", "brief": "Charged twice."}
        )

    def test_the_budget_stops_the_run_and_otherwise_does_not_hide_it(self) -> None:
        # One retry uses the second call; the called workflow needs a third.
        with self.assertRaises(ExecutionError) as caught:
            outcome(BAD, LABEL, BRIEF)
        self.assertEqual(caught.exception.code, "budget")
        self.assertIn("model calls budget exceeds 2", str(caught.exception))

    def test_a_called_workflow_runs_under_its_callers_budget(self) -> None:
        # `inner` declares 50, but it is `main` that is run.
        with self.assertRaises(ExecutionError):
            outcome(BAD, BAD, BRIEF)

    def test_the_host_limit_still_applies_when_it_is_stricter(self) -> None:
        with self.assertRaises(ExecutionError) as caught:
            outcome(LABEL, BRIEF, limits=ExecutionLimits(max_model_calls=1))
        self.assertIn("exceeds 1", str(caught.exception))

    def test_a_budget_only_lowers_limits(self) -> None:
        host = ExecutionLimits(max_model_calls=4, deadline_seconds=10)
        tightened = within_budget(
            host, (("model_calls", 9), ("tool_calls", 3), ("seconds", 60))
        )
        self.assertEqual(tightened.max_model_calls, 4)
        self.assertEqual(tightened.max_tool_calls, 3)
        self.assertEqual(tightened.deadline_seconds, 10)
        self.assertEqual(
            within_budget(ExecutionLimits(), (("seconds", 5),)).deadline_seconds, 5
        )


class LanguageTests(unittest.TestCase):
    def test_a_kind_cannot_be_named_twice(self) -> None:
        source = SOURCE.replace(
            "2 model calls, 30 seconds", "2 model calls, 3 model calls"
        )
        with self.assertRaisesRegex(CheckError, "names model calls more than once"):
            checked(source)

    def test_amounts_are_bounded(self) -> None:
        with self.assertRaisesRegex(CheckError, "model calls is out of range, got 0"):
            checked(SOURCE.replace("2 model calls", "0 model calls"))
        with self.assertRaisesRegex(CheckError, "model calls is out of range"):
            checked(SOURCE.replace("2 model calls", "2.5 model calls"))
        with self.assertRaisesRegex(CheckError, "seconds is out of range"):
            checked(SOURCE.replace("30 seconds", "999999 seconds"))

    def test_unknown_units_are_parse_errors(self) -> None:
        with self.assertRaisesRegex(ParseError, "Expected 'seconds'"):
            parse(SOURCE.replace("30 seconds", "30 dollars"))
        with self.assertRaisesRegex(ParseError, "Expected 'amount'"):
            parse(SOURCE.replace("30 seconds", "many seconds"))

    def test_a_step_may_still_be_named_budget(self) -> None:
        source = SOURCE.replace("brief = call inner", "budget = call inner").replace(
            "build(label, brief)", "build(label, brief: budget)"
        )
        self.assertEqual(
            outcome(LABEL, BRIEF, source=source)["brief"], "Charged twice."
        )

    def test_the_mind_map_shows_the_budget(self) -> None:
        nodes = {
            node["label"]: node
            for node in build_mindmap(checked())["nodes"]
            if node["kind"] == "workflow"
        }
        self.assertEqual(
            nodes["main"]["attributes"]["budget"],
            [["model_calls", 2], ["seconds", 30]],
        )

    def test_a_program_without_a_budget_keeps_its_hash_shape(self) -> None:
        plain = SOURCE.replace("  budget 50 model calls\n", "").replace(
            "  budget 2 model calls, 30 seconds\n", ""
        )
        self.assertNotIn("budget", json.dumps(program_payload(parse(plain))))
        self.assertIn('"budget"', json.dumps(program_payload(checked())))


if __name__ == "__main__":
    unittest.main()
