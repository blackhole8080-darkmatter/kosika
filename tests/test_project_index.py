"""The Project page: everything in a folder, and suggestions from rules."""

from __future__ import annotations

import re
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika import app as studio
from kosika.project_index import project_index

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "agents"

ROUGH = """record Msg { text: Text }
record Out { text: Text }
record Query { path: Text query: Text }
record Hits { matches: Text[] }

model writer {
  input: Msg
  output: Out
}

model spare {
  input: Msg
  output: Out
}

tool search_files {
  source system "files"
  operation: "search"
  access: observe
  input: Query
  output: Hits
}

workflow lonely(msg: Msg) -> Out {
  out = use_model writer(msg.text) otherwise {text: ""}
}

workflow main(msg: Msg) -> Out {
  out = use_model writer(msg.text)
}
"""


def index_of(folder: Path) -> dict[str, Any]:
    return project_index(
        [
            (f"{path.parent.name}/main.kos", path.parent.name, path)
            for path in sorted(folder.glob("*/main.kos"))
        ]
    )


class ProjectIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)

    def write(self, name: str, source: str) -> Path:
        folder = self.root / name
        folder.mkdir()
        (folder / "main.kos").write_text(source, encoding="utf-8")
        return folder

    def rules(self, name: str = "rough") -> dict[str, dict[str, Any]]:
        found = index_of(self.root)["suggestions"]
        return {s["rule"]: s for s in found if s["title"] == name}

    def test_everything_is_listed_with_its_program_and_line(self) -> None:
        view = index_of(EXAMPLES)
        # Counted from the folder, so a new example does not break the test.
        folders = [
            folder for folder in EXAMPLES.iterdir() if (folder / "main.kos").is_file()
        ]
        self.assertEqual(view["counts"]["agent"], len(folders))
        self.assertGreaterEqual(len(folders), 16)
        by_name = {
            (item["kind"], item["title"], item["name"]): item for item in view["items"]
        }
        reader = by_name[("model", "researcher", "reader")]
        self.assertEqual(reader["detail"], "given Ask, answers Found")
        git = by_name[("tool", "workbench", "run_git")]
        self.assertEqual(git["detail"], "starts the program git (asks you first)")
        self.assertIn(("memory", "coach", "sheets"), by_name)
        self.assertIn(("workflow", "planner", "main"), by_name)
        for item in view["items"]:
            source = (EXAMPLES / item["agent"]).read_text(encoding="utf-8").splitlines()
            if item["kind"] != "agent":
                with self.subTest(item=item["name"], agent=item["title"]):
                    self.assertIn(item["name"], source[item["line"] - 1])
        self.assertEqual(sum(view["counts"].values()), len(view["items"]))

    def test_each_suggestion_names_its_rule(self) -> None:
        self.write("rough", ROUGH)
        found = self.rules()
        self.assertEqual(
            set(found),
            {
                "no-tests",
                "no-evals",
                "model-step-no-fallback",
                "no-budget",
                "unused-tool",
                "unused-model",
                "unused-workflow",
                "no-purpose",
            },
        )
        self.assertIn("Step out in main", found["model-step-no-fallback"]["text"])
        at = found["model-step-no-fallback"]["line"]
        self.assertEqual(
            ROUGH.splitlines()[at - 1], "  out = use_model writer(msg.text)"
        )
        self.assertIn("search_files", found["unused-tool"]["text"])
        self.assertIn("spare", found["unused-model"]["text"])
        self.assertIn("lonely", found["unused-workflow"]["text"])
        self.assertFalse(any(s["must"] for s in found.values()))

    def test_a_suggestion_goes_when_its_cause_does(self) -> None:
        folder = self.write(
            "rough",
            "// Writes a line.\n"
            + ROUGH.replace(
                "workflow main(msg: Msg) -> Out {\n  out = use_model writer(msg.text)\n",
                "workflow main(msg: Msg) -> Out {\n  budget 2 model calls, 30 seconds\n"
                '  out = use_model writer(msg.text) otherwise {text: ""}\n',
            )
            + '\ntest "it answers" {\n  answers writer: [{text: "hi"}]\n'
            '  a = main({text: "x"})\n  expect a.text == "hi"\n}\n',
        )
        self.assertEqual(
            set(self.rules()),
            {"no-evals", "unused-tool", "unused-model", "unused-workflow"},
        )
        # A note on the Map tab also counts as a purpose.
        (folder / "main.kos").write_text(ROUGH, encoding="utf-8")
        (folder / "purpose.txt").write_text("Writes a line.\n", encoding="utf-8")
        self.assertNotIn("no-purpose", self.rules())

    def test_a_program_that_does_not_check_is_the_one_thing_to_fix(self) -> None:
        self.write("broken", "record {")
        view = index_of(self.root)
        self.assertEqual(view["counts"], {"agent": 1})
        only = view["suggestions"][0]
        self.assertTrue(only["must"])
        self.assertEqual(only["rule"], "does-not-check")

    def test_a_choice_is_listed_with_its_alternatives(self) -> None:
        self.write(
            "chosen",
            "choice Verdict {\n  accepted\n  refused\n}\n",
        )
        items = index_of(self.root)["items"]
        choice = next(item for item in items if item["kind"] == "choice")
        self.assertEqual(choice["name"], "Verdict")
        self.assertEqual(choice["detail"], "accepted, refused")
        self.assertEqual(choice["line"], 1)

    def test_a_model_named_by_a_thinking_step_is_not_called_unused(self) -> None:
        self.write(
            "judged",
            "record Msg { text: Text }\n"
            "model judge\nmodel idle\n"
            'fn no(msg: Msg) -> Choice {\n  return {label: "b", confidence: 0}\n}\n'
            "workflow main(msg: Msg) -> Choice {\n"
            "  budget 2 model calls\n"
            '  said = classify msg.text into ["a", "b"] using judge otherwise no(msg)\n'
            "}\n",
        )
        unused = [
            s["text"]
            for s in index_of(self.root)["suggestions"]
            if s["rule"] == "unused-model"
        ]
        self.assertEqual(unused, ["Model idle is declared but no step asks it."])

    def test_the_examples_are_clean_apart_from_missing_evals(self) -> None:
        found = index_of(EXAMPLES)["suggestions"]
        rules = {s["rule"] for s in found}
        # Some examples have workflows a host program runs by name (Kosika
        # Chat runs the assistant's `route`), so "never run by another
        # workflow" is a hint there, not a fault.
        self.assertLessEqual(rules, {"no-evals", "unused-workflow"})
        self.assertIn("no-evals", rules)
        self.assertFalse([s for s in found if s["must"]])

    def test_the_page_labels_every_kind_the_index_can_emit(self) -> None:
        page = studio.PAGE.read_text(encoding="utf-8")
        label = re.search(r"const KIND_LABEL = \{(.*?)\};\n", page)
        order = re.search(r"const KIND_ORDER = \[(.*?)\];\n", page)
        self.assertIsNotNone(label)
        self.assertIsNotNone(order)
        labels = set(re.findall(r"(\w+):", label.group(1)))
        ordered = set(re.findall(r'"(\w+)"', order.group(1)))
        # Every kind the index emits, including the ones only choice
        # programs use, has a heading and a filter chip.
        self.assertEqual(
            labels,
            {
                "agent",
                "workflow",
                "model",
                "tool",
                "memory",
                "board",
                "swarm",
                "record",
                "choice",
            },
        )
        self.assertEqual(labels, ordered)

    def test_studio_serves_it_and_the_page_shows_it(self) -> None:
        shutil.copytree(EXAMPLES / "scout", self.root / "scout")
        view = studio.App(self.root).project()
        self.assertEqual(view["counts"]["agent"], 1)
        page = studio.PAGE.read_text(encoding="utf-8")
        self.assertIn('["project", "Project"]', page)
        self.assertIn("never from a model", page)
        self.assertIn('id="icon-project"', page)
        self.assertNotIn("innerHTML", page)


if __name__ == "__main__":
    unittest.main()
