"""Help while writing in Studio: where the first problem is, and words to complete."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika import app as studio

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "agents"


class WritingHelpTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        shutil.copytree(EXAMPLES / "researcher", self.root / "researcher")
        self.app = studio.App(self.root)
        self.path = self.root / "researcher" / "main.kos"
        self.source = self.path.read_text(encoding="utf-8")

    def help(self, source: str) -> dict[str, Any]:
        return self.app.assist("researcher/main.kos", source)

    def word_at(self, source: str, problem: dict[str, Any]) -> str:
        row = source.splitlines()[problem["line"] - 1]
        start = problem["column"] - 1
        return row[start : start + problem["length"]]

    def test_a_good_program_has_no_problem_and_offers_its_own_names(self) -> None:
        found = self.help(self.source)
        self.assertTrue(found["ok"])
        self.assertIsNone(found["problem"])
        words = {word["label"]: word for word in found["words"]}
        self.assertEqual(words["proven"]["kind"], "fn")
        self.assertEqual(words["Hits"]["kind"], "record")
        self.assertEqual(words["search_files"]["kind"], "tool")
        self.assertEqual(words["take"]["detail"], "value.take(count)")
        self.assertEqual(words["matches"]["kind"], "field")
        self.assertIn("if the step fails", words["otherwise"]["detail"])
        labels = [word["label"] for word in found["words"]]
        self.assertEqual(len(labels), len(set(labels)))

    def test_a_problem_points_at_the_word_at_fault(self) -> None:
        for old, new, wrong in (
            ("hits.matches.take(5)", "hits.matchs.take(5)", "matchs"),
            ("return rows.join(", "return row.join(", "row"),
            ("query.is_empty()", "query.is_emty()", "is_emty"),
        ):
            with self.subTest(wrong=wrong):
                source = self.source.replace(old, new, 1)
                found = self.help(source)
                self.assertFalse(found["ok"])
                self.assertEqual(self.word_at(source, found["problem"]), wrong)
                self.assertIn(wrong, found["problem"]["message"])
                # The file's own path is not repeated in front of the message.
                self.assertNotIn(str(self.path), found["problem"]["message"])

    def test_a_text_that_does_not_parse_still_gets_a_place_and_words(self) -> None:
        source = self.source.replace("record Msg {", "record Msg (", 1)
        found = self.help(source)
        self.assertEqual(self.word_at(source, found["problem"]), "(")
        self.assertIn("Expected '{'", found["problem"]["message"])
        self.assertIn("proven", {word["label"] for word in found["words"]})

    def test_nothing_is_written(self) -> None:
        self.help(self.source.replace("record Msg {", "record Msg (", 1))
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.source)

    def test_the_page_offers_words_and_shows_the_problem(self) -> None:
        page = studio.PAGE.read_text(encoding="utf-8")
        self.assertIn("/api/assist", page)
        self.assertIn("Tab completes the first:", page)
        self.assertIn("Click to go there", page)
        self.assertNotIn("innerHTML", page)


if __name__ == "__main__":
    unittest.main()
