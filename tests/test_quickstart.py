"""`kosika start`: one command, no options, nothing granted."""

from __future__ import annotations

import tempfile
import unittest
import unittest.mock
from pathlib import Path

from kosika import app as studio
from kosika.cli import build_parser
from kosika.quickstart import STARTERS, choose_model, one_model_warning, start_plan

GB = 1024**3
INSTALLED = [
    {"name": "nomic-embed-text:latest", "size": 274 * 1024**2},
    {"name": "qwen2.5:1.5b", "size": 1 * GB},
    {"name": "qwen2.5:7b", "size": 5 * GB},
    {"name": "gemma3:1b", "size": 1 * GB},
]


class QuickstartTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.folder = Path(self._dir.name) / "KosikaAgents"

    def test_a_new_folder_gets_starter_agents_that_check(self) -> None:
        arguments, lines = start_plan(self.folder, INSTALLED)
        self.assertEqual(
            sorted(p.name for p in self.folder.iterdir()), sorted(STARTERS)
        )
        cards = studio.App(self.folder).overview()["agents"]
        self.assertEqual(len(cards), len(STARTERS))
        self.assertFalse(any(card.get("error") for card in cards), cards)
        self.assertIn("3 starter agents were made for you", "\n".join(lines))
        self.assertEqual(arguments[:2], ["studio", str(self.folder)])

    def test_it_picks_an_installed_model_and_turns_on_search_by_meaning(self) -> None:
        arguments, lines = start_plan(self.folder, INSTALLED)
        self.assertEqual(
            arguments[2:],
            [
                "--model",
                "qwen2.5:7b",
                "--model-protocol",
                "ollama",
                "--embed-model",
                "nomic-embed-text",
            ],
        )
        told = "\n".join(lines)
        self.assertIn("Model: qwen2.5:7b", told)
        self.assertIn("Search by meaning: on, with nomic-embed-text", told)

    def test_it_grants_nothing(self) -> None:
        arguments, lines = start_plan(self.folder, INSTALLED)
        for grant in (
            "--allow-folder",
            "--allow-shell",
            "--allow-site",
            "--in-session",
            "--mcp",
        ):
            self.assertNotIn(grant, arguments)
        self.assertIn("Granted: nothing.", "\n".join(lines))
        self.assertIn("open Permissions in Studio", "\n".join(lines))
        # The arguments are an ordinary studio command.
        parsed = build_parser().parse_args(arguments)
        self.assertEqual(parsed.command, "studio")
        self.assertIsNone(parsed.allow_folder)
        self.assertFalse(parsed.allow_shell)

    def test_without_a_model_studio_still_opens_and_says_how_to_get_one(self) -> None:
        arguments, lines = start_plan(self.folder, None)
        self.assertEqual(arguments, ["studio", str(self.folder)])
        self.assertIn("ollama pull qwen2.5:7b", "\n".join(lines))
        none_chat, lines = start_plan(self.folder, [INSTALLED[0]])
        self.assertEqual(none_chat, ["studio", str(self.folder)])
        self.assertIn("has no chat model installed", "\n".join(lines))

    def test_an_existing_folder_is_left_as_it_is(self) -> None:
        self.folder.mkdir()
        (self.folder / "mine.kos").write_text(
            "record A { n: Number }\n", encoding="utf-8"
        )
        _, lines = start_plan(self.folder, INSTALLED)
        self.assertEqual([p.name for p in self.folder.iterdir()], ["mine.kos"])
        self.assertNotIn("were made for you", "\n".join(lines))

    def test_choosing_a_model(self) -> None:
        self.assertEqual(choose_model(INSTALLED), "qwen2.5:7b")
        # Nothing preferred: the largest that is not too large.
        others = [
            {"name": "huge:70b", "size": 40 * GB},
            {"name": "small:2b", "size": 2 * GB},
            {"name": "mid:6b", "size": 5 * GB},
        ]
        self.assertEqual(choose_model(others), "mid:6b")
        self.assertEqual(choose_model([others[0]]), "huge:70b")
        self.assertIsNone(choose_model([INSTALLED[0]]))

    def test_it_warns_when_only_one_model_may_be_loaded(self) -> None:
        self.assertEqual(one_model_warning(None), [])
        self.assertEqual(one_model_warning("3"), [])
        told = "\n".join(one_model_warning(" 1 "))
        self.assertIn("OLLAMA_MAX_LOADED_MODELS=1", told)
        self.assertIn("set it to 2", told)
        with unittest.mock.patch.dict("os.environ", {"OLLAMA_MAX_LOADED_MODELS": "1"}):
            _, lines = start_plan(self.folder, INSTALLED)
            self.assertIn("Slow: Ollama is set to keep only one", "\n".join(lines))
            # It is only about search by meaning: with no embedding model
            # installed, nothing is said.
            _, quiet = start_plan(self.folder, [INSTALLED[2]])
            self.assertNotIn("Slow:", "\n".join(quiet))
        with unittest.mock.patch.dict("os.environ", {"OLLAMA_MAX_LOADED_MODELS": "2"}):
            _, lines = start_plan(self.folder, INSTALLED)
            self.assertNotIn("Slow:", "\n".join(lines))

    def test_the_command_exists(self) -> None:
        parsed = build_parser().parse_args(["start"])
        self.assertEqual(parsed.command, "start")
        self.assertIsNone(parsed.folder)


if __name__ == "__main__":
    unittest.main()
