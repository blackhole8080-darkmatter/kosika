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
        self.assertGreaterEqual(len(results), 7)
        self.assertEqual([r.name for r in results if not r.passed], [])

    def test_it_is_granted_nothing_but_a_clock_and_its_memory(self) -> None:
        program = load_program(ASSISTANT)
        sources = sorted({tool.source for tool in program.tools})
        self.assertEqual(sources, ["memory:books", "world:clock"])

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


if __name__ == "__main__":
    unittest.main()
