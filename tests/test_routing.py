"""Model routing: several models, a default, votes across models, `--models`."""

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
from kosika.ast import program_payload
from kosika.checker import check
from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.parser import ParseError, parse
from kosika.runtime import replay, run
from kosika.thinking import ThinkingAnswerError

SOURCE = """
record Ticket { text: Text }
record Out { queue: Text brief: Text refund: Bool }

model cheap
default model careful
model strict

fn build(label: Choice, brief: Summary, refund: Decision) -> Out {
  return {queue: label.label, brief: brief.text, refund: refund.answer}
}

workflow main(ticket: Ticket) -> Out {
  label  = classify ticket.text into ["billing", "bug"] using cheap
  brief  = summarize ticket.text
  refund = decide "Refund?" given ticket.text using cheap, careful, strict vote 3
  out    = build(label, brief, refund)
}
"""

INPUTS = {"ticket": {"text": "My card was charged twice."}}
LABEL = {"label": "billing", "confidence": 0.9}
BRIEF = {"text": "Charged twice."}


def decision(answer: bool) -> dict[str, Any]:
    return {"answer": answer, "confidence": 0.8, "reason": "r"}


def scripted(*answers: Any) -> ScriptedModelProvider:
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
    except ValueError as exc:
        return str(exc)
    raise AssertionError("expected the program to be rejected")


