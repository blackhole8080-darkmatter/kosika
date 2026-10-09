"""`verify`: the program's own check of a model's answer. `eval` and
`measure`: the same program scored on real models."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika.ast import program_payload
from kosika.checker import CheckError
from kosika.model import ModelProviderError, ModelResponse, ScriptedModelProvider
from kosika.modules import load_program
from kosika.parser import parse
from kosika.runtime import run
from kosika.story import build_story, read_trace
from kosika.testing import measure, measure_to_text, run_tests
from kosika.trace_replay import replay_trace

SOURCE = """
record Msg { text: Text }
record Said { text: Text }
record Draft { text: Text }

model writer {
  input: Msg
  output: Draft
}

fn short_enough(reply: Draft, most: Msg) -> Text {
  if reply.text.length() > 12 { return "Too long: at most 12 characters, you used " + reply.text.length() + "." }
  return ""
}

fn gave_up(msg: Msg) -> Draft {
  return {text: "(none)"}
}

fn say(reply: Draft) -> Said {
  return {text: reply.text}
}

workflow main(msg: Msg) -> Said {
  reply = use_model writer(msg.text) verify short_enough(reply, msg) retry 2 otherwise gave_up(msg)
  said  = say(reply)
}

test "scripted: the second answer passes" {
  answers writer: [{text: "This is far too long."}, {text: "Short."}]
  a = main({text: "hi"})
  expect a.text == "Short."
}

eval "live: the answer is short" {
  a = main({text: "Say hi in one word."})
  expect a.text.length() <= 12
  expect a.text != "(none)"
}

