"""A field a model leaves out is read as null, where null is allowed."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika.checker import check
from kosika.model import ModelProviderError, ModelResponse, ScriptedModelProvider
from kosika.model_steps import fill_absent
from kosika.parser import parse
from kosika.runtime import replay, run
from kosika.schema import schema_for_record, schema_for_type

SOURCE = """
record Ticket { text: Text }
record Address { city: Text? street: Text }
record Order { customer: Text? order_id: Text? amount: Number? home: Address? }
record Strict { customer: Text amount: Number? }

model assistant

fn keep(order: Order) -> Order {
  return order
}

fn keep_strict(order: Strict) -> Strict {
  return order
}

workflow plain(ticket: Ticket) -> Order {
  order  = extract Order from ticket.text
  result = keep(order)
}

workflow grounded(ticket: Ticket) -> Order {
  order  = extract Order from ticket.text with evidence
  result = keep(order)
}

workflow strict(ticket: Ticket) -> Strict {
  order  = extract Strict from ticket.text
  result = keep_strict(order)
}
"""

INPUTS = {"ticket": {"text": "This is Tomas Reyes. Order ZR-2085 has not shipped."}}
EMPTY = {"customer": None, "order_id": None, "amount": None, "home": None}


def outcome(workflow: str, answer: dict[str, Any], trace: Path | None = None) -> Any:
    program = parse(SOURCE)
    check(program)
    provider = ScriptedModelProvider([ModelResponse(content=json.dumps(answer))])
    return run(
        program, workflow, trace, inputs=INPUTS, model_bindings={"assistant": provider}
    )


class FillTests(unittest.TestCase):
    def test_only_fields_that_allow_null_are_filled(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "a": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "b": {"type": "string"},
            },
        }
        self.assertEqual(fill_absent({}, schema), {"a": None})
        self.assertEqual(fill_absent({"a": "x"}, schema), {"a": "x"})
        self.assertEqual(fill_absent("text", schema), "text")

    def test_a_tree_is_completed_at_every_level(self) -> None:
        # `record Task { title: Text then: Task? }`: the schema refers back to
        # itself, and a link left out deep down is read as null too.
        program = parse("record Task { title: Text then: Task? }")
        check(program)
        schema = schema_for_record(program, "Task")
        self.assertIn("$defs", schema)
        answer = {"title": "a", "then": {"title": "b", "then": {"title": "c"}}}
        self.assertEqual(
            fill_absent(answer, schema),
            {"title": "a", "then": {"title": "b", "then": {"title": "c", "then": None}}},
        )

    def test_a_choice_is_completed_by_the_alternative_it_is(self) -> None:
        # Only the fields of the alternative the value names are filled: a
        # `wait` is never given the `note` of a `go`.
        program = parse(
            "choice Move { go(note: Text?) wait(until: Number, why: Text?) }"
        )
        check(program)
        schema = schema_for_type(program, "Move")
        self.assertEqual(
            fill_absent({"$case": "wait", "until": 3}, schema),
            {"$case": "wait", "until": 3, "why": None},
        )
        self.assertEqual(fill_absent({"$case": "go"}, schema), {"$case": "go", "note": None})


class RunTests(unittest.TestCase):
    def test_left_out_optional_fields_become_null(self) -> None:
        self.assertEqual(
            outcome("plain", {"customer": "Tomas Reyes"}),
            {**EMPTY, "customer": "Tomas Reyes"},
        )
        self.assertEqual(outcome("plain", {}), EMPTY)

    def test_a_nested_record_is_completed_too(self) -> None:
        result = outcome("plain", {"home": {"street": "Main St"}})
        self.assertEqual(result["home"], {"city": None, "street": "Main St"})

    def test_a_field_that_must_hold_a_value_is_still_required(self) -> None:
        with self.assertRaisesRegex(
            ModelProviderError, "missing required property 'customer'"
        ):
            outcome("strict", {"amount": 3})
        with self.assertRaisesRegex(ModelProviderError, "'street'"):
            outcome("plain", {"home": {"city": "Oslo"}})
        self.assertEqual(
            outcome("strict", {"customer": "A"}), {"customer": "A", "amount": None}
        )

    def test_a_wrong_value_is_not_forgiven(self) -> None:
        with self.assertRaises(ModelProviderError):
            outcome("plain", {"amount": "thirty"})

    def test_evidence_may_leave_out_what_it_has_no_quote_for(self) -> None:
        answer = {
            "customer": "Tomas Reyes",
            "order_id": "ZR-2085",
            "evidence": {
                "customer": "This is Tomas Reyes",
                "order_id": "Order ZR-2085",
            },
        }
        self.assertEqual(
            outcome("grounded", answer),
            {**EMPTY, "customer": "Tomas Reyes", "order_id": "ZR-2085"},
        )

    def test_a_filled_field_still_needs_its_quote(self) -> None:
        answer = {"customer": "Tomas Reyes", "evidence": {}}
        with self.assertRaisesRegex(Exception, "Field 'customer' has no quote"):
            outcome("grounded", answer)

    def test_a_completed_answer_replays(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for workflow, answer in (
                ("plain", {"customer": "Tomas Reyes"}),
                (
                    "grounded",
                    {
                        "customer": "Tomas Reyes",
                        "evidence": {"customer": "This is Tomas Reyes"},
                    },
                ),
            ):
                with self.subTest(workflow=workflow):
                    trace = Path(directory) / f"{workflow}.jsonl"
                    result = outcome(workflow, answer, trace)
                    self.assertEqual(replay(trace), result)


if __name__ == "__main__":
    unittest.main()
