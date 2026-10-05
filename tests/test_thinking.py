"""Thinking steps: classify, extract, summarize, and decide."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from kosika import cli
from kosika.ast import ModelCall, ThinkCall, program_payload
from kosika.checker import CheckError, check
from kosika.development import check_project
from kosika.lexer import lex
from kosika.model import (
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    ScriptedModelProvider,
)
from kosika.modules import load_program
from kosika.parser import ParseError, Parser, parse
from kosika.runtime import replay, run
from kosika.thinking import BUILT_IN_RECORDS, ThinkingAnswerError, lower_thinking

SOURCE = """
record Ticket { text: Text }
record Invoice { customer: Text amount: Number }
record Routing { queue: Text escalate: Bool brief: Text customer: Text why: Text }

model assistant

fn route(label: Choice, refund: Decision, brief: Summary, invoice: Invoice) -> Routing {
  return {
    queue: label.label,
    escalate: refund.answer and label.confidence < 0.9,
    brief: brief.text,
    customer: invoice.customer,
    why: refund.reason
  }
}

workflow main(ticket: Ticket) -> Routing {
  label   = classify ticket.text into ["billing", "bug", "other"]
  invoice = extract Invoice from ticket.text
  brief   = summarize ticket.text
  refund  = decide "Is this refund within policy?" given ticket.text
  routing = route(label, refund, brief, invoice)
}
"""

INPUTS = {"ticket": {"text": "I was charged twice. Ada, 30 dollars."}}
ANSWERS: list[dict[str, Any]] = [
    {"label": "billing", "confidence": 0.8},
    {"customer": "Ada", "amount": 30},
    {"text": "Charged twice."},
    {"answer": True, "confidence": 0.7, "reason": "The text says charged twice."},
]
EXPECTED = {
    "queue": "billing",
    "escalate": True,
    "brief": "Charged twice.",
    "customer": "Ada",
    "why": "The text says charged twice.",
}


def scripted(*answers: dict[str, Any]) -> ScriptedModelProvider:
    return ScriptedModelProvider(
        [ModelResponse(content=json.dumps(answer)) for answer in answers]
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


class LoweringTests(unittest.TestCase):
    def test_each_step_becomes_a_typed_model_call(self) -> None:
        program = checked()
        steps = program.workflows[0].steps
        self.assertTrue(all(isinstance(step.call, ModelCall) for step in steps[:4]))
        self.assertEqual(
            [step.call.model_name for step in steps[:4]],
            [
                "assistant/classify",
                "assistant/extract/Invoice",
                "assistant/summarize",
                "assistant/decide",
            ],
        )
        generated = {model.name: model for model in program.models if model.provider}
        self.assertEqual(
            {model.provider for model in generated.values()}, {"assistant"}
        )
        self.assertEqual(generated["assistant/classify"].output, "Choice")
        self.assertEqual(generated["assistant/extract/Invoice"].output, "Invoice")
        self.assertTrue(all(model.instruction for model in generated.values()))

    def test_built_in_records_are_added_only_when_used(self) -> None:
        names = {record.name for record in checked().records}
        self.assertTrue({"Choice", "Decision", "Summary", "Prompt", "Reply"} <= names)
        plain = checked(
            "record R { n: Number }\nfn f() -> R { return {n: 1} }\n"
            "workflow main() -> R { r = f() }\n"
        )
        self.assertFalse(
            {record.name for record in plain.records} & set(BUILT_IN_RECORDS)
        )

    def test_the_same_verb_and_model_share_one_generated_model(self) -> None:
        source = SOURCE.replace(
            "  routing = route",
            "  again = summarize brief.text\n  routing = route",
        )
        names = [model.name for model in checked(source).models]
        self.assertEqual(names.count("assistant/summarize"), 1)

    def test_a_program_without_thinking_keeps_its_tree_and_hash(self) -> None:
        source = (
            "record Q { text: Text }\nrecord A { text: Text }\n"
            "model m { input: Q output: A }\n"
            "workflow main(q: Q) -> A { a = use_model m(q.text) }\n"
        )
        unlowered = Parser(lex(source)).parse()
        self.assertEqual(lower_thinking(unlowered), unlowered)
        payload = json.dumps(program_payload(parse(source)))
        for field in (
            "provider",
            "instruction",
            "verb",
            '"order"',
            '"condition": null',
        ):
            self.assertNotIn(field, payload)

    def test_the_checker_refuses_an_unlowered_step(self) -> None:
        program = Parser(lex(SOURCE)).parse()
        self.assertIsInstance(program.workflows[0].steps[0].call, ThinkCall)
        with self.assertRaisesRegex(CheckError, "Unlowered thinking steps"):
            check(program)

    def test_a_function_may_still_be_named_like_a_verb(self) -> None:
        source = (
            "record R { n: Number }\n"
            "fn classify(n: Number) -> R { return {n: n + 1} }\n"
            "workflow main() -> R { r = classify(n: 1) }\n"
        )
        self.assertEqual(run(checked(source), "main", None, inputs={}), {"n": 2})


class RunTests(unittest.TestCase):
    def test_four_verbs_run_trace_and_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            result = run(
                checked(),
                "main",
                trace,
                inputs=INPUTS,
                model_bindings={"assistant": scripted(*ANSWERS)},
            )
            self.assertEqual(result, EXPECTED)
            self.assertEqual(replay(trace), EXPECTED)

    def test_the_model_is_told_the_task_and_the_schema(self) -> None:
        seen: list[ModelRequest] = []

        class Recorder:
            def __init__(self) -> None:
                self.inner = scripted(*ANSWERS)

            def complete(self, request: ModelRequest) -> ModelResponse:
                seen.append(request)
                return self.inner.complete(request)

        run(
            checked(),
            "main",
            None,
            inputs=INPUTS,
            model_bindings={"assistant": Recorder()},
        )
        self.assertEqual(len(seen), 4)
        self.assertIn("Task: Classify the text", seen[0].messages[0].content)
        self.assertEqual(
            json.loads(seen[0].messages[1].content),
            {"text": INPUTS["ticket"]["text"], "choices": ["billing", "bug", "other"]},
        )
        self.assertIn('"amount"', seen[1].messages[0].content)
        self.assertEqual(
            json.loads(seen[3].messages[1].content)["question"],
            "Is this refund within policy?",
        )

    def test_a_declaration_grants_no_model(self) -> None:
        with self.assertRaisesRegex(
            PermissionError, "Model 'assistant' has no authorized"
        ):
            run(checked(), "main", None, inputs=INPUTS)

    def test_answers_that_break_a_guarantee_fail_the_run(self) -> None:
        cases: dict[str, tuple[dict[str, Any], type[Exception]]] = {
            "not one of the choices": (
                {"label": "refunds", "confidence": 0.8},
                ThinkingAnswerError,
            ),
            "not from 0 through 1": (
                {"label": "bug", "confidence": 7},
                ThinkingAnswerError,
            ),
            "missing required property": ({"label": "bug"}, ModelProviderError),
        }
        for message, (answer, error) in cases.items():
            with self.subTest(message=message):
                with self.assertRaisesRegex(error, message):
                    run(
                        checked(),
                        "main",
                        None,
                        inputs=INPUTS,
                        model_bindings={"assistant": scripted(answer)},
                    )

    def test_a_decision_confidence_is_bounded_too(self) -> None:
        answers = [*ANSWERS[:3], {"answer": True, "confidence": -1, "reason": "x"}]
        with self.assertRaisesRegex(ThinkingAnswerError, "decide answered confidence"):
            run(
                checked(),
                "main",
                None,
                inputs=INPUTS,
                model_bindings={"assistant": scripted(*answers)},
            )

    def test_using_picks_between_several_models(self) -> None:
        source = (
            SOURCE.replace("model assistant\n", "model fast\nmodel careful\n")
            .replace('"other"]', '"other"] using fast')
            .replace("from ticket.text", "from ticket.text using careful")
            .replace("summarize ticket.text", "summarize ticket.text using fast")
            .replace("given ticket.text", "given ticket.text using careful")
        )
        result = run(
            checked(source),
            "main",
            None,
            inputs=INPUTS,
            model_bindings={
                "fast": scripted(ANSWERS[0], ANSWERS[2]),
                "careful": scripted(ANSWERS[1], ANSWERS[3]),
            },
        )
        self.assertEqual(result, EXPECTED)

    def test_a_general_model_also_answers_plain_text(self) -> None:
        source = (
            "model helper\n"
            "workflow main(prompt: Prompt) -> Reply { reply = use_model helper(prompt.text) }\n"
        )
        result = run(
            checked(source),
            "main",
            None,
            inputs={"prompt": {"text": "hi"}},
            model_bindings={"helper": scripted({"text": "hello"})},
        )
        self.assertEqual(result, {"text": "hello"})

    def test_a_general_model_keeps_its_deployment_and_parameters(self) -> None:
        source = SOURCE.replace(
            "model assistant\n",
            'model assistant {\n  deployment: "local"\n  parameters: { temperature: 0.1 }\n}\n',
        )
        generated = [model for model in checked(source).models if model.provider]
        self.assertTrue(all(model.deployment == "local" for model in generated))
        self.assertTrue(all(model.parameters.temperature == 0.1 for model in generated))


class RejectionTests(unittest.TestCase):
    def test_programs_that_cannot_be_lowered_say_why(self) -> None:
        cases = {
            "declare one with `model assistant`": SOURCE.replace(
                "model assistant\n", ""
            ),
            "add `using NAME`": SOURCE.replace(
                "model assistant\n", "model a\nmodel b\n"
            ),
            "uses unknown model 'nobody'": SOURCE.replace(
                '"other"]', '"other"] using nobody'
            ),
            "expects 'Text[]'": SOURCE.replace(
                'into ["billing", "bug", "other"]', 'into "billing"'
            ),
            "is built in as {label: Text, confidence: Number}": SOURCE.replace(
                "record Ticket", "record Choice { name: Text }\nrecord Ticket"
            ),
            "Did you mean 'Invoice'?": SOURCE.replace(
                "extract Invoice from", "extract Invoce from"
            ),
            "Expected 'into'": SOURCE.replace(' into ["billing", "bug", "other"]', ""),
        }
        for message, source in cases.items():
            with self.subTest(message=message):
                self.assertIn(message, error_of(source))

    def test_a_matching_user_record_may_reuse_a_built_in_name(self) -> None:
        source = SOURCE.replace(
            "record Ticket", "record Summary { text: Text }\nrecord Ticket"
        )
        names = [record.name for record in checked(source).records]
        self.assertEqual(names.count("Summary"), 1)

    def test_thinking_is_a_step_not_an_expression(self) -> None:
        source = SOURCE.replace(
            "    queue: label.label,", "    queue: (summarize brief.text).text,"
        )
        with self.assertRaises((CheckError, ParseError)):
            checked(source)


class ProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_a_model_in_one_module_serves_steps_in_another(self) -> None:
        (self.root / "brain.kos").write_text(
            "export [assistant]\nmodel assistant\n", encoding="utf-8"
        )
        (self.root / "main.kos").write_text(
            'import "brain.kos"\n' + SOURCE.replace("model assistant\n", ""),
            encoding="utf-8",
        )
        program = load_program(self.root / "main.kos")
        result = run(
            program,
            "main",
            None,
            inputs=INPUTS,
            model_bindings={"assistant": scripted(*ANSWERS)},
        )
        self.assertEqual(result, EXPECTED)

    def test_diagnostics_have_stable_codes(self) -> None:
        path = self.root / "main.kos"
        path.write_text(SOURCE.replace("model assistant\n", ""), encoding="utf-8")
        report = check_project(path)["diagnostics"]
        self.assertEqual(report["codes"], ["check/thinking-model-required"])

    def test_the_cli_runs_with_a_fixture_keyed_by_the_declared_model(self) -> None:
        source, inputs, fixture, trace = (
            self.root / name
            for name in ("main.kos", "input.json", "answers.json", "trace.jsonl")
        )
        source.write_text(SOURCE, encoding="utf-8")
        inputs.write_text(json.dumps(INPUTS), encoding="utf-8")
        fixture.write_text(json.dumps({"assistant": ANSWERS}), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        argv = [
            "kosika",
            "run",
            str(source),
            "--input",
            str(inputs),
            "--model-fixture",
            str(fixture),
            "--trace",
            str(trace),
        ]
        with patch.object(sys, "argv", argv):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main()
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(replay(trace), EXPECTED)

    def test_the_model_flag_grants_only_declared_models(self) -> None:
        program = checked()
        args = type(
            "Args",
            (),
            {
                "model_fixture": None,
                "model_binding": None,
                "model": "local",
                "model_url": "http://x/v1",
                "model_protocol": "openai",
            },
        )()
        with contextlib.redirect_stderr(io.StringIO()):
            bindings = cli._workflow_model_bindings(args, program)
        self.assertEqual(list(bindings), ["assistant"])


if __name__ == "__main__":
    unittest.main()
