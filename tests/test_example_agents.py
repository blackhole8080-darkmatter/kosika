"""The example agents keep checking and passing their own tests, and the
file search they rely on finds the right lines and nothing outside."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kosika.modules import load_program
from kosika.parser import parse
from kosika.system_tools import (
    MAX_SEARCH_MATCHES,
    SystemToolError,
    bind_system_tools,
    declaration_problem,
)
from kosika.testing import run_tests

AGENTS = Path(__file__).resolve().parents[1] / "examples" / "agents"

SEARCHER = """
record Query { path: Text query: Text }
record Hits { matches: Text[] }

tool search_files {
  source system "files"
  operation: "search"
  access: observe
  input: Query
  output: Hits
}
"""


class ExampleAgentTests(unittest.TestCase):
    def test_every_example_agent_checks_and_passes_its_own_tests(self) -> None:
        found = sorted(AGENTS.glob("*/main.kos"))
        self.assertGreaterEqual(len(found), 6)
        for path in found:
            with self.subTest(agent=path.parent.name):
                program = load_program(path)
                results = run_tests(program)
                self.assertGreater(len(results), 0)
                failed = [f"{r.name}: {r.message}" for r in results if not r.passed]
                self.assertEqual(failed, [])


class SearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.outer = Path(self._dir.name)
        self.folder = self.outer / "granted"
        (self.folder / "notes").mkdir(parents=True)
        (self.folder / ".git").mkdir()
        (self.folder / "node_modules").mkdir()
        (self.folder / "notes" / "work.md").write_text(
            "# Work\n\nKosika demo for Ravi is next Tuesday.\nRavi likes tea.\n",
            "utf-8",
        )
        (self.folder / "home.md").write_text("Rent is due on the 1st.\n", "utf-8")
        (self.folder / ".git" / "config").write_text("ravi demo secret\n", "utf-8")
        (self.folder / "node_modules" / "x.js").write_text("ravi demo\n", "utf-8")
        (self.folder / "photo.bin").write_bytes(b"\xff\xfe ravi demo \x00")
        (self.outer / "outside.md").write_text("ravi demo outside\n", "utf-8")
        self.program = parse(SEARCHER)
        self.tool = self.program.tools[0]
        self.search = bind_system_tools(self.program, folder=self.folder)[
            "search_files"
        ]

    def find(self, query: str, path: str = ".") -> list[str]:
        return self.search(self.tool, {"path": path, "query": query})["matches"]

    def test_the_declaration_is_accepted_as_one_that_only_looks(self) -> None:
        self.assertIsNone(declaration_problem(self.program, self.tool))

    def test_lines_with_more_of_the_words_come_first_with_file_and_line(self) -> None:
        self.assertEqual(
            self.find("Ravi DEMO"),
            [
                "notes/work.md:3: Kosika demo for Ravi is next Tuesday.",
                "notes/work.md:4: Ravi likes tea.",
            ],
        )
        self.assertEqual(self.find("rent"), ["home.md:1: Rent is due on the 1st."])
        self.assertEqual(self.find("nothing-like-this"), [])

    def test_hidden_folders_tool_stores_and_files_that_are_not_text_are_skipped(
        self,
    ) -> None:
        joined = "\n".join(self.find("ravi demo secret"))
        self.assertNotIn(".git", joined)
        self.assertNotIn("node_modules", joined)
        self.assertNotIn("photo.bin", joined)

    def test_it_never_looks_outside_the_granted_folder(self) -> None:
        self.assertNotIn("outside", "\n".join(self.find("outside ravi")))
        for bad in ("..", "../", "notes/../..", str(self.outer)):
            with self.subTest(path=bad), self.assertRaises(SystemToolError):
                self.find("ravi", bad)

    def test_a_subfolder_can_be_searched_and_an_empty_search_is_refused(self) -> None:
        self.assertEqual(len(self.find("ravi", "notes")), 2)
        with self.assertRaises(SystemToolError):
            self.find("   ")
        with self.assertRaises(SystemToolError):
            self.find("ravi", "home.md")

    def test_a_search_gives_back_a_limited_number_of_short_lines(self) -> None:
        (self.folder / "big.txt").write_text(
            "\n".join(f"ravi {'x' * 400} {i}" for i in range(200)), "utf-8"
        )
        found = self.find("ravi")
        self.assertEqual(len(found), MAX_SEARCH_MATCHES)
        self.assertTrue(all(len(line) < 300 for line in found))


if __name__ == "__main__":
    unittest.main()
