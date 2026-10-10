"""The Map tab: what an agent is for, what it may touch, and what it does."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika import app as studio
from kosika.modules import load_program
from kosika.project_map import MAX_STEPS, NOTE_FILE, beginner_map

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def map_of(path: Path, **extra: Any) -> dict[str, Any]:
    return beginner_map(load_program(path), path.read_text(encoding="utf-8"), **extra)


class MapTests(unittest.TestCase):
    def test_the_researcher_only_looks(self) -> None:
        view = map_of(EXAMPLES / "agents" / "researcher" / "main.kos")
        self.assertEqual(
            {item["name"] for item in view["reads"]},
            {"search_files", "find_files", "look_at", "near_to"},
        )
        self.assertEqual(view["changes"], [])
        self.assertEqual(view["outside"], [])
        self.assertEqual([m["name"] for m in view["models"]], ["reader", "strong"])
        self.assertEqual(view["thinking"], ["summarize"])
        self.assertEqual(view["given"][0]["fields"], [{"name": "text", "type": "Text"}])
        self.assertEqual(view["budget"], [["model_calls", 3], ["seconds", 240]])

    def test_starting_a_program_is_a_change_that_kosika_cannot_see_inside(self) -> None:
        view = map_of(EXAMPLES / "agents" / "workbench" / "main.kos")
        changes = {item["name"]: item for item in view["changes"]}
        self.assertEqual(set(changes), {"run_git", "run_python"})
        self.assertEqual(changes["run_git"]["does"], "starts the program git")
        self.assertIn("not visible to Kosika", changes["run_git"]["unseen"])
        self.assertEqual(view["coverage"]["unseen"], 2)
        self.assertEqual([m["name"] for m in view["memories"]], ["journal"])

    def test_an_outside_server_is_shown_as_unseen(self) -> None:
        view = map_of(EXAMPLES / "mcp" / "librarian.kos")
        unseen = [*view["outside"], *view["changes"]]
        self.assertTrue(unseen)
        for item in unseen:
            self.assertIn("outside server notes", item["does"])
            self.assertIn("not visible to Kosika", item["unseen"])

    def test_every_line_points_at_its_declaration(self) -> None:
        for path in sorted((EXAMPLES / "agents").glob("*/main.kos")):
            source = path.read_text(encoding="utf-8").splitlines()
            view = map_of(path)
            listed = [
                *view["reads"],
                *view["changes"],
                *view["outside"],
                *view["models"],
                *view["memories"],
                *view["steps"],
            ]
            self.assertTrue(listed, path)
            self.assertLessEqual(len(view["steps"]), MAX_STEPS)
            for item in listed:
                with self.subTest(agent=path.parent.name, item=item["name"]):
                    self.assertEqual(item["source"], "checked")
                    self.assertIsNotNone(item["line"])
                    self.assertIn(item["name"], source[item["line"] - 1])

    def test_a_step_says_its_checks_and_what_happens_if_it_fails(self) -> None:
        source = (
            "record Msg { text: Text }\n"
            "record Found { answer: Text }\n"
            "model reader { input: Msg output: Found }\n"
            "model strong { input: Msg output: Found }\n"
            'fn fine(found: Found) -> Text {\n  return ""\n}\n'
            "workflow main(msg: Msg) -> Found {\n"
            "  found = use_model reader(msg.text) verify fine(found) retry 1 "
            'escalate strong otherwise {answer: ""}\n'
            "}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "main.kos"
            path.write_text(source, encoding="utf-8")
            step = map_of(path)["steps"][0]
        self.assertEqual(step["does"], "asks the model reader")
        self.assertEqual(
            step["notes"],
            [
                "the answer must pass a check written in the program",
                "asked again up to 1 more time(s) if refused",
                "a stronger model (strong) is asked if needed",
                "if it fails: uses a value written in the program",
            ],
        )

    def test_purpose_is_never_guessed(self) -> None:
        path = EXAMPLES / "agents" / "scout" / "main.kos"
        self.assertEqual(map_of(path)["purpose"], {"text": "", "source": "none"})
        told = map_of(path, about="Finds things.")["purpose"]
        self.assertEqual(told, {"text": "Finds things.", "source": "comment"})
        both = map_of(path, about="Finds things.", note="Answers from my notes.")
        self.assertEqual(both["purpose"]["source"], "person")
        self.assertEqual(both["coverage"]["person"], 1)


class StudioMapTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        shutil.copytree(EXAMPLES / "agents" / "scout", self.root / "scout")
        self.app = studio.App(self.root)

    def test_a_note_is_kept_beside_the_program_and_survives_a_restart(self) -> None:
        first = self.app.map("scout/main.kos")
        self.assertEqual(first["purpose"]["source"], "comment")
        saved = self.app.save_purpose("scout/main.kos", "  Answers from\nmy notes. ")
        self.assertEqual(
            saved["purpose"], {"text": "Answers from my notes.", "source": "person"}
        )
        self.assertTrue((self.root / "scout" / NOTE_FILE).is_file())
        again = studio.App(self.root).map("scout/main.kos")
        self.assertEqual(again["purpose"]["text"], "Answers from my notes.")
        # An empty note takes the note away again.
        cleared = self.app.save_purpose("scout/main.kos", " ")
        self.assertEqual(cleared["purpose"]["source"], "comment")
        self.assertFalse((self.root / "scout" / NOTE_FILE).exists())

    def test_a_program_that_does_not_check_has_no_map(self) -> None:
        path = self.root / "scout" / "main.kos"
        path.write_text(
            path.read_text(encoding="utf-8") + "\nrecord {\n", encoding="utf-8"
        )
        with self.assertRaises(studio.AppError) as caught:
            self.app.map("scout/main.kos")
        self.assertIn("does not check", str(caught.exception))

    def test_the_page_has_the_tab_and_stays_self_contained(self) -> None:
        page = studio.PAGE.read_text(encoding="utf-8")
        self.assertIn('"Map": mapTab', page)
        self.assertIn("No model wrote any of it.", page)
        self.assertNotIn("innerHTML", page)
        self.assertNotIn("http://", page)
        self.assertNotIn("https://", page)


if __name__ == "__main__":
    unittest.main()
