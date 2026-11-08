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
    return ScriptedModelProvider(
        [ModelResponse(content=json.dumps(value)) for value in values]
    )


class LearnTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.program = program()
        check(self.program)

    def ask(
        self, workflow: str, text: str, models: dict, trace: str | None = None
    ) -> dict:
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
        again = self.ask("reply", "How do I change my card?", {}, trace="again.jsonl")
        self.assertEqual(again, first)
        self.assertEqual(len(writer.requests), 1)
        events = [
            json.loads(line)
            for line in (self.folder / "again.jsonl").read_text().splitlines()
        ]
        returned = [event for event in events if event["event"] == "model_return"]
        self.assertEqual(returned[0]["learned"]["how"], "same")
        # The trace replays the same, with no lessons and no model.
        self.assertEqual(replay(self.folder / "again.jsonl")["text"], first["text"])

    def test_lessons_kept_under_the_earlier_name_are_moved_to_the_shared_one(
        self,
    ) -> None:
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
        again = self.ask("reply", "How do I change my card?", {})
        self.assertEqual(again["text"], "Open Settings, then Billing.")
        self.assertTrue(shared.is_file())
        self.assertFalse(earlier.exists())

    def test_nothing_learned_and_no_model_runs_the_fallback(self) -> None:
        said = self.ask("reply", "What is your refund policy?", {})
        self.assertEqual(said["text"], "I cannot answer that yet.")

    def test_classify_reuses_exact_answers_but_not_a_vote_on_an_unseen_question(
        self,
    ) -> None:
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
            self.assertEqual(
                self.ask("sort", text, {"assistant": model})["label"], label
            )
        self.assertEqual(
            self.ask("sort", examples[0][0], {}),
            {"label": "billing", "confidence": 0.9},
        )
        self.assertEqual(
            self.ask("sort", "why was my card charged twice", {}),
            {"label": "other", "confidence": 0},
        )
        self.assertEqual(self.ask("sort", "hello there", {})["label"], "other")

    def test_changed_meaning_never_reuses_a_nearby_answer(self) -> None:
        prefix = "Please check the final customer account report today before closing this support ticket and confirm that "
        pairs = [
            (
                prefix + "the refund was approved",
                prefix + "the refund was not approved",
            ),
            (
                prefix + "the customer owes the company",
                prefix + "the company owes the customer",
            ),
            (prefix + "the invoice amount is 150", prefix + "the invoice amount is 15"),
            ("the balance is +15", "the balance is -15"),
            ("the amount is 1.50", "the amount is 1 50"),
            ("compare US and us", "compare us and US"),
            ("status " * 300 + "approved", "status " * 300 + "denied"),
        ]
        for original, changed in pairs:
            with self.subTest(original=original[-80:]):
                writer = answers({"text": "approved"})
                self.assertEqual(
                    self.ask("reply", original, {"writer": writer}),
                    {"text": "approved"},
                )
                self.assertEqual(
                    self.ask("reply", changed, {}),
                    {"text": "I cannot answer that yet."},
                )
                self.assertEqual(self.ask("reply", original, {}), {"text": "approved"})

    def test_a_changed_request_asks_the_model_and_keeps_its_own_answer(self) -> None:
        writer = answers({"text": "credit"}, {"text": "debt"})
        for text, expected in (("balance +15", "credit"), ("balance -15", "debt")):
            self.assertEqual(
                self.ask("reply", text, {"writer": writer}), {"text": expected}
            )
        self.assertEqual(len(writer.requests), 2)
        for text, expected in (("balance +15", "credit"), ("balance -15", "debt")):
            self.assertEqual(self.ask("reply", text, {}), {"text": expected})

    def test_clipped_legacy_text_is_not_an_exact_request_identity(self) -> None:
        from kosika.runtime import _lesson_name

        self.ask(
            "reply", "status " * 300 + "approved", {"writer": answers({"text": "yes"})}
        )
        model = next(m for m in self.program.models if m.name == "writer")
        path = Lessons(self.folder / "memory")._path(
            _lesson_name("reply", "said", model)
        )
        kept = json.loads(path.read_text(encoding="utf-8"))
        legacy = [
            {key: item[key] for key in ("question", "plain", "answer", "at")}
            for item in kept
        ]
        path.write_text(json.dumps(legacy), encoding="utf-8")
        self.assertEqual(
            self.ask("reply", legacy[0]["question"], {}),
            {"text": "I cannot answer that yet."},
        )

    def test_numeric_boolean_list_and_explicit_key_context_changes_miss(self) -> None:
        source = """
record Ticket { text: Text amount: Number approved: Bool items: Text[] }
record Said { text: Text }
model writer { input: Ticket output: Said }
workflow reply(t: Ticket) -> Said {
  said = use_model writer(text: t.text, amount: t.amount, approved: t.approved, items: t.items) learn otherwise {text: "unknown"}
}
"""
        original = {
            "text": "check this invoice",
            "amount": 15,
            "approved": True,
            "items": ["a", "b"],
        }
        changes: list[dict[str, object]] = [
            {"amount": -15},
            {"approved": False},
            {"items": ["b", "a"]},
        ]
        for explicit in (False, True):
            with self.subTest(explicit=explicit):
                parsed = parse(
                    source.replace("learn otherwise", "learn by t.text otherwise")
                    if explicit
                    else source
                )
                check(parsed)
                memory = self.folder / str(explicit)
                writer = answers({"text": "yes"})
                first = run(
                    parsed,
                    "reply",
                    None,
                    inputs={"t": original},
                    model_bindings={"writer": writer},
                    lessons=Lessons(memory),
                )
                self.assertEqual(first, {"text": "yes"})
                for change in changes:
                    with self.subTest(change=change):
                        result = run(
                            parsed,
                            "reply",
                            None,
                            inputs={"t": {**original, **change}},
                            model_bindings={},
                            lessons=Lessons(memory),
                        )
                        self.assertEqual(result, {"text": "unknown"})
                repeated = run(
                    parsed,
                    "reply",
                    None,
                    inputs={"t": dict(reversed(list(original.items())))},
                    model_bindings={},
                    lessons=Lessons(memory),
                )
                self.assertEqual(repeated, first)

    def test_changed_label_options_cannot_reuse_the_old_classification(self) -> None:
        self.ask(
            "sort",
            "wrong total on this invoice",
            {"assistant": answers({"label": "billing", "confidence": 0.9})},
        )
        changed = parse(
            SOURCE.replace('["billing", "bug", "other"]', '["account", "bug", "other"]')
        )
        check(changed)
        result = run(
            changed,
            "sort",
            None,
            inputs={"t": {"text": "wrong total on this invoice"}},
            model_bindings={},
            lessons=Lessons(self.folder / "memory"),
        )
        self.assertEqual(result, {"label": "other", "confidence": 0})

    def test_semantic_similarity_is_not_runtime_permission_to_reuse(self) -> None:
        class Near:
            def nearest(self, question, texts):
                return [(0.99, 0)]

        self.ask(
            "reply", "please allow access", {"writer": answers({"text": "allowed"})}
        )
        result = run(
            self.program,
            "reply",
            None,
            inputs={"t": {"text": "access is forbidden"}},
            model_bindings={},
            lessons=Lessons(self.folder / "memory", Near()),
        )
        self.assertEqual(result, {"text": "I cannot answer that yet."})

    def test_a_kept_answer_cannot_bypass_a_program_verifier(self) -> None:
        text = "please approve access"
        self.ask("reply", text, {"writer": answers({"text": "approved"})})
        changed = parse(
            SOURCE.replace("learn by t.text", "verify refuse(said) learn by t.text")
            + '\nfn refuse(s: Said) -> Text { return "not permitted" }\n'
        )
        check(changed)
        result = run(
            changed,
            "reply",
            None,
            inputs={"t": {"text": text}},
            model_bindings={"writer": answers({"text": "approved"})},
            lessons=Lessons(self.folder / "memory"),
        )
        self.assertEqual(result, {"text": "I cannot answer that yet."})

    def test_mind_context_is_not_bypassed_by_a_kept_answer(self) -> None:
        from kosika.mind import MindHooks

        text = "what is the policy"
        self.ask("reply", text, {"writer": answers({"text": "old policy"})})
        writer = answers({"text": "new policy"})
        result = run(
            self.program,
            "reply",
            None,
            inputs={"t": {"text": text}},
            model_bindings={"writer": writer},
            lessons=Lessons(self.folder / "memory"),
            mind=MindHooks(context=lambda arguments: "policy has changed"),
        )
        self.assertEqual(result, {"text": "new policy"})
        self.assertEqual(
            json.loads(writer.requests[0].messages[1].content)["mind_context"],
            "policy has changed",
        )
        self.assertEqual(self.ask("reply", text, {}), {"text": "old policy"})

    def test_a_kept_answer_cannot_replace_required_votes(self) -> None:
        text = "please review this"
        self.ask("reply", text, {"writer": answers({"text": "yes"})})
        changed = parse(SOURCE.replace("learn by t.text", "vote 3 learn by t.text"))
        check(changed)
        writer = answers({"text": "yes"}, {"text": "no"}, {"text": "unsure"})
        result = run(
            changed,
            "reply",
            None,
            inputs={"t": {"text": text}},
            model_bindings={"writer": writer},
            lessons=Lessons(self.folder / "memory"),
        )
        self.assertEqual(result, {"text": "I cannot answer that yet."})
        self.assertEqual(len(writer.requests), 3)

    def test_a_model_fallback_does_not_inherit_learning(self) -> None:
        source = SOURCE.replace(
            "learn by t.text", "verify refuse(said) learn by t.text"
        )
        source = source.replace(
            "otherwise no_reply(t)", "otherwise use_model writer(note.text)"
        )
        parsed = parse(
            source + '\nfn refuse(s: Said) -> Text { return "not permitted" }\n'
        )
        check(parsed)
        for expected in ("first fallback", "fresh fallback"):
            with self.subTest(expected=expected):
                writer = answers({"text": "rejected"}, {"text": expected})
                result = run(
                    parsed,
                    "reply",
                    None,
                    inputs={"t": {"text": "same request"}},
                    model_bindings={"writer": writer},
                    lessons=Lessons(self.folder / "memory"),
                )
                self.assertEqual(result, {"text": expected})
                self.assertEqual(len(writer.requests), 2)

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


