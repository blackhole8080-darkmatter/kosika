"""Sessions: an agent works in a copy of the folder, behind a wall."""

from __future__ import annotations

import subprocess
import tempfile
import time
import unittest
import unittest.mock
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from kosika import app as studio
from kosika import sessions
from kosika.modules import load_program
from kosika.sessions import Change, Session, SessionError, linux_path, wall
from kosika.system_tools import bind_system_tools

SOURCE = """
// Makes a note by starting a program.
record Msg { text: Text }
record Args { args: Text[] }
record Ran { exit_code: Number stdout: Text stderr: Text }
record Note { path: Text text: Text }
record Wrote { bytes: Number }
record Said { text: Text }

tool run_python {
  source system "shell"
  operation: "python3"
  access: execute
  input: Args
  output: Ran
}

tool write_note {
  source system "files"
  operation: "write"
  access: execute
  input: Note
  output: Wrote
}

agent hands {
  can: [run_python, write_note]
  max_steps: 1
}

fn told(ran: Ran, wrote: Wrote) -> Said {
  return {text: f"{ran.stdout.trim()} and {wrote.bytes} bytes"}
}

workflow main(msg: Msg) -> Said {
  require approval
  ran   = execute hands.run_python(["-c", msg.text])
  wrote = execute hands.write_note("notes/made.txt", "made by the agent")
  said  = told(ran, wrote)
}
"""


