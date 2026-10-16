"""The assistant example and the way Studio speaks its answers."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from kosika import app as studio
from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.modules import load_program
from kosika.testing import run_tests

ROOT = Path(__file__).resolve().parents[1]
ASSISTANT = ROOT / "examples" / "agents" / "assistant" / "main.kos"


class AssistantTests(unittest.TestCase):
    def test_its_own_tests_pass(self) -> None:
        results = run_tests(load_program(ASSISTANT))
        self.assertGreaterEqual(len(results), 11)
        self.assertEqual([r.name for r in results if not r.passed], [])

    def test_what_it_reaches_is_listed_and_none_of_it_changes_the_world(self) -> None:
        program = load_program(ASSISTANT)
        sources = sorted({tool.source for tool in program.tools})
        # A clock, its two memories, the other agents here, an outside agent
        # to put a question to, which is bound only if the owner allowed one,
        # and the phone, reached only at the one address the owner names.
        self.assertEqual(
            sources,
            [
                "memory:books",
                "memory:talks",
                "system:agents",
                "system:notify",
                "system:outside",
                "world:clock",
            ],
        )
        outside = [tool for tool in program.tools if tool.source == "system:outside"]
        self.assertEqual([tool.operation for tool in outside], ["answer"])
        # Handing a request to another agent changes nothing by itself: no
        # tool of the assistant's own may change the world.
        self.assertEqual({tool.access for tool in program.tools} & {"execute"}, set())

    def test_studio_opens_on_it(self) -> None:
        self.assertEqual(
            studio.App(ROOT / "examples" / "agents").assistant(), "assistant/main.kos"
        )


class OutsideMindTests(unittest.TestCase):
    """An open question may go to an outside agent the owner allowed. What
    Kosi keeps about the person never goes with it."""

    QUESTION = "what quantum computing projects should we build together"

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name) / "agents"
        (self.root / "assistant").mkdir(parents=True)
        shutil.copyfile(ASSISTANT, self.root / "assistant" / "main.kos")
        self.asked: list[str] = []
        self.shown: list[list[str]] = []
        self.says = "Build a simulator first, because everything else needs one."
        self.voice = ScriptedModelProvider([])

    def make(self, allowed: tuple[str, ...]) -> studio.App:
        made = studio.App(
            self.root,
            bind_models=lambda program: {"voice": self.voice},
            outside=allowed,
        )
        made._outside_runner = self.runner
        self.addCleanup(made.close)
        return made

    def runner(self, command: list[str], folder: Path) -> tuple[int, str]:
        self.asked.append(command[-1])
        self.shown.append(sorted(path.name for path in folder.iterdir()))
        return 0, self.says + "\nWorking...\n"

    def local(self, *texts: str) -> None:
        self.voice = ScriptedModelProvider(
            [ModelResponse(content=json.dumps({"text": text})) for text in texts]
        )

    def test_an_open_question_is_answered_outside_and_says_so(self) -> None:
        app = self.make(("omp",))
        for text in (
            "call me Aryan",
            "remember that my locker is 14",
            "remind me to stretch",
        ):
            app.run("assistant/main.kos", {"text": text})
        said = app.run("assistant/main.kos", {"text": self.QUESTION})
        self.assertEqual(said["text"], self.says + "\n(answered by omp)")
        # It was shown an empty folder and the question, and none of what
        # Kosi keeps: not the name, the remembered fact or the reminder.
        self.assertEqual(self.shown, [[]])
        self.assertIn("The question: " + self.QUESTION, self.asked[0])
        for private in ("Aryan", "locker", "stretch"):
            self.assertNotIn(private, self.asked[0])
        # The next open question takes the last one and its answer along.
        app.run("assistant/main.kos", {"text": "which of those comes first and why"})
        self.assertIn("You: " + self.QUESTION, self.asked[1])
        self.assertIn("Kosi: " + self.says, self.asked[1])
        for private in ("Aryan", "locker", "stretch"):
            self.assertNotIn(private, self.asked[1])

    def test_a_question_about_the_person_comes_back_to_its_own_model(self) -> None:
        self.says = "LOCAL"
        self.local("Your locker is number 14.")
        app = self.make(("omp",))
        said = app.run(
            "assistant/main.kos", {"text": "what is the number of my locker"}
        )
        self.assertEqual(said["text"], "Your locker is number 14.")
        self.assertEqual(len(self.asked), 1)
        # What its own model answered, perhaps from a remembered fact, does
        # not go along with the next question that is put outside.
        self.says = "A simulator."
        app.run("assistant/main.kos", {"text": self.QUESTION})
        self.assertNotIn("14", self.asked[1])
        self.assertNotIn("locker", self.asked[1])

    def test_short_words_and_what_code_answers_never_go_outside(self) -> None:
        self.local("I am well.")
        app = self.make(("omp",))
        for text in (
            "how are you",
            "what is 17 times 23",
            "what time is it right now please",
            "my reminders",
            "thanks kosi",
        ):
            app.run("assistant/main.kos", {"text": text})
        self.assertEqual(self.asked, [])

    def test_with_none_allowed_nothing_is_asked_outside(self) -> None:
        self.local("A simulator.")
        said = self.make(()).run("assistant/main.kos", {"text": self.QUESTION})
        self.assertEqual(said["text"], "A simulator.")
        self.assertEqual(self.asked, [])

    def test_an_agent_that_fails_leaves_the_answer_to_its_own_model(self) -> None:
        self.local("A simulator.")
        app = self.make(("omp",))
        app._outside_runner = lambda command, folder: (1, "error: quota exceeded")
        said = app.run("assistant/main.kos", {"text": self.QUESTION})
        self.assertEqual(said["text"], "A simulator.")


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
