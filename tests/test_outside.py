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

        def make(folder: Path, allowed: tuple[str, ...] = ("devin",)) -> App:
            app = App(
                top / "agents",
                bind_models=lambda program: {"engineer": ScriptedModelProvider([])},
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
        self.assertIn("The tests pass.", done["text"])

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