class RoutingTests(unittest.TestCase):
    def providers(self, strict_says: bool = True) -> dict[str, ScriptedModelProvider]:
        return {
            "cheap": scripted(LABEL, decision(True)),
            "careful": scripted(BRIEF, decision(False)),
            "strict": scripted(decision(strict_says)),
        }

    def test_each_step_asks_the_model_it_names_or_the_default(self) -> None:
        providers = self.providers()
        result = run(checked(), "main", None, inputs=INPUTS, model_bindings=providers)
        self.assertEqual(
            result, {"queue": "billing", "brief": "Charged twice.", "refund": True}
        )
        # cheap: classify and ballot 1; careful: summarize and ballot 2;
        # strict: ballot 3.
        self.assertEqual(
            {name: len(p.requests) for name, p in providers.items()},
            {"cheap": 2, "careful": 2, "strict": 1},
        )

    def test_the_other_models_can_outvote_the_first(self) -> None:
        providers = self.providers(strict_says=False)
        result = run(checked(), "main", None, inputs=INPUTS, model_bindings=providers)
        self.assertFalse(result["refund"])

    def test_models_take_turns_when_there_are_fewer_than_ballots(self) -> None:
        source = SOURCE.replace(
            "using cheap, careful, strict vote 3", "using cheap, strict vote 3"
        )
        providers = {
            "cheap": scripted(LABEL, decision(True), decision(True)),
            "careful": scripted(BRIEF),
            "strict": scripted(decision(False)),
        }
        result = run(
            checked(source), "main", None, inputs=INPUTS, model_bindings=providers
        )
        self.assertTrue(result["refund"])
        self.assertEqual(len(providers["cheap"].requests), 3)

    def test_no_majority_across_models_fails_the_step(self) -> None:
        source = (
            SOURCE.replace(
                'refund = decide "Refund?" given ticket.text using cheap, careful, strict vote 3',
                'pick = classify ticket.text into ["a", "b", "c"] using cheap, careful, strict vote 3',
            )
            .replace("refund: Decision", "pick: Choice")
            .replace("refund: refund.answer", 'refund: pick.label == "a"')
            .replace("build(label, brief, refund)", "build(label, brief, pick)")
        )
        providers = {
            "cheap": scripted(LABEL, {"label": "a", "confidence": 1}),
            "careful": scripted(BRIEF, {"label": "b", "confidence": 1}),
            "strict": scripted({"label": "c", "confidence": 1}),
        }
        with self.assertRaisesRegex(ThinkingAnswerError, "did not agree"):
            run(checked(source), "main", None, inputs=INPUTS, model_bindings=providers)

    def test_a_voting_model_without_a_binding_is_refused_before_any_call(self) -> None:
        providers = self.providers()
        del providers["strict"]
        with self.assertRaisesRegex(PermissionError, "'strict' has no authorized"):
            run(checked(), "main", None, inputs=INPUTS, model_bindings=providers)
        self.assertEqual(len(providers["cheap"].requests), 0)

    def test_the_trace_names_the_model_of_each_ballot_and_replays(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            run(
                checked(), "main", trace, inputs=INPUTS, model_bindings=self.providers()
            )
            self.assertTrue(replay(trace)["refund"])
            events = [
                json.loads(line) for line in trace.read_text("utf-8").splitlines()
            ]
            votes = [e["votes"] for e in events if "votes" in e]
            self.assertEqual(votes[0]["models"], ["cheap", "careful", "strict"])


class LanguageTests(unittest.TestCase):
    def test_without_a_default_several_models_need_using(self) -> None:
        message = error_of(SOURCE.replace("default model careful", "model careful"))
        self.assertIn("mark one `default model NAME`", message)

    def test_two_defaults_are_refused(self) -> None:
        message = error_of(SOURCE.replace("model strict", "default model strict"))
        self.assertIn("marks several models as default", message)

    def test_several_models_need_a_vote(self) -> None:
        message = error_of(SOURCE.replace(" vote 3", ""))
        self.assertIn("only for a step that ends `vote N`", message)

    def test_the_same_model_cannot_be_listed_twice(self) -> None:
        message = error_of(SOURCE.replace("cheap, careful, strict", "cheap, cheap"))
        self.assertIn("lists the same model twice", message)

    def test_an_unknown_voting_model_is_named(self) -> None:
        message = error_of(SOURCE.replace("cheap, careful, strict", "cheap, wise"))
        self.assertIn("unknown model 'wise'", message)

    def test_default_must_be_followed_by_model(self) -> None:
        with self.assertRaises(ParseError):
            parse(SOURCE.replace("default model careful", "default careful"))

    def test_programs_without_routing_keep_their_hash_shape(self) -> None:
        plain = (
            SOURCE.replace("default model careful", "model careful")
            .replace("summarize ticket.text", "summarize ticket.text using careful")
            .replace("using cheap, careful, strict vote 3", "using strict")
        )
        text = json.dumps(program_payload(checked(plain)))
        for word in ('"default"', '"voters"', '"more_models"'):
            self.assertNotIn(word, text)
        routed = json.dumps(program_payload(checked()))
        self.assertIn('"default": true', routed)
        self.assertIn('"voters"', routed)


STANDIN = """
import sys
request = sys.stdin.read()
if "Classify" in request:
    print('{"label": "billing", "confidence": 0.9}')
elif "Summarize" in request:
    print('{"text": "%s"}')
else:
    print('{"answer": %s, "confidence": 0.5, "reason": "r"}')
"""


class ModelsFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        (self.root / "main.kos").write_text(SOURCE, encoding="utf-8")
        (self.root / "input.json").write_text(json.dumps(INPUTS), encoding="utf-8")

    def standin(self, name: str, answer: bool) -> list[str]:
        path = self.root / f"{name}.py"
        path.write_text(
            STANDIN % (name, "true" if answer else "false"), encoding="utf-8"
        )
        return [sys.executable, str(path)]

    def invoke(self, routes: Any) -> tuple[int, str, str]:
        (self.root / "models.json").write_text(json.dumps(routes), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        arguments = [
            "kosika",
            "run",
            str(self.root / "main.kos"),
            "--input",
            str(self.root / "input.json"),
            "--trace",
            str(self.root / "trace.jsonl"),
            "--models",
            str(self.root / "models.json"),
        ]
        with patch.object(sys, "argv", arguments):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main()
        return code, out.getvalue(), err.getvalue()

    def test_each_declared_model_gets_its_own_program(self) -> None:
        code, out, err = self.invoke(
            {
                "cheap": {"command": self.standin("cheap", True)},
                "careful": {"command": self.standin("careful", False)},
                "strict": {"command": self.standin("strict", False)},
            }
        )
        self.assertEqual(code, 0, err)
        self.assertIn("cheap: command", err)
        # The summary came from `careful`, and two of three said no refund.
        self.assertIn("'brief': 'careful'", out)
        self.assertIn("'refund': False", out)

    def test_a_name_the_program_does_not_declare_is_refused(self) -> None:
        code, _, err = self.invoke({"wise": {"model": "m"}})
        self.assertNotEqual(code, 0)
        self.assertIn("does not declare", err)

    def test_a_malformed_route_is_refused(self) -> None:
        for route in ({"command": "gemini"}, {"model": ""}, {"model": "m", "key": 1}):
            with self.subTest(route=route):
                code, _, err = self.invoke({"cheap": route})
                self.assertNotEqual(code, 0)
                self.assertIn("--models route for 'cheap'", err)

    def test_a_declared_model_left_out_has_no_binding(self) -> None:
        code, _, err = self.invoke({"cheap": {"command": self.standin("cheap", True)}})
        self.assertNotEqual(code, 0)
        self.assertIn("no authorized host binding", err)


if __name__ == "__main__":
    unittest.main()