class CorrectionTests(unittest.TestCase):
    """A kept answer can be replaced or dropped, so a mistake is not forever."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name) / "memory"
        self.book = Lessons(self.folder)

    def ask(self, question: str, book: Lessons | None = None):
        found = (book or self.book).recall("reply", question, voting=False)
        return found["answer"] if found else None

    def test_a_wrong_answer_is_replaced_rather_than_added(self) -> None:
        self.book.keep("reply", "How do I change my card?", {"text": "Settings"})
        self.assertTrue(
            self.book.correct("reply", "how do i change my card", {"text": "Billing"})
        )
        self.assertEqual(self.ask("How do I change my card?"), {"text": "Billing"})
        self.assertEqual(self.book.count("reply"), 1)

    def test_a_correction_is_what_a_new_reader_sees(self) -> None:
        self.book.keep("reply", "How long does delivery take?", {"text": "A week"})
        self.book.correct(
            "reply", "How long does delivery take?", {"text": "Two to five days"}
        )
        fresh = Lessons(self.folder)
        self.assertEqual(
            self.ask("how long does delivery take", fresh),
            {"text": "Two to five days"},
        )

    def test_correcting_a_question_never_kept_teaches_it(self) -> None:
        self.assertFalse(
            self.book.correct(
                "reply", "What is the refund window?", {"text": "30 days"}
            )
        )
        self.assertEqual(self.ask("what is the refund window"), {"text": "30 days"})

    def test_forget_drops_one_answer_and_leaves_the_others(self) -> None:
        self.book.keep("reply", "How do I change my card?", {"text": "Billing"})
        self.book.keep("reply", "How long does delivery take?", {"text": "Days"})
        self.assertEqual(self.book.forget("reply", "how do I change my card"), 1)
        self.assertIsNone(self.ask("How do I change my card?"))
        self.assertEqual(self.ask("How long does delivery take?"), {"text": "Days"})
        self.assertEqual(self.book.count("reply"), 1)

    def test_forgetting_a_question_never_kept_writes_nothing(self) -> None:
        self.book.keep("reply", "How do I change my card?", {"text": "Billing"})
        self.assertEqual(self.book.forget("reply", "What is the refund window?"), 0)
        self.assertEqual(self.book.count("reply"), 1)

    def test_a_correction_with_a_request_identity_finds_the_same_lesson(self) -> None:
        self.book.keep(
            "reply", "balance +15", {"text": "credit"}, request="balance +15"
        )
        self.assertTrue(
            self.book.correct(
                "reply", "balance +15", {"text": "debit"}, request="balance +15"
            )
        )
        self.assertEqual(self.ask("balance +15"), {"text": "debit"})


VERIFIED = (
    SOURCE.replace("learn by t.text", "verify enough(said) learn by t.text")
    + """
