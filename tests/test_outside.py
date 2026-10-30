"""Asking an outside agent: it works in a copy, and only inside the one
root the owner named."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kosika import outside
from kosika.outside import OutsideError, propose

CALC = "def add(a, b):\n    return a + b\n"


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name) / "mine"
        self.work = self.root / "project"
        (self.work / "notes").mkdir(parents=True)
        (self.work / "calc.py").write_text(CALC, encoding="utf-8")
        (self.work / "notes" / "todo.txt").write_text("one\n", encoding="utf-8")
        (self.work / ".git").mkdir()
        (self.work / ".git" / "config").write_text("secret", encoding="utf-8")
        self.seen: list[Path] = []

    def ask(self, runner, **changes):  # type: ignore[no-untyped-def]
        options = {"within": self.root, "allowed": ("devin",), "runner": runner}
        options.update(changes)
        return propose(
            options.pop("agent", "devin"),
            "add subtract",
            options.pop("folder", self.work),
            **options,
        )

    def editing(self, command: list[str], copy: Path) -> tuple[int, str]:
        self.seen.append(copy)
        (copy / "calc.py").write_text(
            CALC + "\n\ndef subtract(a, b):\n    return a - b\n", encoding="utf-8"
        )
        (copy / "test_calc.py").write_text(
            "from calc import subtract\n", encoding="utf-8"
        )
        (copy / "notes" / "todo.txt").unlink()
        (copy / "logo.png").write_bytes(b"\x89PNG\xff\xfe")
        return 0, "Added subtract and a test."


class ProposeTests(Base):
    def test_it_works_in_a_copy_and_the_folder_is_not_touched(self) -> None:
        found = self.ask(self.editing)
        self.assertEqual(
            (found.agent, found.said), ("devin", "Added subtract and a test.")
        )
        self.assertEqual([c.path for c in found.changes], ["calc.py", "test_calc.py"])
        changed, added = found.changes
        self.assertEqual((changed.before, changed.existed), (CALC, True))
        self.assertIn("def subtract", changed.text)
        self.assertEqual((added.before, added.existed), ("", False))
        # The real folder is exactly as it was, and the copy is gone.
        self.assertEqual((self.work / "calc.py").read_text(encoding="utf-8"), CALC)
        self.assertFalse((self.work / "test_calc.py").exists())
        self.assertTrue((self.work / "notes" / "todo.txt").exists())
        self.assertFalse(self.seen[0].exists())
        self.assertNotEqual(self.seen[0].resolve(), self.work.resolve())

    def test_what_is_not_offered_is_said(self) -> None:
        found = self.ask(self.editing)
        self.assertEqual(
            sorted(found.left_out),
            [
                "it deleted notes/todo.txt, which is not done for it",
                "logo.png is not text or is over 1 MB, so it is not offered",
            ],
        )

    def test_tools_own_folders_are_not_shown_to_it(self) -> None:
        def looking(command: list[str], copy: Path) -> tuple[int, str]:
            return 0, ",".join(sorted(p.name for p in copy.iterdir()))

        self.assertEqual(self.ask(looking).said, "calc.py,notes")

    def test_the_request_tells_it_to_edit_and_not_to_run(self) -> None:
        asked: list[str] = []
        self.ask(lambda command, copy: (asked.append(command[-1]), (0, "ok"))[1])
        self.assertTrue(
            asked[0].startswith("add subtract\n\nWork only by editing files")
        )
        self.assertIn("Do not run programs", asked[0])

    def test_changing_nothing_is_an_empty_list(self) -> None:
        self.assertEqual(
            self.ask(lambda command, copy: (0, "Nothing to do.")).changes, ()
        )


class RefusalTests(Base):
    def refused(self, **changes):  # type: ignore[no-untyped-def]
        with self.assertRaises(OutsideError) as caught:
            self.ask(self.editing, **changes)
        self.assertEqual(self.seen, [], "it must not have been started")
        return str(caught.exception)

    def test_a_folder_outside_the_named_root_is_refused(self) -> None:
        elsewhere = Path(self._dir.name) / "elsewhere"
        elsewhere.mkdir()
        self.assertIn("is shown only folders inside", self.refused(folder=elsewhere))
        self.assertIn("is shown only folders", self.refused(within=None))
        # The root itself, and a folder deep inside it, are fine.
        self.assertTrue(outside.inside(self.root, self.root))
        self.assertTrue(outside.inside(self.work / "notes", self.root))
        self.assertFalse(outside.inside(self.root.parent, self.root))

    def test_an_agent_not_allowed_or_not_known_is_refused(self) -> None:
        self.assertIn("has not been allowed here", self.refused(allowed=()))
        self.assertIn("has not been allowed here", self.refused(agent="omp"))
        self.assertIn(
            "is not an outside agent Kosika knows", self.refused(agent="mystery")
        )

    def test_nothing_to_ask_is_refused(self) -> None:
        with self.assertRaises(OutsideError):
            propose(
                "devin",
                "  ",
                self.work,
                within=self.root,
                allowed=("devin",),
                runner=self.editing,
            )

    def test_too_many_changes_are_refused(self) -> None:
        def flood(command: list[str], copy: Path) -> tuple[int, str]:
            for at in range(outside.MAX_CHANGED_FILES + 1):
                (copy / f"f{at}.txt").write_text("x", encoding="utf-8")
            return 0, "done"

        with self.assertRaises(OutsideError) as caught:
            self.ask(flood)
        self.assertIn("more than the 40 that can be looked at", str(caught.exception))

    def test_an_error_with_nothing_said_is_an_error(self) -> None:
        with self.assertRaises(OutsideError):
            self.ask(lambda command, copy: (1, "  "))


class CommandTests(unittest.TestCase):
    def test_each_known_agent_is_started_to_edit_only(self) -> None:
        devin = outside.COMMANDS["devin"](Path("w"), "do it")
        omp = outside.COMMANDS["omp"](Path("w"), "do it")
        self.assertEqual(devin[-1], "do it")
        self.assertIn("accept-edits", devin)
        self.assertEqual(omp[-1], "do it")
        self.assertEqual(omp[omp.index("--approval-mode") + 1], "write")
        # Nothing that auto-approves everything.
        for command in (devin, omp):
            self.assertFalse({"--auto", "--auto-approve", "yolo"} & set(command))

    def test_only_installed_ones_are_listed(self) -> None:
        self.assertEqual(outside.installed(("mystery",)), [])


if __name__ == "__main__":
    unittest.main()


class CoderTests(unittest.TestCase):
    """The coder asks an outside agent first, and Kosika stays in charge."""

    def setUp(self) -> None:
        import shutil
        import time

        from kosika.app import App
        from kosika.model import ScriptedModelProvider

        self.time = time
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        top = Path(self._dir.name)
        self.mine, self.other = top / "mine", top / "other"
        self.work = self.mine / "project"
        self.work.mkdir(parents=True)
        self.other.mkdir()
        (self.work / "calc.py").write_text(CALC, encoding="utf-8")
        coder = Path(__file__).resolve().parents[1] / "examples" / "agents" / "coder"
        shutil.copytree(
            coder,
            top / "agents" / "coder",
            ignore=shutil.ignore_patterns("memory", "runs"),
        )
        self.started: list[str] = []
        self.reviews: list[str] = []
        self.reader = ScriptedModelProvider([])
        self.Scripted = ScriptedModelProvider

        def make(folder: Path, allowed: tuple[str, ...] = ("devin",)) -> App:
            app = App(
                top / "agents",
                bind_models=lambda program: {
                    "engineer": ScriptedModelProvider([]),
                    "reader": self.reader,
                },
                allow_folder=folder,
                allow_shell=True,
                outside=allowed,
                outside_within=self.mine,
            )
            app._outside_runner = self.runner
            self.addCleanup(app.close)
            return app

        self.make = make

    def runner(self, command: list[str], copy: Path) -> tuple[int, str]:
        self.started.append(command[0])
        if command[-1].startswith("Review a change"):
            # The reviewer is shown the change in place, the old file beside it.
            self.reviews.append(
                f"{'def subtract' in (copy / 'calc.py').read_text(encoding='utf-8')}"
                f"/{(copy / '_before' / 'calc.py').read_text(encoding='utf-8') == CALC}"
                f"/{(copy / '_before' / 'test_calc.py').exists()}"
            )
            return 0, "- [HIGH] calc.py: subtract has no docstring.\nWorking..."
        (copy / "calc.py").write_text(
            CALC + "\n\ndef subtract(a, b):\n    return a - b\n", encoding="utf-8"
        )
        (copy / "test_calc.py").write_text(
            "from calc import add, subtract\n\n\ndef test_subtract():\n    assert subtract(add(1, 2), 1) == 2\n",
            encoding="utf-8",
        )
        return 0, "Added subtract and a test for it."

    def until(self, app, job_id: str, *states: str):  # type: ignore[no-untyped-def]
        deadline = self.time.monotonic() + 90
        while self.time.monotonic() < deadline:
            view = app.poll(job_id, 0)
            if view["state"] in states:
                return view
            self.time.sleep(0.05)
        self.fail(f"never reached {states}: {app.poll(job_id, 0)}")

    def test_its_work_is_shown_as_one_list_and_nothing_changes_before_a_yes(
        self,
    ) -> None:
        app = self.make(self.work)
        job = app.start(
            "coder/main.kos",
            {"text": "add a function subtract(a, b) to calc.py, with a test"},
        )
        asked = self.until(app, job.id, "waiting", "done", "failed")
        self.assertEqual(asked["state"], "waiting", asked)
        self.assertEqual(self.started, ["devin"])
        request = asked["request"]
        self.assertEqual(
            (request["tool"], request["operation"]), ("save_all", "write_all")
        )
        self.assertEqual(
            [f["path"] for f in request["arguments"]["files"]],
            ["calc.py", "test_calc.py"],
        )
        # Nothing has changed yet.
        self.assertEqual((self.work / "calc.py").read_text(encoding="utf-8"), CALC)
        self.assertFalse((self.work / "test_calc.py").exists())

        app.decide(job.id, True)
        # Next it asks to run the tests: a second question, of another kind.
        deadline = self.time.monotonic() + 60
        tests = asked
        while (tests.get("request") or {}).get("kind") != "system:shell":
            self.assertLess(self.time.monotonic(), deadline, tests)
            self.time.sleep(0.05)
            tests = app.poll(job.id, 0)
            self.assertIn(tests["state"], ("waiting", "running"), tests)
        self.assertIn(
            "def subtract", (self.work / "calc.py").read_text(encoding="utf-8")
        )
        app.decide(job.id, True)
        done = self.until(app, job.id, "done", "failed", "refused")
        self.assertEqual(done["state"], "done", done)
        self.assertTrue(
            done["text"].startswith(
                "Asked devin. It said: Added subtract and a test for it."
            )
        )
        self.assertIn("Wrote: calc.py, test_calc.py", done["text"])
        self.assertIn("The tests for calc pass. Only those were run", done["text"])

    def test_a_no_leaves_the_folder_as_it_was(self) -> None:
        app = self.make(self.work)
        job = app.start(
            "coder/main.kos", {"text": "add subtract to calc.py, with a test"}
        )
        self.until(app, job.id, "waiting")
        app.decide(job.id, False)
        self.assertEqual(
            self.until(app, job.id, "refused", "done", "failed")["state"], "refused"
        )
        self.assertEqual((self.work / "calc.py").read_text(encoding="utf-8"), CALC)
        self.assertFalse((self.work / "test_calc.py").exists())

    def test_outside_the_named_root_it_is_never_started(self) -> None:
        (self.other / "calc.py").write_text(CALC, encoding="utf-8")
        app = self.make(self.other)
        job = app.start(
            "coder/main.kos", {"text": "add subtract to calc.py, with a test"}
        )
        self.until(app, job.id, "waiting", "done", "failed", "refused")
        self.assertEqual(self.started, [])

    def test_with_none_allowed_it_is_never_started(self) -> None:
        app = self.make(self.work, allowed=())
        job = app.start(
            "coder/main.kos", {"text": "add subtract to calc.py, with a test"}
        )
        self.until(app, job.id, "waiting", "done", "failed", "refused")
        self.assertEqual(self.started, [])
        self.assertEqual(
            app.overview()["outside"] if "outside" in app.overview() else [], []
        )


class WhatIsShownTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.work = Path(self._dir.name) / "project"
        self.work.mkdir()
        for name, text in (
            ("calc.py", CALC),
            (".env", "KEY=1"),
            (".env.local", "KEY=2"),
            ("deploy.pem", "x"),
            ("id_rsa", "x"),
            ("notes.txt", "hello"),
            ("cache/big.bin", "x"),
        ):
            path = self.work / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def test_files_named_like_secrets_are_never_shown(self) -> None:
        self.assertEqual(
            sorted(outside._files(self.work)), ["cache/big.bin", "calc.py", "notes.txt"]
        )
        for name in (
            ".env",
            "a/.ENV.production",
            "x.pem",
            "id_ed25519.pub",
            "credentials.json",
        ):
            self.assertTrue(outside._secret(name), name)
        for name in ("environment.py", "keys.py", "calc.py"):
            self.assertFalse(outside._secret(name), name)

    def test_in_a_git_project_what_it_ignores_is_not_shown(self) -> None:
        import shutil
        import subprocess

        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        subprocess.run(
            ["git", "init", "-q"], cwd=self.work, check=True, capture_output=True
        )
        (self.work / ".gitignore").write_text("cache/\n", encoding="utf-8")
        shown = sorted(outside._files(self.work))
        # Untracked files that are not ignored are part of the project too.
        self.assertEqual(shown, [".gitignore", "calc.py", "notes.txt"])


class ReviewTests(CoderTests):
    """With two outside agents, the one that did not write reads the change."""

    def start(self, label: str):  # type: ignore[no-untyped-def]
        from kosika.model import ModelResponse

        self.reader = self.Scripted(
            [ModelResponse(content='{"label": "%s", "confidence": 0.9}' % label)]
        )
        app = self.make(self.work, allowed=("devin", "omp"))
        job = app.start(
            "coder/main.kos",
            {"text": "add a function subtract(a, b) to calc.py, with a test"},
        )
        asked = self.until(app, job.id, "waiting", "done", "failed")
        self.assertEqual(asked["state"], "waiting", asked)
        return asked["request"]["arguments"]["note"]

    def test_the_other_agent_reads_the_change_and_the_person_is_told(self) -> None:
        note = self.start("fine")
        self.assertEqual(self.started, ["devin", "omp"])
        # It saw the new file in place, the old one beside it, and no old
        # copy of a file that is new.
        self.assertEqual(self.reviews, ["True/True/False"])
        self.assertEqual(
            note,
            "Read by omp, which found no problem. It said: - [HIGH] calc.py: subtract has no docstring.",
        )
        self.assertEqual((self.work / "calc.py").read_text(encoding="utf-8"), CALC)

    def test_a_problem_found_sends_the_task_back_to_its_author_once(self) -> None:
        note = self.start("problem")
        self.assertEqual(self.started, ["devin", "omp", "devin"])
        self.assertTrue(
            note.startswith(
                "A reviewer (omp) found a problem in the first answer, and the task was done again."
            ),
            note,
        )

    def test_with_one_agent_nobody_reviews_and_the_note_says_so(self) -> None:
        app = self.make(self.work)
        job = app.start(
            "coder/main.kos", {"text": "add subtract to calc.py, with a test"}
        )
        asked = self.until(app, job.id, "waiting", "done", "failed")
        self.assertEqual(
            asked["request"]["arguments"]["note"], "Not read by a second agent."
        )
        self.assertEqual(self.started, ["devin"])


class FailureTests(Base):
    def test_an_agent_out_of_allowance_is_no_answer(self) -> None:
        def spent(command: list[str], copy: Path) -> tuple[int, str]:
            return 1, "Error: Agent error: Your daily usage quota has been exhausted."

        with self.assertRaises(OutsideError) as caught:
            self.ask(spent)
        self.assertEqual(
            str(caught.exception), "devin has used up its allowance for now."
        )

    def test_any_other_error_is_told_with_its_first_line(self) -> None:
        with self.assertRaises(OutsideError) as caught:
            self.ask(lambda command, copy: (2, "starting\nError: not signed in\nmore"))
        self.assertEqual(
            str(caught.exception), "devin stopped with an error: Error: not signed in"
        )

    def test_a_script_is_not_started_through_a_shell(self) -> None:
        from unittest import mock

        with (
            mock.patch.object(outside.sys, "platform", "win32"),
            # Forward slashes, so the name reads the same on every system.
            mock.patch.object(
                outside.shutil, "which", return_value="C:/tools/agent.cmd"
            ),
        ):
            with self.assertRaises(OutsideError) as caught:
                outside._program("agent")
        self.assertIn("installed as a script (agent.cmd)", str(caught.exception))
        with mock.patch.object(outside.shutil, "which", return_value=None):
            with self.assertRaises(OutsideError):
                outside._program("agent")


class NextAgentTests(CoderTests):
    # The tests of the class it borrows its set-up from assume devin
    # answers; here it does not, so they are not run again.
    test_its_work_is_shown_as_one_list_and_nothing_changes_before_a_yes = None  # type: ignore[assignment]
    test_a_no_leaves_the_folder_as_it_was = None  # type: ignore[assignment]
    test_outside_the_named_root_it_is_never_started = None  # type: ignore[assignment]
    test_with_none_allowed_it_is_never_started = None  # type: ignore[assignment]

    def runner(self, command: list[str], copy: Path) -> tuple[int, str]:
        if command[0] == "devin":
            self.started.append("devin")
            return 1, "Error: Your daily usage quota has been exhausted."
        return super().runner(command, copy)

    def test_when_the_first_agent_cannot_answer_the_next_is_asked(self) -> None:
        app = self.make(self.work, allowed=("devin", "omp"))
        job = app.start(
            "coder/main.kos",
            {"text": "add a function subtract(a, b) to calc.py, with a test"},
        )
        asked = self.until(app, job.id, "waiting", "done", "failed")
        self.assertEqual(asked["state"], "waiting", asked)
        # devin was tried and had nothing left; omp did the work. The only
        # reviewer left is devin, which cannot answer, so nobody read it.
        self.assertEqual(self.started[:2], ["devin", "omp"])
        self.assertEqual(
            [f["path"] for f in asked["request"]["arguments"]["files"]],
            ["calc.py", "test_calc.py"],
        )
        self.assertEqual(
            asked["request"]["arguments"]["note"], "Not read by a second agent."
        )


class KosikaNotesTests(unittest.TestCase):
    def test_every_example_in_the_guide_is_real_kosika(self) -> None:
        import re

        from kosika.modules import load_program
        from kosika.testing import run_tests

        notes = outside.kosika_notes()
        blocks = re.findall(r"```kosika\n(.*?)```", notes, flags=re.S)
        self.assertGreaterEqual(len(blocks), 3)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "main.kos"
            path.write_text("\n".join(blocks), encoding="utf-8")
            results = run_tests(load_program(path))
        self.assertTrue(results and all(result.passed for result in results))

    def test_every_method_the_guide_names_exists(self) -> None:
        import re

        from kosika.builtin_methods import METHODS

        notes = outside.kosika_notes()
        section = notes.split("## Methods you can use")[1].split("There is no `len`")[0]
        named = set(re.findall(r"`([a-z_]+)\(", section))
        # `len(text)` is there as what not to write.
        known = {method.name for method in METHODS} | {"or", "len"}
        self.assertEqual(sorted(named - known), [])
        self.assertGreater(len(named), 30)

    def test_the_guide_goes_with_a_request_about_kosika_and_no_other(self) -> None:
        plain = outside.with_notes("add subtract to calc.py", ["calc.py", "README.md"])
        by_file = outside.with_notes("add is_even", ["tools.kos"])
        by_word = outside.with_notes(
            "write a Kosika program that greets", ["notes.txt"]
        )
        self.assertEqual(plain, "add subtract to calc.py")
        self.assertTrue(
            by_file.startswith("add is_even\n\nSome of this is written in Kosika")
        )
        self.assertIn("# Writing Kosika", by_file)
        self.assertIn("# Writing Kosika", by_word)


class SkillTests(CoderTests):
    """The owner's skills go to the outside agent, and it is held to them."""

    test_its_work_is_shown_as_one_list_and_nothing_changes_before_a_yes = None  # type: ignore[assignment]
    test_a_no_leaves_the_folder_as_it_was = None  # type: ignore[assignment]
    test_outside_the_named_root_it_is_never_started = None  # type: ignore[assignment]
    test_with_none_allowed_it_is_never_started = None  # type: ignore[assignment]

    SKILL = (
        "# How a page is styled\n"
        "When: page, website\n"
        "Files: *.css\n"
        'Check: contains "prefers-reduced-motion"\n'
        "\n"
        "Respect people who have turned motion off.\n"
    )

    def runner(self, command: list[str], copy: Path) -> tuple[int, str]:
        self.started.append(command[0])
        self.asked.append(command[-1])
        kind = "@media (prefers-reduced-motion: reduce) { * { transition: none; } }\n"
        css = "body { transition: color 1s; }\n" + (kind if len(self.asked) > 1 else "")
        (copy / "style.css").write_text(css, encoding="utf-8")
        return 0, "Wrote the stylesheet."

    def test_a_skill_reaches_the_agent_and_its_rule_sends_poor_work_back(self) -> None:
        self.asked: list[str] = []
        app = self.make(self.work)
        skills = app.root / "coder" / "skills"
        # Only this skill: the coder's own web skills have rules of their own.
        for kept in skills.glob("*.md"):
            kept.unlink()
        (skills / "web.md").write_text(self.SKILL, encoding="utf-8")
        job = app.start(
            "coder/main.kos", {"text": "make a page look good with style.css"}
        )
        asked = self.until(app, job.id, "waiting", "done", "failed")
        self.assertEqual(asked["state"], "waiting", asked)
        # The skill's own words were part of what the agent was asked.
        self.assertIn("Respect people who have turned motion off.", self.asked[0])
        self.assertIn("How a page is styled", self.asked[0])
        # Its first stylesheet broke the skill's rule, so it was asked again
        # and told which rule, before any person was asked anything.
        self.assertEqual(len(self.asked), 2)
        self.assertIn(
            "style.css: it must contain 'prefers-reduced-motion'", self.asked[1]
        )
        written = asked["request"]["arguments"]["files"]
        self.assertEqual([f["path"] for f in written], ["style.css"])
        self.assertIn("prefers-reduced-motion", written[0]["text"])

    def test_a_task_the_skill_does_not_fit_is_asked_without_it(self) -> None:
        self.asked = []
        app = self.make(self.work)
        (app.root / "coder" / "skills" / "web.md").write_text(
            self.SKILL, encoding="utf-8"
        )
        job = app.start("coder/main.kos", {"text": "tidy style.css"})
        self.until(app, job.id, "waiting", "done", "failed")
        self.assertNotIn("Respect people who have turned motion off.", self.asked[0])
