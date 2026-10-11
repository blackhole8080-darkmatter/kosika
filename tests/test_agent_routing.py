"""Studio passes a request to the agent that fits it: the model names one of
the agents that exist, and words decide when it cannot."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika import app as studio
from kosika.model import ModelProviderError, ModelResponse, ScriptedModelProvider
from kosika.routing import Candidate, by_words, read_route, route_question

CHATTY = """// {about}

record Msg {{ text: Text }}
record Said {{ text: Text }}

fn echo(msg: Msg) -> Said {{
  return {{text: msg.text}}
}}

workflow main(msg: Msg) -> Said {{
  said = echo(msg)
}}
"""

AGENTS = {
    "researcher": "Researcher: answers questions from your own notes, with the line it used.",
    "coder": "Coder: does one small task in a Python project and runs the tests.",
    "coach": "Coach: quizzes you on any subject and marks your answers.",
}
CANDIDATES = [Candidate(name, name, about) for name, about in AGENTS.items()]


def names(agent: str) -> ModelResponse:
    return ModelResponse(content=json.dumps({"agent": agent}))


class Broken:
    def complete(self, request: Any) -> ModelResponse:
        raise ModelProviderError("the model is not there")


class WordsTests(unittest.TestCase):
    def test_the_agent_sharing_the_most_words_is_found(self) -> None:
        found = by_words(
            CANDIDATES, "add a function to my python project and run tests"
        )
        assert found is not None
        self.assertEqual(found.agent_id, "coder")
        self.assertIn("python", found.why)
        self.assertIn("tests", found.why)

    def test_common_words_do_not_count(self) -> None:
        self.assertIsNone(by_words(CANDIDATES, "what is it and how do you do that"))

    def test_the_model_is_shown_every_agent_and_what_it_does(self) -> None:
        system, ask = route_question(CANDIDATES, "quiz me on rivers")
        self.assertIn("- coach: Coach: quizzes you on any subject", system)
        self.assertIn("- coder: ", system)
        self.assertEqual(ask, "Request: quiz me on rivers")

    def test_only_an_agent_that_exists_can_be_named(self) -> None:
        self.assertEqual(read_route(CANDIDATES, '{"agent": " Coder "}'), CANDIDATES[1])
        for answer in ('{"agent": "admin"}', '{"agent": 3}', "coder", "", None, "[]"):
            with self.subTest(answer=answer):
                self.assertIsNone(read_route(CANDIDATES, answer))


class StudioTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        for name, about in AGENTS.items():
            (self.root / name).mkdir()
            (self.root / name / "main.kos").write_text(
                CHATTY.format(about=about), encoding="utf-8"
            )

    def app(self, provider: Any) -> studio.App:
        return studio.App(self.root, bind_models=lambda program: {"any": provider})

    def test_the_model_chooses_and_the_reason_is_given(self) -> None:
        provider = ScriptedModelProvider([names("coach")])
        found = self.app(provider).route("test me on the planets")
        self.assertEqual((found["agent"], found["title"]), ("coach/main.kos", "coach"))
        self.assertIn("the model chose it", found["why"])
        self.assertTrue(provider.requests[0].json_object)
        self.assertEqual(provider.requests[0].tools, ())

    def test_a_made_up_agent_falls_back_to_words(self) -> None:
        found = self.app(ScriptedModelProvider([names("root")])).route(
            "what do my notes say about rent"
        )
        self.assertEqual(found["agent"], "researcher/main.kos")
        self.assertIn("shares these words", found["why"])

    def test_a_model_that_fails_falls_back_to_words(self) -> None:
        found = self.app(Broken()).route("run the tests of my python project")
        self.assertEqual(found["agent"], "coder/main.kos")

    def test_with_no_model_words_decide(self) -> None:
        found = studio.App(self.root).route("quiz me on a subject")
        self.assertEqual(found["agent"], "coach/main.kos")

    def test_when_nothing_points_anywhere_the_usual_agent_is_used(self) -> None:
        found = studio.App(self.root).route("hmm")
        self.assertIn(found["agent"], {f"{name}/main.kos" for name in AGENTS})
        self.assertIn("nothing pointed to one agent", found["why"])

    def test_one_agent_needs_no_choosing(self) -> None:
        for name in ("coder", "coach"):
            (self.root / name / "main.kos").unlink()
        provider = ScriptedModelProvider([])
        found = self.app(provider).route("anything")
        self.assertEqual(found["agent"], "researcher/main.kos")
        self.assertEqual(provider.requests, [])

    def test_an_empty_request_is_refused(self) -> None:
        with self.assertRaisesRegex(studio.AppError, "Say what you want first"):
            studio.App(self.root).route("   ")

    def test_routing_runs_nothing(self) -> None:
        app = self.app(ScriptedModelProvider([names("coder")]))
        app.route("add a function")
        self.assertEqual(app._jobs, {})

    def test_the_page_has_the_box_and_hands_the_request_over(self) -> None:
        page = (Path(studio.__file__).parent / "app_assets" / "index.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("/api/route", page)
        self.assertIn("Not sure which agent?", page)
        self.assertIn("Passed to ", page)


if __name__ == "__main__":
    unittest.main()
