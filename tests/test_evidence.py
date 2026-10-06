"""`with evidence`: an answer must quote the text it was given."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika.ast import program_payload
from kosika.checker import CheckError, check
from kosika.evidence import EvidenceError, normalize, verify
from kosika.model import ModelProviderError, ModelResponse, ScriptedModelProvider
from kosika.parser import ParseError, parse
from kosika.runtime import replay, run
from kosika.thinking import ThinkingAnswerError, ThinkingError

SOURCE = """
record Ticket { text: Text }
record Invoice { customer: Text? amount: Number? }
record Out { customer: Text amount: Number refund: Bool queue: Text }

model assistant

fn keyword_invoice(ticket: Ticket) -> Invoice {
  return {customer: null, amount: null}
}

fn build(invoice: Invoice, refund: Decision, label: Choice) -> Out {
  return {
    customer: invoice.customer.or("unknown"),
    amount: invoice.amount.or(0),
    refund: refund.answer,
    queue: label.label
  }
}

workflow main(ticket: Ticket) -> Out {
  invoice = extract Invoice from ticket.text with evidence otherwise keyword_invoice(ticket)
  refund  = decide "Was the customer charged twice?" given ticket.text with evidence
  label   = classify ticket.text into ["billing", "other"] with evidence
  out     = build(invoice, refund, label)
}
"""

TEXT = "Hello, this is Ada Lovelace.  My card was charged twice, 30 dollars each time."
INPUTS = {"ticket": {"text": TEXT}}

INVOICE = {
    "customer": "Ada Lovelace",
    "amount": 30,
    "evidence": {"customer": "this is Ada Lovelace", "amount": "30 dollars each time"},
}
REFUND = {
    "answer": True,
    "confidence": 0.9,
    "reason": "It says so.",
    "evidence": ["My card was charged twice"],
}
LABEL = {"label": "billing", "confidence": 0.9, "evidence": ["charged twice"]}
EXPECTED = {
    "customer": "Ada Lovelace",
    "amount": 30,
    "refund": True,
    "queue": "billing",
}


def scripted(*answers: dict[str, Any]) -> ScriptedModelProvider:
    return ScriptedModelProvider(
        [ModelResponse(content=json.dumps(answer)) for answer in answers]
    )


def checked(source: str = SOURCE):
    program = parse(source)
    check(program)
    return program


def outcome(*answers: dict[str, Any], trace: Path | None = None) -> Any:
    return run(
        checked(),
        "main",
        trace,
        inputs=INPUTS,
        model_bindings={"assistant": scripted(*answers)},
    )


class VerifyTests(unittest.TestCase):
    def test_matching_forgives_case_spacing_and_quote_marks(self) -> None:
        self.assertEqual(normalize("  It’s   HERE "), "it's here")
        verify("quotes", {"text": TEXT}, {}, ['"my card  was CHARGED twice..."'])

    def test_a_paraphrase_is_not_a_quote(self) -> None:
        with self.assertRaisesRegex(EvidenceError, "does not appear"):
            verify("quotes", {"text": TEXT}, {}, ["the card was billed two times"])

    def test_a_one_letter_quote_is_refused(self) -> None:
        with self.assertRaisesRegex(EvidenceError, "too short"):
            verify("quotes", {"text": TEXT}, {}, ["a"])

    def test_every_filled_field_needs_its_own_quote(self) -> None:
        arguments = {"text": TEXT}
        verify("fields", arguments, {"customer": None, "amount": None}, {})
        with self.assertRaisesRegex(EvidenceError, "Field 'amount' has no quote"):
            verify(
                "fields",
                arguments,
                {"customer": "Ada Lovelace", "amount": 30},
                {"customer": "Ada Lovelace", "amount": None},
            )

    def test_only_a_true_decision_needs_a_quote(self) -> None:
        arguments = {"given": TEXT}
        verify("quotes_if_true", arguments, {"answer": False}, [])
        with self.assertRaisesRegex(EvidenceError, "no quote"):
            verify("quotes_if_true", arguments, {"answer": True}, [])
        with self.assertRaisesRegex(EvidenceError, "no quote"):
            verify("quotes", {"text": TEXT}, {"label": "billing"}, [])


class RunTests(unittest.TestCase):
    def test_backed_answers_run_and_the_value_has_no_evidence_field(self) -> None:
        self.assertEqual(outcome(INVOICE, REFUND, LABEL), EXPECTED)

    def test_the_model_is_asked_for_quotes(self) -> None:
        provider = scripted(INVOICE, REFUND, LABEL)
        run(
            checked(),
            "main",
            None,
            inputs=INPUTS,
            model_bindings={"assistant": provider},
        )
        system = provider.requests[0].messages[0].content
        self.assertIn('"evidence"', system)
        self.assertIn("exact quote", system)

    def test_an_invented_value_fails_over_to_otherwise(self) -> None:
        invented = {
            "customer": "Ada Lovelace",
            "amount": 45,
            "evidence": {
                "customer": "this is Ada Lovelace",
                "amount": "45 dollars in total",
            },
        }
        result = outcome(invented, REFUND, LABEL)
        self.assertEqual(result, {**EXPECTED, "customer": "unknown", "amount": 0})

    def test_an_unbacked_decision_stops_the_run(self) -> None:
        unbacked = {**REFUND, "evidence": ["charged three times"]}
        with self.assertRaisesRegex(ThinkingAnswerError, "does not appear"):
            outcome(INVOICE, unbacked, LABEL)

    def test_an_answer_without_the_evidence_key_is_invalid(self) -> None:
        bare = {key: value for key, value in REFUND.items() if key != "evidence"}
        with self.assertRaisesRegex(ModelProviderError, "evidence"):
            outcome(INVOICE, bare, LABEL)

    def test_the_trace_keeps_the_quotes_and_replay_proves_them_again(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            self.assertEqual(outcome(INVOICE, REFUND, LABEL, trace=trace), EXPECTED)
            self.assertEqual(replay(trace), EXPECTED)
            events = [
                json.loads(line) for line in trace.read_text("utf-8").splitlines()
            ]
            returned = [e for e in events if e.get("event") == "model_return"]
            self.assertEqual(returned[1]["evidence"], ["My card was charged twice"])
            self.assertNotIn("evidence", returned[1]["result"])

            # A trace whose input no longer contains the quote does not replay.
            forged = trace.read_text("utf-8").replace(
                "charged twice, 30", "charged once, 30"
            )
            (Path(directory) / "forged.jsonl").write_text(forged, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "evidence does not hold"):
                replay(Path(directory) / "forged.jsonl")


class LanguageTests(unittest.TestCase):
    def test_summarize_cannot_give_evidence(self) -> None:
        source = SOURCE.replace(
            "  out     = build",
            "  brief   = summarize ticket.text with evidence\n  out     = build",
        )
        with self.assertRaisesRegex(
            (ThinkingError, ParseError, CheckError), "cannot give evidence"
        ):
            checked(source)

    def test_a_record_with_its_own_evidence_field_is_refused(self) -> None:
        source = SOURCE.replace(
            "amount: Number? }", "amount: Number? evidence: Text? }"
        )
        with self.assertRaisesRegex(
            (ThinkingError, ParseError, CheckError), "its own field"
        ):
            checked(source)

    def test_with_must_be_followed_by_evidence(self) -> None:
        with self.assertRaisesRegex(ParseError, "Expected 'evidence'"):
            parse(SOURCE.replace("with evidence otherwise", "with proof otherwise"))

    def test_a_program_without_evidence_keeps_its_hash_shape(self) -> None:
        plain = parse(SOURCE.replace(" with evidence", ""))
        text = json.dumps(program_payload(plain))
        self.assertNotIn("evidence", text)
        self.assertIn('"evidence": "fields"', json.dumps(program_payload(checked())))


if __name__ == "__main__":
    unittest.main()
