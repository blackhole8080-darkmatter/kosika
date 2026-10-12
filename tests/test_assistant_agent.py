"""The assistant example and the way Studio speaks its answers."""

from __future__ import annotations

import unittest
from pathlib import Path

from kosika import app as studio
from kosika.modules import load_program
from kosika.testing import run_tests

ROOT = Path(__file__).resolve().parents[1]
ASSISTANT = ROOT / "examples" / "agents" / "assistant" / "main.kos"


class AssistantTests(unittest.TestCase):
    def test_its_own_tests_pass(self) -> None:
        results = run_tests(load_program(ASSISTANT))
        self.assertGreaterEqual(len(results), 11)
        self.assertEqual([r.name for r in results if not r.passed], [])

    def test_it_reaches_a_clock_its_memory_and_the_other_agents_only(self) -> None:
        program = load_program(ASSISTANT)
        sources = sorted({tool.source for tool in program.tools})
        self.assertEqual(sources, ["memory:books", "system:agents", "world:clock"])
        # Handing a request to another agent changes nothing by itself: no
        # tool of the assistant's own may change the world.
        self.assertEqual({tool.access for tool in program.tools} & {"execute"}, set())

    def test_studio_opens_on_it(self) -> None:
        self.assertEqual(
            studio.App(ROOT / "examples" / "agents").assistant(), "assistant/main.kos"
        )


class SpeakingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.page = (
            Path(studio.__file__).parent / "app_assets" / "index.html"
        ).read_text(encoding="utf-8")

    def test_only_a_voice_on_this_computer_is_used(self) -> None:
        self.assertIn("voice.localService", self.page)
        self.assertIn(
            "Speak answers aloud, with a voice that is on this computer", self.page
        )

    def test_speaking_is_off_until_chosen_and_the_choice_is_kept(self) -> None:
        self.assertIn('localStorage.getItem("kosika-speak") === "on"', self.page)
        self.assertIn("speechSynthesis.cancel()", self.page)

    def test_the_page_does_not_listen(self) -> None:
        # Hearing is a separate decision; nothing here opens the microphone.
        for word in ("SpeechRecognition", "getUserMedia", "webkitSpeech"):
            self.assertNotIn(word, self.page)


class OrbTests(unittest.TestCase):
    def setUp(self) -> None:
        self.page = (
            Path(studio.__file__).parent / "app_assets" / "index.html"
        ).read_text(encoding="utf-8")

    def test_the_orb_has_a_look_for_each_thing_it_is_doing(self) -> None:
        for state in (".orb.thinking", ".orb.speaking", ".orb.due"):
            self.assertIn(state, self.page)
        self.assertIn('mood("thinking")', self.page)
        self.assertIn("The assistant is ", self.page)

    def test_stillness_is_respected(self) -> None:
        # One rule stops every animation for a person who asked for less motion.
        self.assertIn("prefers-reduced-motion: reduce", self.page)

    def test_it_greets_once_and_offers_things_to_tap(self) -> None:
        self.assertIn('send("hello", true)', self.page)
        self.assertIn("view.result.suggest", self.page)

    def test_a_due_reminder_is_said_once(self) -> None:
        self.assertIn("kosika-told", self.page)
        self.assertIn('"Reminder: " + item.what', self.page)

    def test_the_orb_draws_nothing_from_outside(self) -> None:
        for word in ("http://", "https://", "innerHTML", "<img"):
            self.assertNotIn(word, self.page)
        # The only things the page fetches by address are Kosika's own two
        # typefaces, from this computer.
        self.assertEqual(self.page.count("url("), 2)
        self.assertEqual(self.page.count('url("/fonts/'), 2)


if __name__ == "__main__":
    unittest.main()
