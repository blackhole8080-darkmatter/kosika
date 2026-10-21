"""MayaOS, the app beside Studio: it reads Linux under WSL only with fixed
commands and only a distribution the person named, it finds Studio
through the note Studio leaves, and it opens only Kosika's own apps."""

from __future__ import annotations

import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kosika import desktop_app, mayaos, wsl
from kosika.app import App

ANSWER = """kernel=Linux 6.18.33.1-microsoft-standard-WSL2
uptime=up 2 hours, 5 minutes
load=0.10 0.20 0.30
memory=629 of 7575 MB used
disk=4.2G of 1007G used (1%) on /
upgrades=149
top=systemd 20.0%
top=python3 3.1%
"""


class LinuxTests(unittest.TestCase):
    def test_status_runs_only_the_fixed_script_and_reads_its_lines(self) -> None:
        seen: list[list[str]] = []

        def runner(command: list[str], timeout: float) -> tuple[int, str, str]:
            seen.append(command)
            return 0, ANSWER, ""

        found = wsl.status("Ubuntu", runner)
        self.assertEqual(seen, [["wsl.exe", "-d", "Ubuntu", "--exec", "sh", "-c", wsl.STATUS_SCRIPT]])
        self.assertEqual(found["memory"], "629 of 7575 MB used")
        self.assertEqual(found["upgrades"], "149")
        self.assertEqual(found["top"], ["systemd 20.0%", "python3 3.1%"])

    def test_a_missing_line_reads_unknown_and_a_failure_is_said(self) -> None:
        self.assertEqual(wsl.status("Ubuntu", lambda c, t: (0, "kernel=Linux\n", ""))["disk"], "unknown")
        with self.assertRaises(wsl.WslError):
            wsl.status("Nowhere", lambda c, t: (1, "", "There is no distribution with the supplied name."))

    def test_wsl_names_come_back_from_its_utf16_listing(self) -> None:
        listing = "Ubuntu\r\nDebian\r\n".encode("utf-16-le")
        self.assertEqual(wsl.distributions(lambda c, t: (0, wsl._text(listing), "")), ["Ubuntu", "Debian"])


class MayaTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.maya = mayaos.Maya(linux=["Ubuntu"], settings=self.root / "mayaos.json")

    def test_only_a_named_distribution_is_read(self) -> None:
        with mock.patch.object(wsl, "status", return_value={"distro": "Ubuntu"}) as status:
            self.assertEqual(self.maya.linux_status("Ubuntu"), {"distro": "Ubuntu"})
            with self.assertRaises(mayaos.MayaError):
                self.maya.linux_status("Debian")
        status.assert_called_once_with("Ubuntu")

    def test_letting_maya_read_a_linux_is_kept_and_can_be_taken_back(self) -> None:
        with (
            mock.patch.object(wsl, "distributions", return_value=["Ubuntu", "Debian"]),
            mock.patch.object(mayaos, "windows", return_value={}),
            mock.patch.object(mayaos, "reachable", return_value=""),
        ):
            self.maya.name_linux({"name": "Debian", "on": True})
            self.assertEqual(self.maya.named_linux(), ["Ubuntu", "Debian"])
            self.assertEqual(json.loads((self.root / "mayaos.json").read_text())["linux"], ["Debian"])
            self.maya.name_linux({"name": "Debian", "on": False})
            self.assertEqual(self.maya.named_linux(), ["Ubuntu"])
            with self.assertRaises(mayaos.MayaError):
                self.maya.name_linux({"name": "Fedora", "on": True})
            with self.assertRaises(mayaos.MayaError):
                self.maya.name_linux({"name": "Ubuntu", "on": False})

    def test_only_kosikas_own_apps_are_opened(self) -> None:
        started: list[list[str]] = []
        with mock.patch.object(mayaos, "reachable", return_value=""):
            self.assertEqual(mayaos.open_app("studio", started.append), "started")
        self.assertEqual(started[0][1:], ["-m", "kosika.desktop_app"])
        with self.assertRaises(mayaos.MayaError):
            mayaos.open_app("notepad", started.append)
        with self.assertRaises(mayaos.MayaError):
            self.maya.open({"app": "mayaos"})

    def test_the_page_says_how_to_name_services(self) -> None:
        self.assertIn("KOSIKA_SERVICES", mayaos.PAGE.read_text(encoding="utf-8"))
        self.assertNotIn("innerHTML", mayaos.PAGE.read_text(encoding="utf-8"))


class MindTests(unittest.TestCase):
    """Maya's mind is mayaos/maya.kos; the app only runs it."""

    def test_readings_are_shares_and_counts(self) -> None:
        seen = mayaos.reading("Ubuntu", "linux", "629 of 7575 MB used", "4.2G of 1007G used (1%) on /", "149", True)
        self.assertEqual(seen, {"system": "Ubuntu", "kind": "linux", "memory": 0.083, "disk": 0.0042, "updates": 149, "awake": True})
        self.assertEqual(mayaos.reading("Debian", "linux", None, "unknown", "unknown", False)["disk"], -1)

    def test_maya_is_run_as_a_kosika_program_with_only_the_services_grant(self) -> None:
        seen: list[list[str]] = []

        def runner(command: list[str]) -> object:
            seen.append(command)
            return mock.Mock(returncode=0, stdout="{'title': 'Hi', 'text': 'x', 'notices': [], 'suggest': [], 'open': ''}\nTrace: t", stderr="")

        said = mayaos.think({"text": "home"}, Path("aios.services"), runner)
        self.assertEqual(said["title"], "Hi")
        command = seen[0]
        self.assertEqual(command[1:5], ["-m", "kosika.cli", "run", str(mayaos.MIND)])
        self.assertEqual(command[command.index("--services") + 1], "aios.services")
        self.assertNotIn("--allow-folder", command)
        self.assertNotIn("--allow-site", command)

    def test_the_mind_passes_its_own_tests(self) -> None:
        from kosika.cli import main

        with mock.patch("sys.argv", ["kosika", "test", str(mayaos.MIND)]), mock.patch("sys.stdout"):
            self.assertEqual(main(), 0)


class FindingEachOtherTests(unittest.TestCase):
    def test_a_running_app_is_found_by_its_note_and_a_gone_one_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as folder, socket.socket() as listening:
            listening.bind(("127.0.0.1", 0))
            listening.listen()
            port = listening.getsockname()[1]
            desktop_app.announce("studio", f"http://127.0.0.1:{port}/?token=t", Path(folder))
            self.assertEqual(desktop_app.reachable("studio", Path(folder)), f"http://127.0.0.1:{port}/?token=t")
            self.assertEqual(desktop_app.reachable("mayaos", Path(folder)), "")
        with tempfile.TemporaryDirectory() as folder:
            desktop_app.announce("studio", "http://127.0.0.1:1/?token=t", Path(folder))
            self.assertEqual(desktop_app.reachable("studio", Path(folder)), "")

    def test_studio_opens_mayaos(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            app = App(Path(folder))
            with mock.patch.object(mayaos, "open_app", return_value="started") as opened:
                self.assertEqual(app.open_mayaos(), {"did": "started"})
            opened.assert_called_once_with("mayaos")


if __name__ == "__main__":
    unittest.main()
