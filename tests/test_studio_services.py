"""Studio's Services page: the agents that keep running, read from a
services file, each with what it may touch, and a pause that sticks."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kosika import services as running
from kosika.app import PAGE, App, AppError

ROOT = Path(__file__).resolve().parents[1]
LOOKOUT = (ROOT / "examples" / "agents" / "lookout" / "main.kos").as_posix()

SERVICES = f"""// Two services for the test.
service lookout {{
  program "{LOOKOUT}"
  say "check"
  every 15 minutes
  allow site "github.com"
}}

service quiet {{
  program "{LOOKOUT}"
}}
"""


class ServicesPageTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.file = self.root / "aios.services"
        self.file.write_text(SERVICES, encoding="utf-8")

    def test_each_service_shows_what_it_may_touch_and_how_often(self) -> None:
        view = App(self.root, services=self.file).services()
        self.assertEqual(view["file"], str(self.file.resolve()))
        by_name = {item["name"]: item for item in view["services"]}
        self.assertEqual(by_name["lookout"]["every"], 900)
        self.assertIn("reads github.com", by_name["lookout"]["touches"])
        self.assertEqual(by_name["quiet"]["touches"], ["nothing outside itself"])
        self.assertFalse(by_name["lookout"]["paused"])

    def test_a_pause_is_kept_in_the_services_folder_and_can_be_lifted(self) -> None:
        app = App(self.root, services=self.file)
        view = app.pause_service({"name": "lookout", "paused": True})
        self.assertTrue(next(i for i in view["services"] if i["name"] == "lookout")["paused"])
        self.assertEqual(running.read_paused(self.file), ["lookout"])
        app.pause_service({"name": "lookout", "paused": False})
        self.assertEqual(running.read_paused(self.file), [])

    def test_an_unknown_service_or_a_bad_request_is_refused(self) -> None:
        app = App(self.root, services=self.file)
        with self.assertRaises(AppError):
            app.pause_service({"name": "nobody", "paused": True})
        with self.assertRaises(AppError):
            app.pause_service({"name": "lookout", "paused": "yes"})

    def test_without_a_services_file_the_page_says_how(self) -> None:
        self.assertEqual(App(self.root).services(), {"file": "", "services": []})
        page = PAGE.read_text(encoding="utf-8")
        self.assertIn('["services", "Services"]', page)
        self.assertIn("KOSIKA_SERVICES", page)


if __name__ == "__main__":
    unittest.main()