fn enough(said: Said) -> Text {
  if said.text.contains("refuse") { return "The answer was refused." }
  return ""
}
"""
)


class VerifiedLessonTests(unittest.TestCase):
    """A kept answer is checked like a fresh one, and served only if it passes."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.program = parse(VERIFIED)
        check(self.program)

    def ask(self, text: str, models: dict) -> dict:
        return run(
            self.program,
            "reply",
            None,
            inputs={"t": {"text": text}},
            model_bindings=models,
            lessons=Lessons(self.folder / "memory"),
        )

    def test_a_kept_answer_passing_its_check_needs_no_model(self) -> None:
        answer = {"text": "Billing, then Payment methods."}
        first = self.ask("How do I change my card?", {"writer": answers(answer)})
        self.assertEqual(first, answer)
        self.assertEqual(self.ask("How do I change my card?", {}), first)

    def test_a_kept_answer_failing_its_check_is_not_served(self) -> None:
        from kosika.runtime import _lesson_name

        text = "How do I change my card?"
        self.ask(text, {"writer": answers({"text": "Billing, then Payment methods."})})
        model = next(m for m in self.program.models if m.name == "writer")
        book = Lessons(self.folder / "memory")
        self.assertTrue(
            book.correct(
                _lesson_name("reply", "said", model), text, {"text": "refuse this"}
            )
        )
        self.assertEqual(self.ask(text, {}), {"text": "I cannot answer that yet."})


