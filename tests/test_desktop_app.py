"""Kosika Studio as a desktop app: the icon, the shortcuts, and the start
with no console that ends when the window is closed."""

from __future__ import annotations

import contextlib
import io
import struct
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from kosika import app as studio
from kosika import cli, desktop_app


class IconTests(unittest.TestCase):
    def test_the_icon_is_a_well_formed_ico_with_every_size(self) -> None:
        data = desktop_app.icon_bytes()
        reserved, kind, count = struct.unpack_from("<HHH", data, 0)
        self.assertEqual((reserved, kind, count), (0, 1, len(desktop_app.ICON_SIZES)))
        end = 6 + 16 * count
        for index, size in enumerate(desktop_app.ICON_SIZES):
            width, height, _, _, planes, bits, length, offset = struct.unpack_from(
                "<BBBBHHII", data, 6 + 16 * index
            )
            self.assertEqual((width, height), (size % 256, size % 256))
            self.assertEqual((planes, bits), (1, 32))
            self.assertEqual(offset, end)
            header = struct.unpack_from("<IiiHH", data, offset)
            # The height is doubled: the picture, then its mask.
            self.assertEqual(header, (40, size, size * 2, 1, 32))
            end = offset + length
        self.assertEqual(end, len(data))

    def test_the_picture_is_a_dark_tile_with_see_through_corners(self) -> None:
        picture = desktop_app._picture(32)[40:]
        corner = picture[0:4]  # bottom-left pixel: blue, green, red, alpha
        middle = picture[(16 * 32 + 16) * 4 : (16 * 32 + 16) * 4 + 4]
        self.assertEqual(corner[3], 0)
        self.assertEqual(middle[3], 255)
        self.assertGreater(middle[0], 200)  # the pale dot in the centre

    def test_the_icon_is_the_same_every_time(self) -> None:
        self.assertEqual(desktop_app.icon_bytes(), desktop_app.icon_bytes())


class ShortcutTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.top = Path(self._dir.name)
        (self.top / "Desktop").mkdir()
        programs = (
            self.top / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        )
        programs.mkdir(parents=True)
        self.env = mock.patch.dict(
            "os.environ",
            {
                "USERPROFILE": str(self.top),
                "APPDATA": str(self.top / "Roaming"),
                "LOCALAPPDATA": str(self.top / "Local"),
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.ran: list[dict[str, Any]] = []

    def powershell(self, command: list[str], **more: Any) -> Any:
        """Stands in for PowerShell: makes the file it was asked to make."""
        self.ran.append({"command": command, "env": more["env"]})
        Path(more["env"]["KOSIKA_LNK"]).write_bytes(b"lnk")
        return mock.Mock(returncode=0, stdout="", stderr="")

    def test_it_goes_on_the_desktop_and_in_the_start_menu(self) -> None:
        agents = self.top / "my agents"
        agents.mkdir()
        with (
            mock.patch.object(desktop_app.sys, "platform", "win32"),
            mock.patch.object(
                desktop_app.subprocess, "run", side_effect=self.powershell
            ),
        ):
            made = desktop_app.make_shortcuts(agents)
        self.assertEqual(
            [path.name for path in made], ["Kosika Studio.lnk", "Kosika Studio.lnk"]
        )
        self.assertEqual(made[0].parent, self.top / "Desktop")
        first = self.ran[0]
        # Nothing about the folder is pasted into the command itself.
        self.assertNotIn(str(agents), " ".join(first["command"]))
        self.assertEqual(
            first["env"]["KOSIKA_ARGS"], f'-m kosika.desktop_app "{agents.resolve()}"'
        )
        self.assertEqual(first["env"]["KOSIKA_DIR"], str(desktop_app.code_folder()))
        self.assertTrue(Path(first["env"]["KOSIKA_ICON"]).is_file())

    def test_with_no_folder_the_app_uses_its_usual_one(self) -> None:
        self.assertEqual(desktop_app.shortcut_arguments(None), "-m kosika.desktop_app")

    def test_a_folder_name_that_could_break_out_of_its_quotes_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "quotation mark"):
            desktop_app.shortcut_arguments(Path('a" -c "evil'))

    def test_a_failure_is_reported_not_hidden(self) -> None:
        failed = mock.Mock(returncode=1, stdout="", stderr="Access is denied")
        with (
            mock.patch.object(desktop_app.sys, "platform", "win32"),
            mock.patch.object(desktop_app.subprocess, "run", return_value=failed),
        ):
            with self.assertRaisesRegex(OSError, "Access is denied"):
                desktop_app.make_shortcuts(None)

    def test_they_can_be_taken_away_again(self) -> None:
        for place in desktop_app.shortcut_places():
            place.write_bytes(b"lnk")
        gone = desktop_app.remove_shortcuts()
        self.assertEqual(len(gone), 2)
        self.assertEqual(desktop_app.remove_shortcuts(), [])

    def test_off_windows_it_says_so(self) -> None:
        with mock.patch.object(desktop_app.sys, "platform", "linux"):
            with self.assertRaisesRegex(OSError, "Windows only"):
                desktop_app.make_shortcuts(None)

    def run_cli(self, *arguments: str) -> tuple[int, str]:
        said = io.StringIO()
        with (
            mock.patch.object(cli.sys, "argv", ["kosika", *arguments]),
            contextlib.redirect_stdout(said),
            contextlib.redirect_stderr(said),
        ):
            return cli.main(), said.getvalue()

    def test_the_command_makes_them_and_says_where(self) -> None:
        with (
            mock.patch.object(desktop_app.sys, "platform", "win32"),
            mock.patch.object(
                desktop_app.subprocess, "run", side_effect=self.powershell
            ),
        ):
            code, said = self.run_cli("desktop")
        self.assertEqual(code, 0, said)
        self.assertIn("Kosika Studio is now an app on this computer", said)
        self.assertIn("It starts with nothing granted", said)
        code, said = self.run_cli("desktop", "--remove")
        self.assertEqual(code, 0)
        self.assertIn("Removed:", said)

    def test_the_command_refuses_a_folder_that_is_not_there(self) -> None:
        code, said = self.run_cli("desktop", str(self.top / "nowhere"))
        self.assertEqual(code, 2)
        self.assertIn("is not a folder", said)


class WindowTests(unittest.TestCase):
    def test_the_window_gets_settings_of_its_own_so_it_can_be_waited_for(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            settings = Path(folder) / "window"
            with (
                mock.patch.object(
                    studio, "window_browsers", return_value=[Path("edge")]
                ),
                mock.patch.object(studio.subprocess, "Popen") as started,
            ):
                window = studio.open_app_window("http://127.0.0.1:1/?token=t", settings)
            self.assertIs(window, started.return_value)
            command = started.call_args.args[0]
            self.assertIn("--app=http://127.0.0.1:1/?token=t", command)
            self.assertIn(f"--user-data-dir={settings}", command)
            self.assertTrue(settings.is_dir())

    def test_with_no_such_browser_there_is_no_window(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(studio, "window_browsers", return_value=[]):
                self.assertIsNone(studio.open_app_window("u", Path(folder) / "w"))

    def test_the_app_starts_studio_and_logs_instead_of_printing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            seen: list[list[str]] = []

            def fake_main() -> int:
                seen.append(list(cli.sys.argv))
                print("Kosika is starting.")
                return 0

            with (
                mock.patch.dict("os.environ", {"LOCALAPPDATA": folder}),
                mock.patch.object(cli, "main", side_effect=fake_main),
                mock.patch.object(desktop_app.sys, "stdout", None),
                mock.patch.object(desktop_app.sys, "stderr", None),
                mock.patch.object(desktop_app.sys, "argv", ["x"]),
            ):
                code = desktop_app.main(["C:/agents"])
            self.assertEqual(code, 0)
            self.assertEqual(seen, [["kosika", "start", "C:/agents", "--until-closed"]])
            log = Path(folder) / "kosika" / "studio.log"
            self.assertIn("Kosika is starting.", log.read_text(encoding="utf-8"))

    def test_a_crash_is_told_to_the_person_and_kept_in_the_log(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with (
                mock.patch.dict("os.environ", {"LOCALAPPDATA": folder}),
                mock.patch.object(cli, "main", side_effect=RuntimeError("no port")),
                mock.patch.object(desktop_app, "_tell") as told,
                mock.patch.object(desktop_app.sys, "stdout", None),
                mock.patch.object(desktop_app.sys, "stderr", None),
                mock.patch.object(desktop_app.sys, "argv", ["x"]),
            ):
                code = desktop_app.main([])
            self.assertEqual(code, 1)
            self.assertIn("stopped unexpectedly", told.call_args.args[0])
            log = (Path(folder) / "kosika" / "studio.log").read_text(encoding="utf-8")
            self.assertIn("RuntimeError: no port", log)


if __name__ == "__main__":
    unittest.main()
