"""Skills written as notes beside an agent: picked for each request before
the agent begins, given to the model as advice, and recorded in the run."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from kosika.agent import ApprovalRecord
from kosika.app import App
from kosika.durable import ApprovalPending, ExecutionSession
from kosika.model import ModelRequest, ModelResponse, ScriptedModelProvider
from kosika.modules import load_program
from kosika.runtime import run
from kosika.skills import (
    SkillCatalogError,
    load_skill_note,
    load_skill_notes,
    pick_skills,
    skills_text,
)
from kosika.story import read_trace
from kosika.system_tools import bind_system_tools
from kosika.trace_replay import replay_trace
from tests.test_tool_loop import SCOUT, finding, picks

README = """# What a good readme has
When: readme, explain the folder

Start with a title line.
Name every file.
"""
PYTHON = """# How a small Python file is written
When: python, .py, script

Begin with a docstring.
"""


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.top = Path(self._dir.name)
        self.agent = self.top / "agents" / "scout"
        self.skills = self.agent / "skills"
        self.skills.mkdir(parents=True)
        (self.agent / "main.kos").write_text(SCOUT, encoding="utf-8")
        (self.skills / "readme.md").write_text(README, encoding="utf-8")
        (self.skills / "python.md").write_text(PYTHON, encoding="utf-8")
        self.work = self.top / "work"
        self.work.mkdir()
        (self.work / "calc.py").write_text("x = 1\n", encoding="utf-8")


class NoteTests(Base):
    def test_a_note_is_a_title_a_when_line_and_know_how(self) -> None:
        skill = load_skill_note(self.skills / "readme.md")
        self.assertEqual(skill.skill_id, "readme")
        self.assertEqual(skill.summary, "What a good readme has")
        self.assertEqual(skill.keywords, ("readme", "explain the folder"))
        self.assertEqual(
            skill.instructions, "Start with a title line.\nName every file."
        )

    def test_a_folder_that_is_not_there_holds_no_skills(self) -> None:
        self.assertEqual(len(load_skill_notes(self.top / "nowhere")), 0)

    def test_a_note_that_cannot_be_used_says_why(self) -> None:
        for name, text, why in (
            (
                "a.md",
                "# Title\n\nKnow-how with no when line.\n",
                "needs a line such as",
            ),
            ("b.md", "# Title\nWhen: x\n", "has no know-how"),
            ("c.md", "\n\n", "is empty"),
            ("bad name!.md", README, "needs a name of letters"),
        ):
            path = self.top / name
            path.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(SkillCatalogError, why):
                load_skill_note(path)


class PickTests(Base):
    def pick(self, request: str) -> list[tuple[str, str]]:
        return list(pick_skills(load_skill_notes(self.skills), request).reasons)

    def test_a_skill_fits_when_the_request_says_one_of_its_words(self) -> None:
        self.assertEqual(
            self.pick("Write a README for this"),
            [("readme", "the request says: readme")],
        )

    def test_plurals_and_phrases_are_found(self) -> None:
        self.assertEqual(
            self.pick("two scripts, and explain the folders"),
            [
                ("python", "the request says: script"),
                ("readme", "the request says: explain the folder"),
            ],
        )

    def test_the_skill_with_more_of_its_words_comes_first(self) -> None:
        picked = self.pick("a readme and a python script named run.py")
        self.assertEqual([name for name, _ in picked], ["python", "readme"])

    def test_part_of_a_word_does_not_count(self) -> None:
        self.assertEqual(self.pick("describe the scripture"), [])

    def test_no_more_than_the_limit_are_picked(self) -> None:
        for number in range(5):
            (self.skills / f"extra{number}.md").write_text(
                f"# Extra {number}\nWhen: readme\n\nMore.\n", encoding="utf-8"
            )
        self.assertEqual(len(self.pick("a readme")), 3)

    def test_the_model_is_told_skills_add_no_permission(self) -> None:
        text = skills_text(pick_skills(load_skill_notes(self.skills), "a readme"))
        self.assertIn("no new tools and no new permissions", text)
        self.assertIn("## What a good readme has\nStart with a title line.", text)
        self.assertEqual(
            skills_text(pick_skills(load_skill_notes(self.skills), "x")), ""
        )


def chooses(*names: str) -> ModelResponse:
    return ModelResponse(content=json.dumps({"skills": list(names)}))


LOOK = [picks(("look_at", {"path": "calc.py"})), finding("It sets x.", "calc.py")]


class RunTests(Base):
    def go(
        self, text: str, first: list[ModelResponse], with_skills: bool = True
    ) -> ScriptedModelProvider:
        program = load_program(self.agent / "main.kos")
        provider = ScriptedModelProvider([*first, *LOOK])
        run(
            program,
            "main",
            self.top / "run.jsonl",
            inputs={"msg": {"text": text}},
            model_bindings={"thinker": provider},
            tool_bindings=bind_system_tools(program, folder=self.work),
            skills=load_skill_notes(self.skills) if with_skills else None,
        )
        return provider

    def called(self) -> dict[str, Any]:
        return [
            event
            for event in read_trace(self.top / "run.jsonl")
            if event["event"] == "agent_call"
        ][0]

    def system(self, request: ModelRequest) -> str:
        return " ".join(
            message.content or ""
            for message in request.messages
            if message.role == "system"
        )

    def test_the_fitting_skill_is_given_to_the_model_and_recorded(self) -> None:
        provider = self.go("is there a python script here?", [chooses()])
        told = self.system(provider.requests[1])
        self.assertIn("Begin with a docstring.", told)
        self.assertNotIn("Name every file.", told)
        self.assertEqual(
            self.called()["skills"],
            [{"id": "python", "why": "the request says: python, script"}],
        )
        replay_trace(self.top / "run.jsonl")

    def test_the_model_is_asked_which_skills_fit_and_shown_them_all(self) -> None:
        provider = self.go("what is in here?", [chooses()])
        asking = provider.requests[0]
        self.assertTrue(asking.json_object)
        self.assertEqual(asking.tools, ())
        menu = self.system(asking)
        self.assertIn("- python: How a small Python file is written.", menu)
        self.assertIn("- readme: What a good readme has.", menu)
        self.assertEqual(asking.messages[-1].content, "Request: what is in here?")

    def test_a_skill_the_model_chose_is_used_though_no_word_matched(self) -> None:
        provider = self.go("document this place for a newcomer", [chooses("readme")])
        self.assertIn("Name every file.", self.system(provider.requests[1]))
        self.assertEqual(
            self.called()["skills"],
            [{"id": "readme", "why": "the model chose it for this request"}],
        )

    def test_words_and_the_model_together(self) -> None:
        self.go("a python script, documented", [chooses("readme", "python")])
        self.assertEqual(
            [skill["id"] for skill in self.called()["skills"]], ["python", "readme"]
        )

    def test_a_skill_that_does_not_exist_cannot_be_chosen(self) -> None:
        provider = self.go("what is in here?", [chooses("root-access", "readme ")])
        self.assertNotIn("Know-how", self.system(provider.requests[1]))
        self.assertNotIn("skills", self.called())

    def test_an_answer_that_is_not_a_choice_leaves_the_words_to_decide(self) -> None:
        for answer in ("no idea", "[1, 2]", json.dumps({"skills": "python"})):
            with self.subTest(answer=answer):
                self.go("a python script?", [ModelResponse(content=answer)])
                self.assertEqual(
                    [skill["id"] for skill in self.called()["skills"]], ["python"]
                )

    def test_a_request_no_skill_fits_gets_none(self) -> None:
        provider = self.go("what is in here?", [chooses()])
        self.assertNotIn("Know-how", self.system(provider.requests[1]))
        self.assertNotIn("skills", self.called())

    def test_without_a_skills_folder_the_model_is_not_asked_to_choose(self) -> None:
        provider = self.go("is there a python script here?", [], with_skills=False)
        self.assertEqual(len(provider.requests), 2)
        self.assertNotIn("Know-how", self.system(provider.requests[0]))
        self.assertNotIn("skills", self.called())

    def test_an_empty_skills_folder_is_the_same_as_none(self) -> None:
        for path in self.skills.iterdir():
            path.unlink()
        provider = self.go("is there a python script here?", [])
        self.assertEqual(len(provider.requests), 2)


class KeptTests(Base):
    """A step that begins again after a yes keeps the skills it first had."""

    TIDY = (
        SCOUT.replace(
            "record Finding",
            "record NewFile { path: Text text: Text }\nrecord Written { bytes: Number }\nrecord Finding",
        )
        .replace(
            "agent scout {",
            'tool create_file {\n  source system "files"\n  operation: "create"\n'
            "  access: execute\n  input: NewFile\n  output: Written\n}\n\nagent scout {",
        )
        .replace(
            "can: [list_folder, look_at]", "can: [list_folder, look_at, create_file]"
        )
        .replace(
            "  said  = tell(found)\n}", "  said  = tell(found)\n  require approval\n}"
        )
    )

    def test_the_choice_is_made_once_and_kept_across_the_question(self) -> None:
        (self.agent / "main.kos").write_text(self.TIDY, encoding="utf-8")
        program = load_program(self.agent / "main.kos")
        provider = ScriptedModelProvider(
            [
                chooses("readme"),
                picks(("create_file", {"path": "README.md", "text": "# Work"})),
                finding("Made it."),
            ]
        )

        def attempt(approvals: list[ApprovalRecord]) -> dict[str, Any]:
            return run(
                program,
                "main",
                self.top / "run.jsonl",
                inputs={"msg": {"text": "document this place"}},
                model_bindings={"thinker": provider},
                tool_bindings=bind_system_tools(program, folder=self.work),
                skills=load_skill_notes(self.skills),
                session=ExecutionSession(
                    self.top / "run.sqlite", binding_id="test", approvals=approvals
                ),
            )

        with self.assertRaises(ApprovalPending) as asked:
            attempt([])
        yes = ApprovalRecord(
            plan_hash=asked.exception.request["plan_hash"],
            action_hash=asked.exception.request["action_hash"],
            approved_by="tester",
        )
        self.assertEqual(attempt([yes]), {"text": "Made it."})
        # Three model calls in all: the choice, the plan, the answer.
        self.assertEqual(len(provider.requests), 3)
        called = [
            event
            for event in read_trace(self.top / "run.jsonl")
            if event["event"] == "agent_call"
        ][0]
        self.assertEqual([skill["id"] for skill in called["skills"]], ["readme"])
        self.assertTrue((self.work / "README.md").exists())


class StudioTests(Base):
    def test_studio_reads_the_folder_beside_the_agent_and_says_what_it_picked(
        self,
    ) -> None:
        provider = ScriptedModelProvider([chooses("readme"), *LOOK])
        app = App(
            self.top / "agents",
            bind_models=lambda program: {"thinker": provider},
            allow_folder=self.work,
        )
        job = app.start("scout/main.kos", {"text": "is there a python script here?"})
        deadline = time.monotonic() + 30
        view = app.poll(job.id, 0)
        while view["state"] not in ("done", "failed") and time.monotonic() < deadline:
            time.sleep(0.01)
            view = app.poll(job.id, 0)
        self.assertEqual(view["state"], "done", view.get("error"))
        called = [e for e in view["events"] if e["event"] == "agent_call"][0]
        self.assertEqual(called["skills"], ["python", "readme"])
        page = (
            Path(__file__).resolve().parents[1] / "kosika" / "app_assets" / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn("event.skills", page)


if __name__ == "__main__":
    unittest.main()
