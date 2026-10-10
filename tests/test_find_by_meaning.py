"""A files tool that finds lines by meaning: operation `find`."""

from __future__ import annotations

import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from kosika.embeddings import EmbeddingError, Meaning
from kosika.modules import load_program
from kosika.runtime import run
from kosika.system_tools import MAX_FIND_MATCHES, SystemToolError, bind_system_tools
from kosika.trace_replay import replay_trace

SOURCE = """
record Msg { text: Text }
record Query { path: Text query: Text }
record Hits { matches: Text[] }

tool find_files {
  source system "files"
  operation: "find"
  access: observe
  input: Query
  output: Hits
}

agent hands {
  can: [find_files]
  max_steps: 1
}

workflow main(msg: Msg) -> Hits {
  hits = execute hands.find_files(".", msg.text) otherwise {matches: ["not granted"]}
}
"""

# A stand-in for an embedding model: each text is scored on a few ideas, so
# texts about the same idea are near each other whatever their words.
IDEAS = (
    ("rent", "landlord", "pay", "flat"),
    ("demo", "meeting", "present", "ravi"),
    ("tea", "drink", "coffee", "cup"),
)


class Fake:
    def __init__(self) -> None:
        self.asked: list[str] = []

    def __call__(self, texts: Sequence[str]) -> list[list[float]]:
        self.asked.extend(texts)
        return [
            [float(sum(word in text.lower() for word in idea)) for idea in IDEAS]
            + [0.01]
            for text in texts
        ]


class FindTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.notes = self.root / "notes"
        (self.notes / ".git").mkdir(parents=True)
        (self.notes / "home.md").write_text(
            "# Home\n\nThe flat costs 900 euros a month.\nRavi likes a cup of tea.\nok\n",
            encoding="utf-8",
        )
        (self.notes / "work.md").write_text(
            "We present the demo to the team on Tuesday.\n", encoding="utf-8"
        )
        (self.notes / ".git" / "config").write_text(
            "pay the landlord in secret\n", encoding="utf-8"
        )
        (self.root / "main.kos").write_text(SOURCE, encoding="utf-8")
        self.program = load_program(self.root / "main.kos")
        self.fake = Fake()
        self.meaning = Meaning(self.fake, "fake", self.root / "store" / "v.sqlite3")

    def ask(self, text: str, meaning: Meaning | None) -> Any:
        tools = bind_system_tools(self.program, folder=self.notes, meaning=meaning)
        return run(
            self.program,
            "main",
            self.root / "run.jsonl",
            inputs={"msg": {"text": text}},
            tool_bindings=tools,
        )["matches"]

    def test_it_finds_a_line_that_shares_no_word_with_the_question(self) -> None:
        matches = self.ask("What do I pay my landlord?", self.meaning)
        self.assertEqual(matches[0], "home.md:3: The flat costs 900 euros a month.")
        # Hidden folders are never read, and a line too short to mean
        # anything is never compared.
        self.assertNotIn(".git", "\n".join(matches))
        self.assertNotIn("home.md:5: ok", matches)
        self.assertLessEqual(len(matches), MAX_FIND_MATCHES)
        self.assertEqual(replay_trace(self.root / "run.jsonl"), {"matches": matches})

    def test_without_an_embedding_model_the_tool_cannot_be_reached(self) -> None:
        self.assertEqual(self.ask("What do I pay my landlord?", None), ["not granted"])

    def test_a_line_is_embedded_once_and_remembered_outside_the_folder(self) -> None:
        self.ask("When is the meeting?", self.meaning)
        first = len(self.fake.asked)
        before = sorted(p.name for p in self.notes.rglob("*"))
        # A new Meaning, as after a restart: only the question is asked again.
        again = Meaning(self.fake, "fake", self.root / "store" / "v.sqlite3")
        matches = self.ask("When is the meeting?", again)
        self.assertEqual(len(self.fake.asked), first + 1)
        self.assertTrue(matches[0].startswith("work.md:1:"))
        self.assertEqual(sorted(p.name for p in self.notes.rglob("*")), before)

    def test_only_so_many_new_lines_are_embedded_in_one_call(self) -> None:
        texts = [f"line number {n} about the flat" for n in range(10)]
        ranked = self.meaning.nearest("rent", texts, limit=4)
        self.assertEqual(len(ranked), 4)
        self.assertEqual(len(self.meaning.nearest("rent", texts, limit=4)), 8)

    def test_a_model_that_cannot_be_reached_fails_the_step(self) -> None:
        def broken(texts: Sequence[str]) -> list[list[float]]:
            raise EmbeddingError("no answer")

        self.assertEqual(self.ask("rent", Meaning(broken)), ["not granted"])
        tools = bind_system_tools(
            self.program, folder=self.notes, meaning=Meaning(broken)
        )
        with self.assertRaises(SystemToolError):
            tools["find_files"](self.program.tools[0], {"path": ".", "query": "rent"})
        with self.assertRaisesRegex(SystemToolError, "needs a question"):
            bind_system_tools(self.program, folder=self.notes, meaning=self.meaning)[
                "find_files"
            ](self.program.tools[0], {"path": ".", "query": "  "})


if __name__ == "__main__":
    unittest.main()