eval "live: it is not empty" {
  a = main({text: "Say hi in one word."})
  expect a.text.length() > 0
}
"""


def says(*texts: str) -> ScriptedModelProvider:
    return ScriptedModelProvider(
        [ModelResponse(content=json.dumps({"text": text})) for text in texts]
    )


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.folder = Path(self._dir.name)

    def program(self, source: str = SOURCE) -> Any:
        (self.folder / "main.kos").write_text(source, encoding="utf-8")
        return load_program(self.folder / "main.kos")

    def go(self, provider: Any, source: str = SOURCE) -> Any:
        return run(
            self.program(source),
            "main",
            self.folder / "run.jsonl",
            inputs={"msg": {"text": "hi"}},
            model_bindings={"writer": provider},
        )


class VerifyTests(Base):
    def test_a_refused_answer_is_sent_back_with_the_programs_own_reason(self) -> None:
        provider = says("This is far too long.", "Short.")
        self.assertEqual(self.go(provider), {"text": "Short."})
        again = provider.requests[1].messages
        self.assertEqual(
            json.loads(again[-2].content), {"text": "This is far too long."}
        )
        self.assertIn(
            "checked and refused: Too long: at most 12 characters, you used 21.",
            again[-1].content,
        )

    def test_the_check_is_recorded_and_the_run_replays(self) -> None:
        self.go(says("This is far too long.", "Short."))
        returned = [
            event
            for event in read_trace(self.folder / "run.jsonl")
            if event["event"] == "model_return"
        ][0]
        self.assertEqual(len(returned["rejected"]), 1)
        self.assertIn("Too long", returned["rejected"][0]["reason"])
        call, done = returned["verified"]
        self.assertEqual(call["function"], "short_enough")
        self.assertEqual(call["arguments"]["reply"], {"text": "Short."})
        self.assertEqual(done["result"], "")
        replay_trace(self.folder / "run.jsonl")
        step = build_story(read_trace(self.folder / "run.jsonl"))["steps"][0]
        self.assertIn("Too long", step["rejected"][0])

    def test_a_trace_that_keeps_a_refused_answer_does_not_replay(self) -> None:
        self.go(says("Short."))
        path = self.folder / "run.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        tampered = []
        for line in lines:
            event = json.loads(line)
            if event.get("event") == "model_return":
                event["verified"][1]["result"] = "Too long"
            tampered.append(json.dumps(event))
        path.write_text("\n".join(tampered) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "its own check refused"):
            replay_trace(path)

    def test_when_every_answer_is_refused_the_fallback_takes_over(self) -> None:
        provider = says("far too long one", "far too long two", "far too long three")
        self.assertEqual(self.go(provider), {"text": "(none)"})
        self.assertEqual(len(provider.requests), 3)

    def test_without_retry_one_refusal_fails_the_step(self) -> None:
        source = SOURCE.replace(" retry 2 otherwise gave_up(msg)", "")
        with self.assertRaisesRegex(ModelProviderError, "checked and refused"):
            self.go(says("This is far too long."), source)

    def test_an_answer_that_passes_is_asked_for_once(self) -> None:
        provider = says("Fine.")
        self.assertEqual(self.go(provider), {"text": "Fine."})
        self.assertEqual(len(provider.requests), 1)

    def test_the_check_must_be_a_text_function_on_a_model_step(self) -> None:
        for change, reason in (
            (("-> Text {\n  if reply", "-> Draft {\n  if reply"), None),
            (
                ("verify short_enough(reply, msg)", "verify no_such(reply)"),
                None,
            ),
            (
                ("verify short_enough(reply, msg)", "verify short_enough(msg, msg)"),
                None,
            ),
            (
                (
                    "  said  = say(reply)\n",
                    "  said  = say(reply) verify short_enough(reply, msg)\n",
                ),
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
        returns_record = SOURCE.replace(
            'fn short_enough(reply: Draft, most: Msg) -> Text {\n  if reply.text.length() > 12 { return "Too long: at most 12 characters, you used " + reply.text.length() + "." }\n  return ""\n}',
            "fn short_enough(reply: Draft, most: Msg) -> Draft {\n  return reply\n}",
        )
        with self.assertRaisesRegex(CheckError, "must return Text"):
            self.program(returns_record)

    def test_the_last_step_of_a_workflow_may_be_verified(self) -> None:
        source = SOURCE.replace(
            "workflow main(msg: Msg) -> Said {",
            "workflow drafted(msg: Msg) -> Draft {\n"
            "  reply = use_model writer(msg.text) verify short_enough(reply, msg) retry 1\n"
            "}\n\nworkflow main(msg: Msg) -> Said {",
        )
        self.program(source)

    def test_a_program_without_verify_hashes_as_before(self) -> None:
        plain = parse(SOURCE.replace(" verify short_enough(reply, msg)", ""))
        self.assertNotIn("verify", json.dumps(program_payload(plain)))
        self.assertIn("verify", json.dumps(program_payload(parse(SOURCE))))


class MeasureTests(Base):
    def test_kosika_test_leaves_evals_alone(self) -> None:
        results = run_tests(self.program())
        self.assertEqual(
            [result.name for result in results], ["scripted: the second answer passes"]
        )
        self.assertTrue(results[0].passed)

    def test_each_model_is_scored_on_the_same_evals(self) -> None:
        asked: dict[str, int] = {}

        class Model:
            def __init__(self, name: str, text: str) -> None:
                self.name, self.text = name, text

            def complete(self, request: Any) -> ModelResponse:
                asked[self.name] = asked.get(self.name, 0) + 1
                return ModelResponse(content=json.dumps({"text": self.text}))

        models = {
            "terse": Model("terse", "Hi."),
            "wordy": Model("wordy", "Hello there, friend!"),
        }
        report = measure(
            self.program(),
            lambda name: {"writer": models[name]},
            ["terse", "wordy"],
            repeat=2,
        )
        self.assertEqual(
            report["evals"], ["live: the answer is short", "live: it is not empty"]
        )
        terse, wordy = report["models"]
        self.assertEqual(
            (terse["passed"], terse["runs"], terse["model_calls"]), (4, 4, 4)
        )
        # The wordy model is refused and asked again, then the fallback runs:
        # three calls each time, and the first eval sees the fallback's text.
        self.assertEqual(
            (wordy["passed"], wordy["runs"], wordy["model_calls"]), (2, 4, 12)
        )
        self.assertEqual(asked, {"terse": 4, "wordy": 12})
        self.assertIn("expected a.text !=", wordy["evals"][0]["first_failure"])
        text = measure_to_text(report)
        self.assertIn(
            "terse  4 of 4",
            " ".join(text.split("\n")[3].split()).replace(" 4 of 4", "  4 of 4"),
        )
        self.assertIn("First failure of each eval that did not always pass:", text)

    def test_one_eval_can_be_chosen_and_none_is_explained(self) -> None:
        provider = says("Hi.")
        report = measure(
            self.program(),
            lambda name: {"writer": provider},
            ["m"],
            only="live: it is not empty",
        )
        self.assertEqual(report["models"][0]["runs"], 1)
        empty = measure(
            self.program(SOURCE.split('eval "live: the answer')[0]),
            lambda name: {},
            ["m"],
        )
        self.assertIn("No evals found", measure_to_text(empty))

    def test_an_eval_cannot_script_the_model_it_is_measuring(self) -> None:
        source = SOURCE.replace(
            'eval "live: it is not empty" {\n',
            'eval "live: it is not empty" {\n  answers writer: [{text: "Hi."}]\n',
        )
        report = measure(
            self.program(source), lambda name: {"writer": says("Hi.")}, ["m"]
        )
        failure = report["models"][0]["evals"][1]["first_failure"]
        self.assertIn("cannot script answers for 'writer'", failure)

    def test_a_model_that_fails_outright_is_a_failed_eval_not_a_crash(self) -> None:
        report = measure(
            self.program(),
            lambda name: {"writer": ScriptedModelProvider([])},
            ["m"],
            only="live: it is not empty",
        )
        self.assertEqual(report["models"][0]["passed"], 1)  # the fallback answered


if __name__ == "__main__":
    unittest.main()
