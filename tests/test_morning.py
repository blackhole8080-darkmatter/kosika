"""Kosi's morning plan: the facts it reads, and morning.kos's plan."""

from __future__ import annotations

import datetime
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from kosika import morning

DIGEST = """# Research digest, Tuesday 2026-11-17

### Latent Lattice Language Models
Machine learning · 2026-11-16 · A. Author
https://arxiv.org/abs/2611.21608v1 (preprint)

Words.

### Exoplanetary Debris
Galaxies · 2026-11-16 · B. Author
https://arxiv.org/abs/2611.21583v1 (preprint)
"""


class Morning(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        self.work, self.digests, self.home = root / "daily", root / "digests", root / "home"
        for folder in (self.work, self.digests, self.home):
            folder.mkdir()
        (self.digests / "2026-11-17-afternoon.md").write_text(DIGEST, encoding="utf-8")
        self.env = mock.patch.dict(os.environ, {"KOSIKA_DIGEST_DIR": str(self.digests), "KOSIKA_OVERLAY_HOME": str(self.home),
                                                 "KOSIKA_PERSON": "aryan", "KOSIKA_INTERESTS": "Galaxies"})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.dir.cleanup()

    def test_the_digests_papers_are_read_with_field_and_link(self) -> None:
        found = morning.papers(self.digests)
        self.assertEqual([p["title"] for p in found], ["Latent Lattice Language Models", "Exoplanetary Debris"])
        self.assertEqual(found[1], {"title": "Exoplanetary Debris", "field": "Galaxies", "url": "https://arxiv.org/abs/2611.21583v1"})

    def test_only_runs_since_last_evening_count_as_overnight(self) -> None:
        for name, age in (("2026-11-17-1", 3600), ("2026-11-15-1", 3 * 86400)):
            folder = self.work / name
            folder.mkdir()
            (folder / "request.txt").write_text(name, encoding="utf-8")
            report = folder / "report.md"
            report.write_text("# x\n\n2026-11-17, 9 s, passed its checks\n", encoding="utf-8")
            then = time.time() - age
            os.utime(report, (then, then))
        since = datetime.datetime.now() - datetime.timedelta(hours=12)
        self.assertEqual([r["run"] for r in morning.overnight(self.work, since)], ["2026-11-17-1"])

    def test_the_plan_puts_your_fields_first_and_is_approved_once_a_day(self) -> None:
        with mock.patch.object(morning.coders, "todo_file", return_value=self.work / "todo.md"):
            plan = morning.plan(self.work, datetime.datetime(2026, 11, 17, 8, 0))
            self.assertTrue(plan["ok"], plan["error"])
            self.assertEqual(plan["greeting"], "Good morning, Aryan")
            papers = [s for s in plan["steps"] if s["kind"] == "paper"]
            self.assertEqual(papers[0]["title"], "Exoplanetary Debris")
            self.assertEqual(plan["steps"][-1]["kind"], "study")
            self.assertIsNone(plan["approved"])
            said = morning.approve({"by": "test"}, self.work)
            self.assertEqual(said["started"], 0)  # nothing open, so no swarm
            kept = json.loads((self.home / "morning" / f"{datetime.date.today().isoformat()}.json").read_text(encoding="utf-8"))
            self.assertEqual(kept["by"], "test")


if __name__ == "__main__":
    unittest.main()
