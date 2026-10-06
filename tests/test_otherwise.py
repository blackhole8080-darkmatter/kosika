"""`step = call otherwise fallback`: recover from a failed step."""

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
from kosika.mindmap import build_mindmap, to_mermaid
from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.modules import load_program
from kosika.parser import ParseError, parse
from kosika.runtime import ExecutionSession, replay, run

SOURCE = """
record Ticket { text: Text }
record Routing { queue: Text sure: Bool }

model assistant

fn keyword_label(ticket: Ticket) -> Choice {
  return {
    label: if ticket.text.lower().contains("charge") { "billing" } else { "other" },
    confidence: 0.3
  }
}

fn route(label: Choice) -> Routing {
  return {queue: label.label, sure: label.confidence > 0.5}
}

workflow main(ticket: Ticket) -> Routing {
  label   = classify ticket.text into ["billing", "bug", "other"] otherwise keyword_label(ticket)
  routing = route(label)
}
"""

TICKET = {"ticket": {"text": "I was charged twice"}}


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


def error_of(source: str) -> str:
    try:
        checked(source)
    except (CheckError, ParseError) as exc:
        return str(exc)
    raise AssertionError("expected the program to be rejected")


class RecoveryTests(unittest.TestCase):
    def run_with(self, *answers: Any, **host: Any) -> Any:
        return run(
            checked(),
            "main",
            host.pop("trace", None),
            inputs=TICKET,
            model_bindings={"assistant": scripted(*answers)},
            **host,
        )

    def test_a_good_answer_never_touches_the_fallback(self) -> None:
        result = self.run_with({"label": "bug", "confidence": 0.9})
        self.assertEqual(result, {"queue": "bug", "sure": True})

    def test_each_kind_of_bad_answer_falls_back(self) -> None:
        cases = {
            "a label that was not offered": {"label": "refunds", "confidence": 0.9},
            "a confidence out of range": {"label": "bug", "confidence": 7},
            "a missing field": {"label": "bug"},
            "text that is not JSON": "I think it is billing",
        }
        for label, answer in cases.items():
            with self.subTest(label):
                self.assertEqual(
                    self.run_with(answer), {"queue": "billing", "sure": False}
                )

    def test_the_trace_records_why_and_still_replays(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            result = self.run_with({"label": "refunds", "confidence": 0.9}, trace=trace)
            events = [
                json.loads(line) for line in trace.read_text("utf-8").splitlines()
            ]
            self.assertEqual(replay(trace), result)
        starts = [event for event in events if event["event"] == "step_start"]
        self.assertEqual([event["step"] for event in starts], ["label", "routing"])
        self.assertEqual(starts[0]["recovered_from"]["error"], "ThinkingAnswerError")
        self.assertIn("refunds", starts[0]["recovered_from"]["message"])
        self.assertNotIn("recovered_from", starts[1])
        kinds = [event["event"] for event in events]
        self.assertNotIn("model_call", kinds, "the failed attempt leaves no half step")
        self.assertEqual(kinds.count("function_call"), 2)

    def test_a_failing_fallback_fails_the_run(self) -> None:
        source = SOURCE.replace(
            'label: if ticket.text.lower().contains("charge") { "billing" } else { "other" },',
            "label: ticket.text.slice(start: 0, end: 999),",
        )
        with self.assertRaisesRegex(RuntimeError, "outside a value"):
            run(
                checked(source),
                "main",
                None,
                inputs=TICKET,
                model_bindings={"assistant": scripted({"label": "x", "confidence": 1})},
            )

    def test_running_out_of_budget_is_not_recovered(self) -> None:
        source = SOURCE.replace(
            "  label   = classify",
            "  brief   = summarize ticket.text\n  label   = classify",
        )
        with self.assertRaises(ExecutionError) as raised:
            run(
                checked(source),
                "main",
                None,
                inputs=TICKET,
                model_bindings={"assistant": scripted({"text": "short"})},
                limits=ExecutionLimits(max_model_calls=1),
            )
        self.assertEqual(raised.exception.code, "budget")

    def test_a_missing_model_binding_is_not_recovered(self) -> None:
        with self.assertRaisesRegex(PermissionError, "no authorized host binding"):
            run(checked(), "main", None, inputs=TICKET)

    def test_a_fallback_that_asks_another_model_needs_its_binding_too(self) -> None:
        source = SOURCE.replace(
            "model assistant\n", "model assistant\nmodel backup\n"
        ).replace(
            '["billing", "bug", "other"] otherwise keyword_label(ticket)',
            '["billing", "bug", "other"] using assistant\n'
            '    otherwise classify ticket.text into ["billing", "other"] using backup',
        )
        program = checked(source)
        with self.assertRaisesRegex(PermissionError, "Model 'backup'"):
            run(
                program,
                "main",
                None,
                inputs=TICKET,
                model_bindings={
                    "assistant": scripted({"label": "bug", "confidence": 1})
                },
            )
        result = run(
            program,
            "main",
            None,
            inputs=TICKET,
            model_bindings={
                "assistant": scripted("nonsense"),
                "backup": scripted({"label": "other", "confidence": 0.8}),
            },
        )
        self.assertEqual(result, {"queue": "other", "sure": True})

    def test_recovery_works_inside_a_durable_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = ExecutionSession(
                Path(directory) / "run.sqlite", binding_id="test"
            )
            result = self.run_with(
                {"label": "refunds", "confidence": 0.9}, session=session
            )
            self.assertEqual(result, {"queue": "billing", "sure": False})
            again = ExecutionSession(Path(directory) / "run.sqlite", binding_id="test")
            resumed = run(
                checked(),
                "main",
                None,
                inputs=TICKET,
                session=again,
                model_bindings={"assistant": scripted()},
            )
            self.assertEqual(resumed, result)

    def test_the_mind_map_draws_the_fallback(self) -> None:
        mindmap = build_mindmap(checked())
        labels = {node["id"]: node["label"] for node in mindmap["nodes"]}
        drawn = {
            (labels[edge["source"]], edge["label"], labels[edge["target"]])
            for edge in mindmap["edges"]
            if labels[edge["source"]] == "1. label"
            and edge["relation"] in ("model_call", "function_call")
        }
        self.assertEqual(
            drawn,
            {
                ("1. label", "asks", "assistant/classify"),
                ("1. label", "otherwise: computes", "keyword_label"),
            },
        )
        self.assertIn("|otherwise: computes|", to_mermaid(mindmap))
        plain = build_mindmap(
            checked(SOURCE.replace(" otherwise keyword_label(ticket)", ""))
        )
        self.assertFalse(
            [edge for edge in plain["edges"] if "otherwise" in edge["label"]]
        )

    def test_the_long_step_form_accepts_otherwise(self) -> None:
        source = SOURCE.replace(
            '  label   = classify ticket.text into ["billing", "bug", "other"] '
            "otherwise keyword_label(ticket)\n",
            "  step label {\n"
            '    let label: Choice = classify ticket.text into ["billing", "bug", "other"]\n'
            "      otherwise keyword_label(ticket)\n"
            "    return label\n  }\n",
        )
        self.assertEqual(
            run(
                checked(source),
                "main",
                None,
                inputs=TICKET,
                model_bindings={"assistant": scripted("bad")},
            ),
            {"queue": "billing", "sure": False},
        )


class CheckingTests(unittest.TestCase):
    def test_the_fallback_must_return_the_same_type(self) -> None:
        source = SOURCE.replace(
            "otherwise keyword_label(ticket)", "otherwise route(label: ticket)"
        )
        message = error_of(source)
        self.assertTrue("otherwise branch returns 'Routing'" in message, message)

    def test_the_fallback_is_checked_like_any_call(self) -> None:
        self.assertIn(
            "unknown function",
            error_of(SOURCE.replace("keyword_label(ticket)", "nope(ticket)")),
        )
        self.assertIn(
            "expects 'Ticket'",
            error_of(
                SOURCE.replace("keyword_label(ticket)", 'keyword_label(ticket: "x")')
            ),
        )

    def test_otherwise_is_refused_around_a_world_changing_tool(self) -> None:
        source = """
record Email { to: Text }
record Receipt { sent: Bool }
tool send_email { source mcp "mail" operation: "send" access: execute input: Email output: Receipt }
agent messenger { can: [send_email] }
fn pretend(email: Email) -> Receipt { return {sent: false} }
workflow main(email: Email) -> Receipt {
  receipt = execute messenger.send_email(email.to) otherwise pretend(email)
  require approval
}
"""
        self.assertIn("a failed action may still have happened", error_of(source))

    def test_a_program_without_otherwise_keeps_its_hash(self) -> None:
        source = SOURCE.replace(" otherwise keyword_label(ticket)", "")
        self.assertNotIn("fallback", json.dumps(program_payload(parse(source))))
        self.assertIn("fallback", json.dumps(program_payload(parse(SOURCE))))

    def test_it_loads_from_a_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "main.kos"
            path.write_text(SOURCE, encoding="utf-8")
            program = load_program(path)
        self.assertIsNotNone(program.workflows[0].steps[0].fallback)


if __name__ == "__main__":
    unittest.main()
