"""Sessions in a named WSL system, such as Kali."""

from __future__ import annotations

import unittest
from unittest import mock

from kosika import cli, sessions


class SystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(sessions.use_system, None)

    def test_the_named_system_is_handed_to_wsl_as_one_argument(self) -> None:
        with mock.patch.object(sessions.sys, "platform", "win32"):
            self.assertEqual(sessions._prefix(), ["wsl.exe", "--"])
            self.assertEqual(sessions.system_name(), "the default WSL system")
            sessions.use_system("kali-linux", ["Ubuntu", "kali-linux"])
            self.assertEqual(sessions._prefix(), ["wsl.exe", "-d", "kali-linux", "--"])
            self.assertEqual(sessions.system_name(), "kali-linux")
            sessions.use_system(None)
            self.assertEqual(sessions._prefix(), ["wsl.exe", "--"])

    def test_a_system_wsl_does_not_have_is_refused_and_nothing_changes(self) -> None:
        for name in ("debian", "kali-linux -- rm", "", "--exec"):
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(
                    sessions.SessionError, "WSL has no system named"
                ),
            ):
                sessions.use_system(name, ["Ubuntu", "kali-linux"])
        with mock.patch.object(sessions.sys, "platform", "win32"):
            self.assertEqual(sessions._prefix(), ["wsl.exe", "--"])

    def test_off_windows_there_is_no_prefix_and_no_list(self) -> None:
        with mock.patch.object(sessions.sys, "platform", "linux"):
            self.assertEqual(sessions._prefix(), [])
            self.assertEqual(sessions.systems(), [])
            self.assertEqual(sessions.system_name(), "this computer")

    def test_the_list_wsl_gives_is_read(self) -> None:
        answer = mock.Mock(stdout="Ubuntu\r\nkali-linux\r\n\r\n".encode("utf-16-le"))
        with (
            mock.patch.object(sessions.sys, "platform", "win32"),
            mock.patch.object(sessions.shutil, "which", return_value="wsl.exe"),
            mock.patch.object(sessions.subprocess, "run", return_value=answer),
        ):
            self.assertEqual(sessions.systems(), ["Ubuntu", "kali-linux"])

    def test_the_command_line_needs_in_session_with_it(self) -> None:
        import contextlib
        import io
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            said = io.StringIO()
            argv = ["kosika", "studio", folder, "--session-system", "kali-linux"]
            with (
                contextlib.redirect_stderr(said),
                mock.patch.object(cli.sys, "argv", argv),
            ):
                code = cli.main()
        self.assertEqual(code, 2)
        self.assertIn("only means something with --in-session", said.getvalue())


if __name__ == "__main__":
    unittest.main()
