"""Tests of the Python researcher: the same cases the Kosika one is tested on."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import researcher as r  # noqa: E402


def scripted(*answers: dict):
    """A model that gives these answers in order."""

    left = list(answers)

    def ask(prompt: str, fields: tuple[str, ...]) -> dict:
        if not left:
            raise RuntimeError("no answer left")
        return left.pop(0)

    return ask


class ResearcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.outer = Path(self._dir.name)
        self.folder = self.outer / "notes"
        (self.folder / "sub").mkdir(parents=True)
        (self.folder / ".git").mkdir()
        (self.folder / "work.md").write_text(
            "# Work\n\nKosika demo for Ravi is next Tuesday at 3pm\nRavi likes tea.\n",
            "utf-8",
        )
        (self.folder / ".git" / "config").write_text("ravi demo\n", "utf-8")
        (self.outer / "secret.md").write_text("ravi demo secret\n", "utf-8")

    def test_the_key_words_of_a_question_are_found(self) -> None:
        self.assertEqual(r.key_words("When is the demo with Ravi?"), "demo ravi")
        self.assertEqual(
            r.key_words("find invoice 2026", skip_first=True), "invoice 2026"
        )
        self.assertEqual(r.key_words("What does calc.py do?"), "calc.py")
        self.assertEqual(r.key_words("what is it?"), "")

    def test_search_ranks_lines_and_stays_in_the_folder(self) -> None:
        hits = r.search(self.folder, "ravi demo")
        self.assertEqual(
            hits[0], "work.md:3: Kosika demo for Ravi is next Tuesday at 3pm"
        )
        self.assertEqual(len(hits), 2)
        self.assertNotIn(".git", "\n".join(hits))
        self.assertEqual(r.peek(self.folder, "../secret.md"), "")
        with self.assertRaises(ValueError):
            r.inside(self.folder, "../secret.md")

    def test_an_answer_is_shown_with_its_quote_and_line(self) -> None:
        fast = scripted(
            {
                "answer": "The demo is next Tuesday at 3pm.",
                "quote": "Kosika demo for Ravi is next Tuesday at 3pm",
            }
        )
        text = r.respond(self.folder, "When is the demo with Ravi?", fast, fast)
        self.assertEqual(
            text,
            'The demo is next Tuesday at 3pm.\nQuote: "Kosika demo for Ravi is next Tuesday at 3pm"\nFrom: work.md, line 3',
        )

    def test_an_unproven_answer_goes_to_the_strong_model_then_is_withheld(self) -> None:
        fast = scripted(
            {"answer": "Next Tuesday.", "quote": "The demo will be held next Tuesday"}
        )
        strong = scripted(
            {"answer": "It is next Tuesday at 3pm.", "quote": "next Tuesday at 3pm."}
        )
        text = r.respond(self.folder, "When is the demo with Ravi?", fast, strong)
        self.assertTrue(text.startswith("It is next Tuesday at 3pm."))
        both_wrong = r.respond(
            self.folder,
            "When is the demo with Ravi?",
            scripted({"answer": "Friday.", "quote": "The demo is on Friday at noon"}),
            scripted({"answer": "", "quote": ""}),
        )
        self.assertTrue(both_wrong.startswith("I will not guess"))
        self.assertNotIn("Friday", both_wrong)

    def test_nothing_found_means_the_model_is_not_asked(self) -> None:
        nobody = scripted()
        self.assertIn(
            "I found nothing",
            r.respond(self.folder, "Who is my dentist?", nobody, nobody),
        )
        self.assertIn(
            "Ask me something", r.respond(self.folder, "what is it?", nobody, nobody)
        )

    def test_find_read_and_summarise(self) -> None:
        nobody = scripted()
        self.assertTrue(
            r.respond(self.folder, "find tea", nobody, nobody).startswith(
                '1 line(s) for "tea"'
            )
        )
        self.assertTrue(
            r.respond(self.folder, "read work.md", nobody, nobody).startswith(
                "work.md:\n# Work"
            )
        )
        self.assertIn(
            "could not read", r.respond(self.folder, "read missing.md", nobody, nobody)
        )
        brief = r.respond(
            self.folder,
            "summarize work.md",
            scripted({"text": "About a demo."}),
            nobody,
        )
        self.assertEqual(brief, "Summary of work.md:\nAbout a demo.")

    def test_a_quote_is_proved_only_by_a_line_that_holds_it(self) -> None:
        hits = ["a.md:3: The rent is 900 euros a month, due on the 1st."]
        self.assertEqual(r.proof(hits, '"The rent is 900 euros a month."'), hits[0])
        self.assertEqual(r.proof(hits, "The rent is 950 euros a month"), "")
        self.assertEqual(r.proof(hits, "rent"), "")
        self.assertEqual(r.source_of(hits[0]), "a.md, line 3")


if __name__ == "__main__":
    unittest.main()
