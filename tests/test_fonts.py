"""The two typefaces Kosika carries: where they are and who may have them."""

from __future__ import annotations

import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from kosika import fonts
from kosika.app import PAGE, App, make_server


class FontTests(unittest.TestCase):
    def test_both_families_are_there_with_their_licences(self) -> None:
        for family in (fonts.DISPLAY, fonts.TEXT):
            path = fonts.font_path(family)
            self.assertIsNotNone(path)
            assert path is not None
            # A TrueType file starts with these four bytes.
            self.assertEqual(path.read_bytes()[:4], b"\x00\x01\x00\x00")
        for licence in ("EBGaramond-OFL.txt", "Figtree-OFL.txt"):
            text = (fonts.FOLDER / licence).read_text(encoding="utf-8")
            self.assertIn("SIL Open Font License", text)
        self.assertIsNone(fonts.font_path("Comic Sans"))

    def test_only_the_two_files_are_given_out(self) -> None:
        self.assertIsNotNone(fonts.served("Figtree.ttf"))
        self.assertIsNotNone(fonts.served("EBGaramond.ttf"))
        for name in ("Figtree-OFL.txt", "../index.html", "..\\brand.kos", "", "x.ttf"):
            self.assertIsNone(fonts.served(name))

    def test_a_missing_file_is_nothing_rather_than_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            with mock.patch.object(fonts, "FOLDER", Path(empty)):
                self.assertIsNone(fonts.font_path(fonts.TEXT))
                self.assertIsNone(fonts.served("Figtree.ttf"))

    def test_off_windows_nothing_is_lent(self) -> None:
        with mock.patch.object(fonts.sys, "platform", "linux"):
            self.assertEqual(fonts.lend_to_this_program(), [])

    def test_the_page_asks_this_computer_for_them_and_nowhere_else(self) -> None:
        page = PAGE.read_text(encoding="utf-8")
        self.assertIn('url("/fonts/Figtree.ttf")', page)
        self.assertIn('url("/fonts/EBGaramond.ttf")', page)
        self.assertIn('--display: "EB Garamond"', page)
        self.assertNotIn("https://", page)
        self.assertNotIn("http://", page)

    def test_studio_serves_them_without_a_token_and_nothing_beside_them(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            server = make_server(App(Path(folder)))
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            with urllib.request.urlopen(f"{base}/fonts/Figtree.ttf", timeout=20) as got:
                self.assertEqual(got.headers["Content-Type"].split(";")[0], "font/ttf")
                self.assertEqual(got.read(), fonts.served("Figtree.ttf"))
            for other in ("/fonts/Figtree-OFL.txt", "/fonts/..%2Findex.html"):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(f"{base}{other}", timeout=20)
                self.assertEqual(caught.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