class ContextRecallTest(unittest.TestCase):
    """A recall may be held to the inputs its answer was kept for."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.book = Lessons(Path(folder.name) / "memory")

    def test_a_recall_can_be_held_to_one_context(self) -> None:
        self.book.keep(
            "reply", "check this invoice", {"text": "approved"}, context="15"
        )
        self.book.keep(
            "reply", "check this invoice", {"text": "declined"}, context="16"
        )
        held = self.book.recall(
            "reply", "check this invoice", voting=False, context="16"
        )
        assert held is not None
        self.assertEqual(held["answer"], {"text": "declined"})
        other = self.book.recall(
            "reply", "check this invoice", voting=False, context="15"
        )
        assert other is not None
        self.assertEqual(other["answer"], {"text": "approved"})

    def test_a_recall_without_a_context_sees_every_lesson(self) -> None:
        self.book.keep(
            "reply", "check this invoice", {"text": "approved"}, context="15"
        )
        found = self.book.recall("reply", "check this invoice", voting=False)
        assert found is not None
        self.assertEqual(found["answer"], {"text": "approved"})

    def test_a_context_never_kept_finds_nothing(self) -> None:
        self.book.keep(
            "reply", "check this invoice", {"text": "approved"}, context="15"
        )
        self.assertIsNone(
            self.book.recall("reply", "check this invoice", voting=False, context="16")
        )


NEAR = SOURCE.replace("learn by t.text", "learn near by t.text")
NARROW_NEAR = """
record Ticket { text: Text amount: Number }
record Said { text: Text }
model writer { input: Ticket output: Said }
workflow reply(t: Ticket) -> Said {
  said = use_model writer(text: t.text, amount: t.amount) learn near otherwise {text: "nothing kept"}
}
"""


class NearLearnTests(unittest.TestCase):
    """`learn near`: reuse a question written differently, for the same inputs.

    The default stays exact, which is why the safety tests above keep their
    assertions: `near` is the program saying it accepts those risks.
    """

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.program = parse(NEAR)
        check(self.program)
        self.narrow = parse(NARROW_NEAR)
        check(self.narrow)

    def ask(self, text: str, models: dict, program=None) -> dict:
        return run(
            program or self.program,
            "reply",
            None,
            inputs={"t": {"text": text}},
            model_bindings=models,
            lessons=Lessons(self.folder / "memory"),
        )

    def ask_narrow(self, text: str, amount: float, models: dict, lessons) -> dict:
        return run(
            self.narrow,
            "reply",
            None,
            inputs={"t": {"text": text, "amount": amount}},
            model_bindings=models,
            lessons=lessons,
        )

    def test_a_question_in_another_order_is_answered_from_what_was_kept(self) -> None:
        answer = {"text": "Billing, then Payment methods."}
        self.ask("How do I change my card?", {"writer": answers(answer)})
        self.assertEqual(self.ask("change my card how do I?", {}), answer)

    def test_another_input_asks_the_model(self) -> None:
        book = Lessons(self.folder / "narrow")
        self.ask_narrow(
            "check this invoice", 15, {"writer": answers({"text": "approved"})}, book
        )
        self.assertEqual(
            self.ask_narrow("check this invoice", 16, {}, book),
            {"text": "nothing kept"},
        )

    def test_a_near_step_never_carries_other_figures(self) -> None:
        book = Lessons(self.folder / "sign")
        self.ask_narrow(
            "the balance is +15", 0, {"writer": answers({"text": "credit"})}, book
        )
        self.assertEqual(
            self.ask_narrow("the balance is -15", 0, {}, book),
            {"text": "nothing kept"},
        )

    def test_a_near_step_never_changes_words_in_capitals(self) -> None:
        self.ask("compare US and us", {"writer": answers({"text": "US only"})})
        self.assertEqual(
            self.ask("compare us and US", {}), {"text": "I cannot answer that yet."}
        )

    def test_a_question_that_reads_too_differently_is_asked(self) -> None:
        self.ask("How do I change my card?", {"writer": answers({"text": "Billing."})})
        self.assertEqual(
            self.ask("Where do I update my payment method?", {}),
            {"text": "I cannot answer that yet."},
        )

    def test_a_near_answer_is_checked_like_a_fresh_one(self) -> None:
        from kosika.runtime import _lesson_name

        verified = parse(VERIFIED.replace("learn by t.text", "learn near by t.text"))
        check(verified)
        text = "How do I change my card?"
        self.ask(text, {"writer": answers({"text": "Billing."})}, program=verified)
        model = next(m for m in verified.models if m.name == "writer")
        book = Lessons(self.folder / "memory")
        self.assertTrue(
            book.correct(_lesson_name("reply", "said", model), text, {"text": "refuse"})
        )
        self.assertEqual(
            self.ask(text, {}, program=verified),
            {"text": "I cannot answer that yet."},
        )


if __name__ == "__main__":
    unittest.main()
