"""A value where a step expects a call may use the workflow's inputs and the
steps before it: `otherwise {text: msg.text}`."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kosika.checker import CheckError, plain_names
from kosika.modules import load_program
from kosika.testing import run_tests

SOURCE = """
record Msg { text: Text }
record Hits { matches: Text[] }
record Query { path: Text query: Text }
record Said { text: Text count: Number }

tool search {
  source system "files"
  operation: "search"
  access: observe
  input: Query
  output: Hits
}

agent hands {
  can: [search]
  max_steps: 1
}

fn found(hits: Hits) -> Said {
  return {text: hits.matches.join(","), count: hits.matches.length()}
}

workflow main(msg: Msg) -> Said {
  hits = execute hands.search(".", msg.text) otherwise {matches: [msg.text]}
  said = match msg.text {
    case "echo" { {text: msg.text + "!", count: hits.matches.length()} }
    case _ { found(hits) }
  }
}

test "a fallback value uses the workflow's input" {
  a = main({text: "tea"})
  expect a.text == "tea"
  expect a.count == 1
}

test "a case value uses the input and an earlier step" {
  answers search: [{matches: ["a", "b", "c"]}]
  a = main({text: "echo"})
  expect a.text == "echo!"
  expect a.count == 3
}
"""


class ValueNameTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "main.kos"

    def load(self, source: str):
        self.path.write_text(source, encoding="utf-8")
        return load_program(self.path)

    def test_the_values_work_in_a_run(self) -> None:
        program = self.load(SOURCE)
        report = run_tests(program)
        self.assertEqual(
            [(result.name, result.passed) for result in report],
            [
                ("a fallback value uses the workflow's input", True),
                ("a case value uses the input and an earlier step", True),
            ],
            [result.message for result in report],
        )

    def test_each_name_used_becomes_a_parameter_with_its_type(self) -> None:
        program = self.load(SOURCE)
        made = {f.name: f for f in program.functions if "$" in f.name}
        self.assertEqual(
            [(p.name, p.type_name) for p in made["main$hits$otherwise"].parameters],
            [("msg", "Msg")],
        )
        self.assertEqual(
            [(p.name, p.type_name) for p in made["main$said$case1"].parameters],
            [("hits", "Hits"), ("msg", "Msg")],
        )

    def test_a_value_cannot_use_its_own_step_or_a_later_one(self) -> None:
        for bad in ("{matches: [hits.matches[0]]}", "{matches: [said.text]}"):
            with self.subTest(bad=bad), self.assertRaises(CheckError):
                self.load(SOURCE.replace("{matches: [msg.text]}", bad))

    def test_a_name_used_with_the_wrong_type_is_still_refused(self) -> None:
        with self.assertRaises(CheckError):
            self.load(SOURCE.replace("{matches: [msg.text]}", "{matches: [msg]}"))

    def test_a_fault_is_said_in_terms_of_the_step_not_a_made_up_name(self) -> None:
        with self.assertRaises(CheckError) as failed:
            self.load(SOURCE.replace("{matches: [msg.text]}", "{matches: [msg]}"))
        said = str(failed.exception)
        self.assertTrue(
            said.startswith(
                "Step 'hits' in workflow 'main': the value after `otherwise` "
            ),
            said,
        )
        self.assertNotIn("$", said)
        with self.assertRaises(CheckError) as failed:
            self.load(SOURCE.replace('msg.text + "!"', "5"))
        self.assertIn(
            "Step 'said' in workflow 'main': the value in case 1", str(failed.exception)
        )

    def test_other_messages_are_left_as_they_are(self) -> None:
        self.assertEqual(plain_names("Step 'hits' is wrong"), "Step 'hits' is wrong")


if __name__ == "__main__":
    unittest.main()
