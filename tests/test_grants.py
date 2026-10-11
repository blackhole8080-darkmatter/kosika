"""Granting a folder from inside Studio: picked in the computer's own folder
window, confirmed on the page, and never named by the page itself."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from kosika import app as studio
from kosika.grants import too_wide
from kosika.model import ScriptedModelProvider
from tests.test_tool_loop import SCOUT, finding, picks


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.top = Path(self._dir.name).resolve()
        (self.top / "agents" / "scout").mkdir(parents=True)
        (self.top / "agents" / "scout" / "main.kos").write_text(SCOUT, encoding="utf-8")
        self.work = self.top / "work"
        self.work.mkdir()
        (self.work / "calc.py").write_text("x = 1\n", encoding="utf-8")
        self.next_pick = str(self.work)

    def app(self, responses: list[Any] | None = None, **more: Any) -> studio.App:
        provider = ScriptedModelProvider(responses or [])
        return studio.App(
            self.top / "agents",
            bind_models=lambda program: {"thinker": provider},
            pick_folder=lambda: self.next_pick,
            **more,
        )


class TooWideTests(unittest.TestCase):
    def test_a_project_folder_is_fine(self) -> None:
        home = Path.home()
        self.assertIsNone(too_wide(home / "projects" / "notes", home))

    def test_a_whole_drive_is_refused(self) -> None:
        drive = Path(Path.home().anchor)
        self.assertIn("whole drive", too_wide(drive) or "")

    def test_the_user_folder_and_what_holds_it_are_refused(self) -> None:
        home = Path.home()
        self.assertIn("everything of yours", too_wide(home, home) or "")
        if home.parent != Path(home.anchor):
            self.assertIn("everything of yours", too_wide(home.parent, home) or "")

    def test_a_system_folder_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as system:
            with mock.patch.dict("os.environ", {"SystemRoot": system}):
                inside = Path(system) / "System32"
                inside.mkdir()
                self.assertIn("belongs to the system", too_wide(inside) or "")


class PickTests(Base):
    def test_picking_remembers_the_folder_but_grants_nothing(self) -> None:
        app = self.app()
        self.assertEqual(app.pick_folder(), {"folder": str(self.work)})
        self.assertEqual(app.overview()["allow_folder"], "")

    def test_closing_the_window_picks_nothing(self) -> None:
        app = self.app()
        self.next_pick = ""
        self.assertEqual(app.pick_folder(), {"folder": ""})
        with self.assertRaisesRegex(studio.AppError, "Choose a folder first"):
            app.grant({"folder": "picked"})

    def test_a_folder_too_wide_is_refused_at_the_window(self) -> None:
        app = self.app()
        self.next_pick = str(Path.home())
        with self.assertRaisesRegex(studio.AppError, "everything of yours"):
            app.pick_folder()
        with self.assertRaisesRegex(studio.AppError, "Choose a folder first"):
            app.grant({"folder": "picked"})

    def test_something_that_is_not_a_folder_is_refused(self) -> None:
        app = self.app()
        self.next_pick = str(self.work / "calc.py")
        with self.assertRaisesRegex(studio.AppError, "is not a folder"):
            app.pick_folder()


class GrantTests(Base):
    def test_the_picked_folder_is_granted_when_confirmed(self) -> None:
        app = self.app()
        app.pick_folder()
        now = app.grant({"folder": "picked", "shell": True, "sessions": False})
        self.assertEqual(
            now, {"allow_folder": str(self.work), "shell": True, "sessions": False}
        )
        view = app.overview()
        self.assertEqual((view["allow_folder"], view["shell"]), (str(self.work), True))

    def test_the_page_cannot_name_a_folder_of_its_own(self) -> None:
        app = self.app()
        for named in (str(self.work), "C:\\", "..", {"path": str(self.work)}):
            with self.subTest(named=named), self.assertRaises(studio.AppError):
                app.grant({"folder": named})
        self.assertEqual(app.overview()["allow_folder"], "")

    def test_a_pick_is_used_once(self) -> None:
        app = self.app()
        app.pick_folder()
        app.grant({"folder": "picked"})
        app.grant({"folder": "none"})
        with self.assertRaisesRegex(studio.AppError, "Choose a folder first"):
            app.grant({"folder": "picked"})

    def test_without_a_folder_nothing_else_can_be_on(self) -> None:
        app = self.app()
        now = app.grant({"folder": "none", "shell": True, "sessions": True})
        self.assertEqual(now, {"allow_folder": "", "shell": False, "sessions": False})

    def test_keeping_the_folder_changes_only_the_switches(self) -> None:
        app = self.app(allow_folder=self.work)
        now = app.grant({"folder": "keep", "shell": True})
        self.assertEqual((now["allow_folder"], now["shell"]), (str(self.work), True))
        self.assertFalse(app.grant({"folder": "keep"})["shell"])

    def test_sessions_are_refused_with_the_reason_when_they_cannot_work(self) -> None:
        app = self.app(allow_folder=self.work)
        with mock.patch.object(
            studio, "sessions_unavailable", return_value="Sessions need WSL."
        ):
            with self.assertRaisesRegex(studio.AppError, "Sessions need WSL"):
                app.grant({"folder": "keep", "sessions": True})
        self.assertFalse(app.overview()["sessions"])

    def test_an_agent_can_use_the_folder_once_it_is_granted(self) -> None:
        look = [
            picks(("look_at", {"path": "calc.py"})),
            finding("It sets x.", "calc.py"),
        ]
        app = self.app(look)
        app.pick_folder()
        app.grant({"folder": "picked"})
        job = app.start("scout/main.kos", {"text": "what does calc.py do?"})
        deadline = time.monotonic() + 30
        while job.state == "running" and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(job.state, "done", job.error)
        self.assertIn("It sets x.", job.text)

    def test_nothing_changes_while_an_agent_is_working(self) -> None:
        app = self.app(allow_folder=self.work)
        job = studio.Job.__new__(studio.Job)
        job.state = "running"
        app._jobs["x"] = job
        with self.assertRaisesRegex(studio.AppError, "An agent is working"):
            app.grant({"folder": "none"})
        self.assertEqual(app.overview()["allow_folder"], str(self.work))

    def test_two_answers_cannot_overlap(self) -> None:
        import threading

        app = self.app(allow_folder=self.work)
        entered, release = threading.Event(), threading.Event()

        def slow() -> None:
            entered.set()
            release.wait(10)
            return None

        results: list[dict[str, Any]] = []
        with mock.patch.object(studio, "sessions_unavailable", side_effect=slow):
            first = threading.Thread(
                target=lambda: results.append(
                    app.grant({"folder": "keep", "sessions": True})
                )
            )
            first.start()
            self.assertTrue(entered.wait(10))
            second = threading.Thread(
                target=lambda: results.append(app.grant({"folder": "none"}))
            )
            second.start()
            time.sleep(0.2)
            # The second waits for the first, so it decides on what is true
            # after it, and the folder it takes away stays away.
            self.assertEqual(results, [])
            release.set()
            first.join(10)
            second.join(10)
        self.assertEqual(app.overview()["allow_folder"], "")
        self.assertFalse(app.overview()["sessions"])

    def test_nothing_changes_while_a_session_is_open(self) -> None:
        app = self.app(allow_folder=self.work)
        app._sessions["x"] = mock.Mock()
        with self.assertRaisesRegex(studio.AppError, "A session is open"):
            app.grant({"folder": "none"})


class PageTests(unittest.TestCase):
    def test_the_page_offers_the_choice_and_says_who_can_make_it(self) -> None:
        page = (Path(studio.__file__).parent / "app_assets" / "index.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("/api/grant/pick", page)
        self.assertIn("Choose a folder", page)
        self.assertIn("only a person sitting here can grant one", page)
        self.assertNotIn("--allow-folder FOLDER", page)


if __name__ == "__main__":
    unittest.main()
