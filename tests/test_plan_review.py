"""A plan is checked before a person sees it: against plain sense and against
the checks its skills carry. A plan found wanting goes back to the model once;
if it is still wrong, the person is shown what is wrong beside the plan."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika.agent import ApprovalRecord
from kosika.durable import ApprovalPending, ExecutionSession
from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.modules import load_program
from kosika.runtime import run
from kosika.skills import (
    SkillCatalogError,
    check_problem,
    load_skill_note,
    load_skill_notes,
    pick_skills,
    plan_problems,
)
from kosika.system_tools import bind_system_tools
from kosika.trace_replay import replay_trace
from tests.test_plan_first import TIDY, finding, picks

LISTS = """# How a list is written
When: todo, checklist
Files: *.txt
Check: every line starts with "- [ ] "

One item per line.
Check: this line is know-how, not a rule.
"""
README = """# What a readme has
When: readme
Files: readme*
Check: starts with "# "
Check: contains "## Files"

Start with a title.
"""


def make(path: str, text: str) -> tuple[str, dict[str, Any]]:
    return ("create_file", {"path": path, "text": text})


def chooses(*names: str) -> ModelResponse:
    return ModelResponse(content=json.dumps({"skills": list(names)}))


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.top = Path(self._dir.name)
        self.skills = self.top / "agent" / "skills"
        self.skills.mkdir(parents=True)
        (self.skills / "lists.md").write_text(LISTS, encoding="utf-8")
        (self.skills / "readme.md").write_text(README, encoding="utf-8")
        self.source = self.top / "agent" / "main.kos"
        self.source.write_text(TIDY, encoding="utf-8")
        self.work = self.top / "work"
        self.work.mkdir()


class NoteTests(Base):
    def test_a_note_can_say_which_files_and_what_they_must_keep(self) -> None:
        skill = load_skill_note(self.skills / "lists.md")
        self.assertEqual(skill.files, ("*.txt",))
        self.assertEqual(skill.checks, ('every line starts with "- [ ] "',))
        # A line in the know-how that begins with the same word stays there.
        self.assertIn("Check: this line is know-how", skill.instructions)

    def test_a_check_that_cannot_be_read_is_refused(self) -> None:
        bad = self.top / "bad.md"
        bad.write_text(
            "# T\nWhen: x\nFiles: *.txt\nCheck: looks nice\n\nBody.\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(SkillCatalogError, "cannot read: 'looks nice'"):
            load_skill_note(bad)

    def test_checks_need_to_say_which_files(self) -> None:
        bad = self.top / "bad.md"
        bad.write_text('# T\nWhen: x\nCheck: contains "a"\n\nBody.\n', encoding="utf-8")
        with self.assertRaisesRegex(SkillCatalogError, "needs a line such as"):
            load_skill_note(bad)

    def test_each_kind_of_rule(self) -> None:
        self.assertIsNone(check_problem('starts with "# "', "\n# Title\n"))
        self.assertIn(
            "must start with", check_problem('starts with "# "', "Title") or ""
        )
        self.assertIsNone(check_problem('contains "main"', "def main():"))
        self.assertIn("must contain", check_problem('contains "main"', "x = 1") or "")
        rule = 'every line starts with "- "'
        self.assertIsNone(check_problem(rule, "- a\n\n- b\n"))
        self.assertIn("every line", check_problem(rule, "- a\nb") or "")


class ProblemTests(Base):
    def problems(self, request: str, *actions: tuple[str, dict[str, Any]]) -> list[str]:
        picked = pick_skills(load_skill_notes(self.skills), request)
        return plan_problems(picked, request, actions)

    def test_a_file_that_keeps_its_skill_has_no_problem(self) -> None:
        self.assertEqual(
            self.problems(
                "a todo and a readme",
                make("todo.txt", "- [ ] one\n- [ ] two\n"),
                make("README.md", "# Notes\nFor notes.\n## Files\n- todo.txt: chores"),
            ),
            [],
        )

    def test_a_file_that_breaks_a_check_is_named_with_the_reason(self) -> None:
        self.assertEqual(
            self.problems("a todo", make("todo.txt", "1. one\n2. two")),
            ["todo.txt: every line must start with '- [ ] ' (How a list is written)"],
        )

    def test_the_skill_that_says_most_about_the_name_applies(self) -> None:
        # readme.txt is a text file and a readme; the readme's checks apply.
        found = self.problems(
            "a todo and a readme", make("readme.txt", "# Notes\n## Files\n- a")
        )
        self.assertEqual(found, [])

    def test_a_skill_that_was_not_picked_checks_nothing(self) -> None:
        self.assertEqual(self.problems("a readme", make("todo.txt", "1. one")), [])

    def test_plain_sense_needs_no_skill(self) -> None:
        found = plan_problems(
            None,
            "something to tick off before a weekend trip",
            [
                make("empty.txt", " \n"),
                make("trip.txt", "Something to tick off before a weekend trip"),
                make("run.py", "def main():\\n    pass"),
                make("a.txt", "a"),
                ("run_python", {"args": ["-c", ""]}),
            ],
        )
        self.assertEqual(
            [line.split(": ")[0] for line in found], ["empty.txt", "trip.txt", "run.py"]
        )
        self.assertIn("would be empty", found[0])
        self.assertIn("only repeats the request", found[1])
        self.assertIn("backslash and n", found[2])

    def test_a_file_written_twice_in_one_plan_is_a_problem(self) -> None:
        found = plan_problems(
            None, "two notes", [make("a.txt", "one"), make("./A.txt", "two")]
        )
        self.assertEqual(len(found), 1)
        self.assertIn("writes this file twice", found[0])


class SendBackTests(Base):
    def attempt(
        self, provider: ScriptedModelProvider, approvals: list[ApprovalRecord]
    ) -> dict[str, Any]:
        program = load_program(self.source)
        return run(
            program,
            "main",
            self.top / "run.jsonl",
            inputs={"msg": {"text": "make a todo for the week"}},
            model_bindings={"thinker": provider},
            tool_bindings=bind_system_tools(program, folder=self.work),
            skills=load_skill_notes(self.skills),
            session=ExecutionSession(
                self.top / "run.sqlite", binding_id="test", approvals=approvals
            ),
        )

    def yes(self, pending: ApprovalPending) -> ApprovalRecord:
        return ApprovalRecord(
            plan_hash=pending.request["plan_hash"],
            action_hash=pending.request["action_hash"],
            approved_by="tester",
        )

    BAD = make("todo.txt", "1. wash up\n2. shop")
    GOOD = make("todo.txt", "- [ ] wash up\n- [ ] shop\n")

    def test_a_plan_that_breaks_its_skill_goes_back_and_the_better_one_is_shown(
        self,
    ) -> None:
        provider = ScriptedModelProvider(
            [chooses(), picks(self.BAD), picks(self.GOOD), finding("Made it.")]
        )
        with self.assertRaises(ApprovalPending) as asked:
            self.attempt(provider, [])
        request = asked.exception.request["arguments"]
        # The person is asked about the second plan only, and nothing is
        # wrong with it.
        self.assertEqual(request["actions"][0]["arguments"], self.GOOD[1])
        self.assertNotIn("warnings", request)
        told = json.loads(provider.requests[2].messages[-1].content)["error"]
        self.assertIn("was not shown to the person because", told)
        self.assertIn("every line must start with '- [ ] '", told)
        self.assertEqual(list(self.work.iterdir()), [])

        self.assertEqual(
            self.attempt(provider, [self.yes(asked.exception)]), {"text": "Made it."}
        )
        self.assertEqual(
            (self.work / "todo.txt").read_text(encoding="utf-8"),
            "- [ ] wash up\n- [ ] shop\n",
        )
        self.assertEqual(len(provider.requests), 4)
        replay_trace(self.top / "run.jsonl")

    def test_a_plan_still_wrong_is_shown_with_what_is_wrong(self) -> None:
        worse = make("todo.txt", "* wash up")
        # A plan goes back twice. The third, still wrong, is shown.
        provider = ScriptedModelProvider(
            [
                chooses(),
                picks(self.BAD),
                picks(make("todo.txt", "+ wash up")),
                picks(worse),
                finding("Made it."),
            ]
        )
        with self.assertRaises(ApprovalPending) as asked:
            self.attempt(provider, [])
        request = asked.exception.request["arguments"]
        self.assertEqual(request["actions"][0]["arguments"], worse[1])
        self.assertEqual(
            request["warnings"],
            ["todo.txt: every line must start with '- [ ] ' (How a list is written)"],
        )
        # It is the person's decision: a yes still carries it out.
        self.attempt(provider, [self.yes(asked.exception)])
        self.assertEqual(
            (self.work / "todo.txt").read_text(encoding="utf-8"), "* wash up"
        )

    def test_a_good_plan_is_not_sent_back(self) -> None:
        provider = ScriptedModelProvider(
            [chooses(), picks(self.GOOD), finding("Made it.")]
        )
        with self.assertRaises(ApprovalPending) as asked:
            self.attempt(provider, [])
        self.assertNotIn("warnings", asked.exception.request["arguments"])
        self.assertEqual(len(provider.requests), 2)

    def test_the_page_and_the_app_carry_the_warnings(self) -> None:
        root = Path(__file__).resolve().parents[1] / "kosika"
        self.assertIn(
            "request.warnings",
            (root / "app_assets" / "index.html").read_text(encoding="utf-8"),
        )
        self.assertIn('"warnings"', (root / "app.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
