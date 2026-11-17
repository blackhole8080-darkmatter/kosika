"""Studio's own page, opened in a real browser: it draws, and throws nothing.

Every other test of the page reads it as text: this file must contain that
string, must not contain that one. This one runs it. A headless browser of
the Chromium family loads the page from Studio's own server, its script runs
for a few seconds, and the test reads the DOM the script built. So a screen
that never appears, or a function that throws on the way, is a failure here
rather than something a person finds.

Where no such browser is installed the test is skipped, not failed: the page
is still held to its text by the tests beside this one.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from kosika import app as studio

ROOT = Path(__file__).resolve().parent.parent
#: A small folder, so Studio's index is built in a moment: the page itself is
#: the same whichever folder is open.
FOLDER = ROOT / "examples" / "motion"
#: Time the page is given, in the browser's own clock, to finish drawing.
BUDGET_MS = 20000


def browser() -> Path | None:
    """A browser that can run the page without being seen."""

    chosen = os.environ.get("KOSIKA_TEST_BROWSER")
    if chosen and Path(chosen).exists():
        return Path(chosen)
    found = studio.window_browsers()
    return found[0] if found else None


def render(executable: Path, url: str) -> str:
    """The DOM the page builds once its script has had time to run."""

    with tempfile.TemporaryDirectory(prefix="kosika-studio-page-") as profile:
        done = subprocess.run(  # noqa: S603 - a found browser path and our own URL
            [
                str(executable),
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                f"--user-data-dir={profile}",
                f"--virtual-time-budget={BUDGET_MS}",
                "--dump-dom",
                url,
            ],
            capture_output=True,
            timeout=240,
            check=False,
        )
    # The page is UTF-8 whatever this computer's console is set to.
    return (done.stdout or b"").decode("utf-8", errors="replace")


class StudioPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.executable = browser()
        if cls.executable is None:
            raise unittest.SkipTest("no Chromium-family browser on this computer")
        cls.application = studio.App(FOLDER)
        cls.server = studio.make_server(cls.application, 0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = studio.address(cls.application, cls.server)
        cls.dom = render(cls.executable, cls.url)
        cls.server.shutdown()
        cls.server.server_close()

    def test_the_page_arrives_and_its_script_runs(self) -> None:
        # A page whose script threw would be a shell: no rail, no heading.
        self.assertTrue(self.dom, "the browser printed no DOM at all")
        self.assertIn("<title>Kosika Studio</title>", self.dom)
        self.assertIn('id="nav"', self.dom)

    def test_a_screen_that_failed_would_say_so(self) -> None:
        self.assertNotIn('class="banner bad"', self.dom)
        self.assertNotIn("Uncaught", self.dom)
        self.assertNotIn("is not defined", self.dom)


if __name__ == "__main__":
    unittest.main()
