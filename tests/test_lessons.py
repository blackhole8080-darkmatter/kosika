"""`learn`: a thinking step keeps its model's accepted answers and answers
again from them, so it needs the model less and less, and sometimes not at
all."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kosika.checker import CheckError, check
from kosika.lessons import Lessons
from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.parser import parse
from kosika.runtime import replay, run

SOURCE = """
record Ticket { text: Text }
record Said { text: Text }
record Note { text: Text }

model assistant

fn unsure(t: Ticket) -> Choice {
  return {label: "other", confidence: 0}
}

fn no_reply(t: Ticket) -> Said {
  return {text: "I cannot answer that yet."}
}

workflow sort(t: Ticket) -> Choice {
  label = classify t.text into ["billing", "bug", "other"] using assistant learn otherwise unsure(t)
}

workflow reply(t: Ticket) -> Said {
  note = make_note(t)
  said = use_model writer(note.text) learn by t.text otherwise no_reply(t)
}

fn make_note(t: Ticket) -> Note {
  return {text: "Answer briefly: " + t.text}
}

model writer {
  input: Note
  output: Said
}
"""


def program():
    return parse(SOURCE)


def answers(*values: dict) -> ScriptedModelProvider:
    return ScriptedModelProvider([ModelResponse(content=json.dumps(value)) for value in values])


class LearnTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.program = program()
        check(self.program)

    def ask(self, workflow: str, text: str, models: dict, trace: str | None = None) -> dict:
        return run(
            self.program,
            workflow,
            self.folder / trace if trace else None,
            inputs={"t": {"text": text}},
            model_bindings=models,
            lessons=Lessons(self.folder / "memory"),
        )

    def test_a_question_asked_before_needs_no_model(self) -> None:
        writer = answers({"text": "Open Settings, then Billing."})
        first = self.ask("reply", "How do I change my card?", {"writer": writer})
        self.assertEqual(first["text"], "Open Settings, then Billing.")
        # Asked again, in other case and punctuation, with no model at all.
        again = self.ask("reply", "how do I change my card", {}, trace="again.jsonl")
        self.assertEqual(again, first)
        self.assertEqual(len(writer.requests), 1)
        events = [json.loads(line) for line in (self.folder / "again.jsonl").read_text().splitlines()]
        returned = [event for event in events if event["event"] == "model_return"]
        self.assertEqual(returned[0]["learned"]["how"], "same")
        # The trace replays the same, with no lessons and no model.
        self.assertEqual(replay(self.folder / "again.jsonl")["text"], first["text"])

    def test_lessons_kept_under_the_earlier_name_are_moved_to_the_shared_one(self) -> None:
        from kosika.runtime import _earlier_lesson_name, _lesson_name

        writer = answers({"text": "Open Settings, then Billing."})
        self.ask("reply", "How do I change my card?", {"writer": writer})
        model = next(m for m in self.program.models if m.name == "writer")
        book = Lessons(self.folder / "memory")
        shared = book._path(_lesson_name("reply", "said", model))
        earlier = book._path(_earlier_lesson_name("reply", "said", model))
        self.assertNotEqual(shared, earlier)
        # As an earlier version of Kosika left it.
        shared.replace(earlier)
        again = self.ask("reply", "how do I change my card", {})
        self.assertEqual(again["text"], "Open Settings, then Billing.")
        self.assertTrue(shared.is_file())
        self.assertFalse(earlier.exists())

    def test_nothing_learned_and_no_model_runs_the_fallback(self) -> None:
        said = self.ask("reply", "What is your refund policy?", {})
        self.assertEqual(said["text"], "I cannot answer that yet.")

    def test_classify_learns_from_examples_and_then_answers_alone(self) -> None:
        examples = [
            ("I was charged twice for my plan", "billing"),
            ("my invoice shows the wrong amount charged", "billing"),
            ("refund the extra charge on my card", "billing"),
            ("the charge on my card is wrong", "billing"),
            ("the app crashes when I open settings", "bug"),
            ("the page shows an error after login", "bug"),
            ("the button does nothing when clicked", "bug"),
        ]
        model = answers(*({"label": label, "confidence": 0.9} for _, label in examples))
        for text, label in examples:
            self.assertEqual(self.ask("sort", text, {"assistant": model})["label"], label)
        # A new question, never seen, answered by the examples alone.
        alone = self.ask("sort", "why was my card charged twice", {})
        self.assertEqual(alone["label"], "billing")
        self.assertGreaterEqual(alone["confidence"], 0.8)
        # One the examples do not agree on goes to the fallback.
        self.assertEqual(self.ask("sort", "hello there", {})["label"], "other")

    def test_without_a_lessons_folder_it_is_an_ordinary_step(self) -> None:
        writer = answers({"text": "one"}, {"text": "two"})
        for expected in ("one", "two"):
            said = run(
                self.program,
                "reply",
                None,
                inputs={"t": {"text": "same question"}},
                model_bindings={"writer": writer},
            )
            self.assertEqual(said["text"], expected)

    def test_only_a_model_step_learns(self) -> None:
        bad = SOURCE.replace("note = make_note(t)", "note = make_note(t) learn")
        with self.assertRaisesRegex(CheckError, "cannot learn: only a model step"):
            check(parse(bad))
        bad = SOURCE.replace("learn by t.text", "learn by t")
        with self.assertRaisesRegex(CheckError, "must be Text"):
            check(parse(bad))


if __name__ == "__main__":
    unittest.main()