class Recorder:
    """Stands in for the wall: records what would be run, and answers."""

    def __init__(self, stdout: str = "42\n", code: int = 0) -> None:
        self.commands: list[list[str]] = []
        self.stdout, self.code = stdout, code

    def __call__(
        self, command: Sequence[str], timeout: float
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(list(command))
        return subprocess.CompletedProcess(list(command), self.code, self.stdout, "")


class SessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.real = self.root / "real"
        (self.real / "sub").mkdir(parents=True)
        (self.real / "node_modules").mkdir()
        (self.real / "calc.py").write_text("print(6 * 7)\n", encoding="utf-8")
        (self.real / "sub" / "old.txt").write_text("old\n", encoding="utf-8")
        (self.real / "node_modules" / "big.js").write_text("x", encoding="utf-8")
        self.fake = Recorder()
        self.session = Session(self.real, self.root / "home", launcher=self.fake)

    def test_the_session_starts_as_a_copy(self) -> None:
        self.assertEqual(
            (self.session.work / "calc.py").read_text(encoding="utf-8"),
            "print(6 * 7)\n",
        )
        self.assertTrue((self.session.work / "sub" / "old.txt").is_file())
        self.assertFalse((self.session.work / "node_modules").exists())
        self.assertEqual(self.session.changes(), [])

    def test_the_wall_shares_nothing_but_the_copy(self) -> None:
        self.session.run("python3", ["calc.py"])
        command = self.fake.commands[0]
        start = command.index("bwrap")
        walled = command[start:]
        self.assertIn("--unshare-all", walled)
        self.assertNotIn("--share-net", walled)
        self.assertIn("--die-with-parent", walled)
        self.assertIn("--clearenv", walled)
        # Exactly one place is writable, and it is the copy.
        binds = [walled[i + 1] for i, part in enumerate(walled) if part == "--bind"]
        self.assertEqual(binds, [linux_path(self.session.work)])
        self.assertEqual(walled[-3:], ["--", "python3", "calc.py"])
        self.assertEqual(wall("/w", "git", ["status"])[-3:], ["--", "git", "status"])

    def test_only_one_program_can_be_named(self) -> None:
        for name in ("", "python3 -c", "/bin/sh", "..\\cmd"):
            with self.subTest(name=name), self.assertRaises(SessionError):
                self.session.run(name, [])
        self.assertEqual(self.fake.commands, [])

    def test_changes_are_listed_and_nothing_is_real_until_applied(self) -> None:
        (self.session.work / "made.txt").write_text("made", encoding="utf-8")
        (self.session.work / "calc.py").write_bytes(b"print(1)\n")
        (self.session.work / "sub" / "old.txt").unlink()
        self.assertEqual(
            self.session.changes(),
            [
                Change("calc.py", "changed", 9),
                Change("made.txt", "new", 4),
                Change("sub/old.txt", "removed", 0),
            ],
        )
        self.assertFalse((self.real / "made.txt").exists())
        self.assertEqual(
            (self.real / "calc.py").read_text(encoding="utf-8"), "print(6 * 7)\n"
        )
        applied = self.session.apply(["made.txt"])
        self.assertEqual([change.path for change in applied], ["made.txt"])
        self.assertTrue((self.real / "sub" / "old.txt").is_file())
        self.session.apply()
        self.assertEqual((self.real / "calc.py").read_bytes(), b"print(1)\n")
        self.assertFalse((self.real / "sub" / "old.txt").exists())
        self.assertEqual(self.session.changes(), [])

    def test_throwing_a_session_away_leaves_the_real_folder_alone(self) -> None:
        (self.session.work / "calc.py").write_text("ruined", encoding="utf-8")
        self.session.discard()
        self.assertFalse(self.session.root.exists())
        self.assertEqual(
            (self.real / "calc.py").read_text(encoding="utf-8"), "print(6 * 7)\n"
        )

    def test_a_folder_too_large_is_refused(self) -> None:
        with unittest.mock.patch.object(sessions, "MAX_COPY_FILES", 1):
            with self.assertRaisesRegex(SessionError, "too large for a session"):
                Session(self.real, self.root / "other", launcher=self.fake)

    def test_a_program_that_runs_too_long_is_reported(self) -> None:
        def slow(command: Sequence[str], timeout: float) -> Any:
            raise subprocess.TimeoutExpired(list(command), timeout)

        session = Session(self.real, self.root / "slow", launcher=slow)
        with self.assertRaisesRegex(SessionError, "longer than 2 seconds"):
            session.run("sleep", ["9"], timeout=2)

    def test_tools_work_in_the_copy_not_the_real_folder(self) -> None:
        (self.root / "main.kos").write_text(SOURCE, encoding="utf-8")
        program = load_program(self.root / "main.kos")
        tools = bind_system_tools(
            program, folder=self.real, allow_shell=True, session=self.session
        )
        by_name = {tool.name: tool for tool in program.tools}
        wrote = tools["write_note"](
            by_name["write_note"], {"path": "notes/made.txt", "text": "hi"}
        )
        self.assertEqual(wrote, {"bytes": 2})
        self.assertFalse((self.real / "notes").exists())
        self.assertTrue((self.session.work / "notes" / "made.txt").is_file())
        ran = tools["run_python"](by_name["run_python"], {"args": ["calc.py"]})
        self.assertEqual(ran, {"exit_code": 0, "stdout": "42\n", "stderr": ""})
        self.assertIn("bwrap", self.fake.commands[0])


class StudioSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.real = self.root / "real"
        self.real.mkdir()
        (self.real / "calc.py").write_text("print(6 * 7)\n", encoding="utf-8")
        agent = self.root / "agents" / "maker"
        agent.mkdir(parents=True)
        (agent / "main.kos").write_text(SOURCE, encoding="utf-8")
        self.fake = Recorder()

        def make(folder: Path, home: Path, **extra: Any) -> Session:
            return Session(folder, home, launcher=self.fake, **extra)

        patcher = unittest.mock.patch.object(studio, "Session", make)
        patcher.start()
        self.addCleanup(patcher.stop)

    def app(self, in_session: bool) -> studio.App:
        return studio.App(
            self.root / "agents",
            bind_models=lambda program: {},
            allow_folder=self.real,
            allow_shell=True,
            in_session=in_session,
            session_home=self.root / "home",
        )

    def go(self, app: studio.App) -> studio.Job:
        job = app.start("maker/main.kos", {"text": "print(42)"})
        while job.state == "running":
            time.sleep(0.01)
        return job

    def test_in_a_session_the_agent_is_not_stopped_step_by_step(self) -> None:
        app = self.app(True)
        job = self.go(app)
        self.assertEqual(job.state, "done", job.error)
        self.assertEqual(job.text, "42 and 17 bytes")
        allowed = [e["tool"] for e in job.events if e["event"] == "session_allowed"]
        self.assertEqual(allowed, ["run_python", "write_note"])
        # Nothing has reached the real folder.
        self.assertFalse((self.real / "notes").exists())
        waiting = app.session("maker/main.kos")
        self.assertEqual(
            waiting["changes"],
            [{"path": "notes/made.txt", "kind": "new", "bytes": 17}],
        )
        kept = app.end_session("maker/main.kos", True)
        self.assertEqual(kept, {"kept": True, "applied": ["notes/made.txt"]})
        self.assertEqual(
            (self.real / "notes" / "made.txt").read_text(encoding="utf-8"),
            "made by the agent",
        )
        self.assertFalse(app.session("maker/main.kos")["open"])

    def test_a_session_thrown_away_changes_nothing(self) -> None:
        app = self.app(True)
        self.go(app)
        self.assertEqual(
            app.end_session("maker/main.kos", False), {"kept": False, "applied": []}
        )
        self.assertFalse((self.real / "notes").exists())
        with self.assertRaises(studio.AppError):
            app.end_session("maker/main.kos", False)

    def test_without_sessions_each_change_still_waits_for_a_yes(self) -> None:
        app = self.app(False)
        job = self.go(app)
        self.assertEqual(job.state, "waiting")
        self.assertEqual(job.request["tool"], "run_python")
        app.decide(job.id, False)
        while job.state == "waiting":
            time.sleep(0.01)
        self.assertEqual(job.state, "refused")
        self.assertFalse(app.session("maker/main.kos")["open"])
        self.assertFalse(app.overview()["sessions"])


class RealWallTests(unittest.TestCase):
    """Run only where the wall exists: Linux, or Windows with WSL, with bubblewrap."""

    def setUp(self) -> None:
        problem = sessions.unavailable()
        if problem is not None:
            self.skipTest(problem)
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        (self.root / "real").mkdir()
        (self.root / "real" / "calc.py").write_text("print(6 * 7)\n", encoding="utf-8")
        self.session = Session(self.root / "real", self.root / "home")

    def test_a_program_sees_the_copy_and_nothing_else(self) -> None:
        code = (
            "import os, socket\n"
            "print(sorted(os.listdir('/work')), os.path.exists('/mnt'), "
            "os.path.exists('/home'))\n"
            "open('made.txt', 'w').write('x')\n"
            "try:\n"
            "    socket.create_connection(('1.1.1.1', 53), timeout=3)\n"
            "    print('network open')\n"
            "except OSError:\n"
            "    print('network closed')\n"
            "try:\n"
            "    open('/usr/kosika_probe', 'w')\n"
            "    print('system writable')\n"
            "except OSError:\n"
            "    print('system read-only')\n"
        )
        ran = self.session.run("python3", ["-c", code])
        self.assertEqual(ran["exit_code"], 0, ran)
        self.assertEqual(
            str(ran["stdout"]).splitlines(),
            ["['calc.py'] False False", "network closed", "system read-only"],
        )
        self.assertEqual(self.session.changes(), [Change("made.txt", "new", 1)])
        self.assertFalse((self.root / "real" / "made.txt").exists())


if __name__ == "__main__":
    unittest.main()
