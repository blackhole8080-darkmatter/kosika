"""Kosi as a pill on the screen: everything about it that is not the window."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from kosika import cli, desktop_app, pill
from kosika.model import ModelResponse, ScriptedModelProvider

ROOT = Path(__file__).resolve().parents[1]
ASSISTANT = ROOT / "examples" / "agents" / "assistant"


class EngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.agents = Path(self._dir.name)
        shutil.copytree(
            ASSISTANT,
            self.agents / "assistant",
            ignore=shutil.ignore_patterns("memory", "runs"),
        )

    def engine(self, responses: list[ModelResponse] | None = None) -> pill.Engine:
        provider = ScriptedModelProvider(responses or [])
        return pill.Engine(self.agents, lambda program: {"voice": provider})

    def test_a_sum_is_answered_with_no_model(self) -> None:
        answer = self.engine().ask("what is 17 times 23")
        self.assertEqual((answer.text, answer.failed), ("391", False))

    def test_what_it_offers_next_comes_back_too(self) -> None:
        answer = self.engine().ask("remind me to call Sam")
        self.assertIn("I will remind you to call Sam", answer.text)
        self.assertEqual(answer.suggest, ("my reminders",))

    def test_a_question_goes_to_the_model(self) -> None:
        said = ModelResponse(content=json.dumps({"text": "Light scatters."}))
        answer = self.engine([said]).ask("why is the sky blue?")
        self.assertEqual(answer.text, "Light scatters.")

    def test_with_no_model_the_plain_things_still_work(self) -> None:
        engine = pill.Engine(self.agents, None)
        self.assertEqual(engine.ask("what is 2 plus 2").text, "4")
        # The assistant itself says, politely, that it has no answer.
        self.assertIn("could not think", engine.ask("why is the sky blue?").text)

    def test_nothing_typed_asks_nothing(self) -> None:
        engine = self.engine()
        self.assertEqual(engine.ask("   ").text, "")
        self.assertEqual(engine.app._jobs, {})

    def test_it_reads_the_reminders_the_assistant_keeps(self) -> None:
        engine = self.engine()
        engine.ask("remind me to stretch")
        book = pill.read_book(self.agents)
        self.assertEqual(book["reminders"][0]["what"], "stretch")
        self.assertIsNone(pill.read_book(self.agents / "nowhere"))


class AskingTests(unittest.TestCase):
    """An agent Kosi handed a request to wants to change a file."""

    def setUp(self) -> None:
        from tests.test_hand_over import SCRIBE

        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        top = Path(self._dir.name)
        self.agents, self.work = top / "agents", top / "work"
        self.work.mkdir()
        shutil.copytree(
            ASSISTANT,
            self.agents / "assistant",
            ignore=shutil.ignore_patterns("memory", "runs"),
        )
        (self.agents / "scribe").mkdir()
        (self.agents / "scribe" / "main.kos").write_text(SCRIBE, encoding="utf-8")
        self.engine = pill.Engine(self.agents, None)
        self.addCleanup(self.engine.app.close)
        self.engine.pick = lambda: str(self.work)

    def test_no_folder_is_reachable_until_one_is_chosen(self) -> None:
        self.assertEqual(self.engine.folder(), "")
        said = self.engine.choose_folder()
        self.assertIn("Anything they want to change there, I will ask you first", said)
        self.assertEqual(Path(self.engine.folder()), self.work.resolve())
        self.assertEqual(
            self.engine.forget_folder(), "Agents can no longer reach any folder."
        )
        self.assertEqual(self.engine.folder(), "")

    def test_picking_nothing_grants_nothing(self) -> None:
        self.engine.pick = lambda: ""
        self.assertEqual(self.engine.choose_folder(), "")
        self.assertEqual(self.engine.folder(), "")

    def test_a_yes_lets_the_change_happen_and_the_question_says_who_and_what(
        self,
    ) -> None:
        self.engine.choose_folder()
        seen: list[dict[str, Any]] = []

        def yes(request: dict[str, Any]) -> bool:
            seen.append(request)
            return True

        answer = self.engine.ask("@scribe remember the milk", decide=yes)
        self.assertEqual(answer.text, "The scribe agent says: saved 17 bytes")
        self.assertEqual(len(seen), 1)
        self.assertEqual(
            pill.ask_words(seen[0]),
            "The scribe agent wants to write the file note.txt (17 characters).",
        )
        self.assertEqual(
            (self.work / "note.txt").read_text(encoding="utf-8"), "remember the milk"
        )

    def test_a_no_changes_nothing(self) -> None:
        self.engine.choose_folder()
        answer = self.engine.ask(
            "@scribe remember the milk", decide=lambda request: False
        )
        self.assertEqual(answer.text, "That was refused, so nothing was changed.")
        self.assertFalse((self.work / "note.txt").exists())

    def test_with_nobody_to_ask_the_answer_is_no(self) -> None:
        self.engine.choose_folder()
        self.engine.ask("@scribe remember the milk")
        self.assertFalse((self.work / "note.txt").exists())

    def test_the_question_in_words(self) -> None:
        self.assertEqual(
            pill.ask_words(
                {
                    "kind": "system:shell",
                    "operation": "python",
                    "arguments": {"args": ["-m", "pytest"]},
                }
            ),
            "An agent wants to start the program: python -m pytest.",
        )
        plan = pill.ask_words(
            {
                "tool": "plan",
                "agent": "coder",
                "warnings": ["calc.py is not valid Python"],
                "plan": [
                    {"tool": "write_file", "kind": "system:files", "operation": "write",
                     "arguments": {"path": "calc.py", "text": "x = 1\n"}},
                    {"tool": "write_file", "kind": "system:files", "operation": "create",
                     "arguments": {"path": "test_calc.py", "text": "y"}},
                ],
            }
        )  # fmt: skip
        self.assertEqual(
            plan.splitlines(),
            [
                "The coder agent wants to make these 2 changes, in this order:",
                "1. write the file calc.py (6 characters)",
                "2. create the file test_calc.py (1 characters)",
                "Checked before asking, and something is wrong: calc.py is not valid Python",
            ],
        )
        long = pill.ask_words({"plan": [{"tool": "t"}] * 9})
        self.assertIn("and 3 more. Open Kosika Studio to read them all.", long)
        self.assertEqual(
            pill.ask_words(None),
            "An agent wants to do something it could not describe.",
        )
        self.assertEqual(
            pill.ask_words({"tool": "send", "arguments": {"to": "x" * 200}}).count("x"),
            59,
        )


class LogicTests(unittest.TestCase):
    def test_it_sits_against_the_right_edge_half_way_down(self) -> None:
        self.assertEqual(pill.place((1920, 1032), (38, 72)), (1874, 480))
        # Opened, it is wider; its right edge and its middle have not moved.
        rest = pill.place((1920, 1032), (38, 72), 10)
        wide = pill.place((1920, 1032), (575, 58), 10)
        self.assertEqual(rest[0] + 38, wide[0] + 575)
        self.assertEqual(rest[1] + 36, wide[1] + 29)
        self.assertEqual(pill.place((100, 50), (460, 46)), (0, 2))
        self.assertEqual(pill.place((100, 40), (460, 46)), (0, 0))

    def test_standing_on_end_the_mark_is_in_the_middle(self) -> None:
        standing = pill.pill_image((38, 72), False)
        if standing is None:
            self.skipTest("Pillow is not installed")
        self.assertEqual(standing.size, (38, 72))
        # The ring's dot is light and in the centre; the ends are body only.
        self.assertGreater(sum(standing.getpixel((19, 36))[:3]), 500)
        self.assertLess(sum(standing.getpixel((19, 8))[:3]), 300)

    def test_the_picture_has_a_soft_edge_and_room_for_its_shadow(self) -> None:
        rest = pill.pill_image((140, 42), False, "Kosi", None, 1.25, 15)
        if rest is None:
            self.skipTest("Pillow is not installed")
        awake = pill.pill_image((140, 42), True, "Ask Kosi", 40, 1.25, 15)
        self.assertEqual(rest.size, (170, 72))
        self.assertEqual(rest.mode, "RGBA")
        solid = rest.getchannel("A")
        # Nothing in the corner, the screen showing through the middle at
        # rest, and nothing showing through when awake.
        self.assertEqual(solid.getpixel((0, 0)), 0)
        self.assertTrue(150 < solid.getpixel((100, 22)) < 255)
        self.assertEqual(awake.getchannel("A").getpixel((100, 22)), 255)
        # The edge fades: some pixels are neither nothing nor solid.
        row = [solid.getpixel((x, 36)) for x in range(170)]
        self.assertTrue(any(0 < value < 150 for value in row))
        self.assertNotEqual(rest.tobytes(), awake.tobytes())

    def test_colours_are_mixed_by_share(self) -> None:
        self.assertEqual(pill.mix("#000000", "#ffffff", 0.5), "#808080")
        self.assertEqual(pill.mix("#102030", "#ffffff", 0), "#102030")

    def test_off_windows_the_window_helpers_do_nothing(self) -> None:
        with mock.patch.object(pill.sys, "platform", "linux"):
            pill.sharpen()
            pill.round_corners(None)
            self.assertEqual(pill.usable_area((800, 600)), (800, 600))
            self.assertFalse(pill.layer(None, None, 0, 0))

    def test_a_reminder_is_due_once_its_time_has_come_and_only_once(self) -> None:
        book = {
            "reminders": [
                {"what": "stretch", "due": 100, "at": "9:15"},
                {"what": "later", "due": 900, "at": "9:30"},
                {"what": "no time", "due": 0, "at": ""},
                "nonsense",
            ]
        }
        self.assertEqual(
            [r["what"] for r in pill.due_now(book, 500, set())], ["stretch"]
        )
        self.assertEqual(pill.due_now(book, 500, {"100|stretch"}), [])
        self.assertEqual(len(pill.due_now(book, 1000, set())), 2)
        for broken in (
            None,
            [],
            {"reminders": "x"},
            {"reminders": [{"due": True, "what": "x"}]},
        ):
            self.assertEqual(pill.due_now(broken, 1000, set()), [])

    def test_a_long_answer_is_cut_and_says_where_the_rest_is(self) -> None:
        self.assertEqual(pill.shown("  391  "), "391")
        cut = pill.shown("word " * 400)
        self.assertLess(len(cut), pill.MAX_SHOWN + 60)
        self.assertTrue(cut.endswith("Open Kosika Studio for the rest."))

    def test_a_longer_answer_stays_up_longer_within_limits(self) -> None:
        self.assertEqual(pill.stay_seconds("391"), 6.0)
        self.assertGreater(pill.stay_seconds("x" * 200), pill.stay_seconds("x" * 80))
        self.assertEqual(pill.stay_seconds("x" * 5000), 45.0)

    def test_only_one_pill_at_a_time(self) -> None:
        with mock.patch.object(pill, "ONE_PILL_PORT", 0):
            first = pill.only_one()
            self.assertIsNotNone(first)
            assert first is not None
            port = first.getsockname()[1]
            with mock.patch.object(pill, "ONE_PILL_PORT", port):
                self.assertIsNone(pill.only_one())
            first.close()

    def test_the_key_combinations_tried_are_few_and_named(self) -> None:
        self.assertEqual(pill.HOTKEYS[0][0], "Ctrl+Alt+K")
        for words, modifiers, key in pill.HOTKEYS:
            self.assertTrue(words and modifiers & 0x0002 and 0x20 <= key <= 0x5A)

    def test_off_windows_there_is_no_key_combination(self) -> None:
        with mock.patch.object(pill.sys, "platform", "linux"):
            self.assertEqual(pill.watch_hotkey(lambda: None), "")

    def test_the_pill_neither_listens_nor_watches(self) -> None:
        source = Path(pill.__file__).read_text(encoding="utf-8")
        for word in (
            "SetWindowsHookEx",
            "GetAsyncKeyState",
            "pyaudio",
            "ImageGrab",
            "sounddevice",
        ):
            self.assertNotIn(word, source)
        self.assertIn("RegisterHotKey", source)


class ShortcutTests(unittest.TestCase):
    def test_kosi_gets_a_shortcut_of_its_own(self) -> None:
        self.assertEqual(
            desktop_app.shortcut_arguments(None, desktop_app.PILL_MODULE),
            "-m kosika.pill",
        )
        with tempfile.TemporaryDirectory() as folder:
            top = Path(folder)
            (top / "Desktop").mkdir()
            with mock.patch.dict("os.environ", {"USERPROFILE": folder, "APPDATA": ""}):
                self.assertEqual(
                    [
                        p.name
                        for p in desktop_app.shortcut_places(desktop_app.PILL_NAME)
                    ],
                    ["Kosi.lnk"],
                )

    def test_the_command_is_there_and_refuses_a_missing_folder(self) -> None:
        import contextlib
        import io

        said = io.StringIO()
        with (
            mock.patch.object(cli.sys, "argv", ["kosika", "pill", "Z:/nowhere/at/all"]),
            contextlib.redirect_stderr(said),
        ):
            code = cli.main()
        self.assertEqual(code, 2)
        self.assertIn("is not a folder", said.getvalue())

    def test_starting_without_the_assistant_says_so(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            told: list[Any] = []
            with (
                mock.patch.dict("os.environ", {"LOCALAPPDATA": folder}),
                mock.patch.object(pill, "ONE_PILL_PORT", 0),
                mock.patch.object(desktop_app, "_tell", side_effect=told.append),
                mock.patch.object(pill.sys, "stdout", None),
                mock.patch.object(pill.sys, "stderr", None),
            ):
                code = pill.main([str(Path(folder) / "empty")])
            self.assertEqual(code, 2)
            self.assertIn("needs the assistant agent", told[0])


if __name__ == "__main__":
    unittest.main()
