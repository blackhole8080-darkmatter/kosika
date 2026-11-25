"""Studio's Brain page: the timeline read right, drawn once per change."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kosika import brain_page


def episode(seq: int, outcome: str, part: str = "ai") -> dict:
    return {"part": part, "session": "default", "seq": seq, "at": f"2026-11-25T10:{seq:02d}",
            "ask": f"What is {seq}% of 2450?", "did": "did it", "outcome": outcome, "checked_by": "tests", "words": ""}


class BrainPage(unittest.TestCase):
    def setUp(self) -> None:
        brain_page._kept.clear()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.memory = Path(self.folder.name)
        patcher = mock.patch.dict("os.environ", {"KOSIKA_BRAIN_MEMORY": str(self.memory)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, episodes: list[dict]) -> None:
        entries = {f"default-{e['seq']}": e for e in episodes}
        entries["not-an-episode"] = "just text"
        (self.memory / "timeline.json").write_text(json.dumps(entries), encoding="utf-8")

    def test_the_newest_episodes_are_kept_oldest_first(self) -> None:
        self.write([episode(seq, "passed") for seq in range(1, 51)])
        shown, total = brain_page.episodes()
        self.assertEqual(total, 50)
        self.assertEqual(len(shown), brain_page.NEWEST)
        self.assertEqual(shown[0]["seq"], 50 - brain_page.NEWEST + 1)
        self.assertEqual(shown[-1]["seq"], 50)

    def test_a_missing_or_broken_timeline_is_an_empty_brain(self) -> None:
        self.assertEqual(brain_page.episodes(), ([], 0))
        (self.memory / "timeline.json").write_text("not json", encoding="utf-8")
        self.assertEqual(brain_page.episodes(), ([], 0))

    def test_brain_kos_draws_the_page_and_counts_the_checks(self) -> None:
        self.write([episode(1, "passed"), episode(2, "passed"), episode(3, "failed", "designs")])
        got = brain_page.page()
        self.assertTrue(got["ok"], got["error"])
        self.assertEqual((got["passed"], got["failed"], got["total"]), (2, 1, 3))
        self.assertIn("<svg", got["svg"])
        self.assertTrue(got["summary"].startswith("Showing all 3 episodes"))

    def test_the_drawing_is_kept_until_the_timeline_changes(self) -> None:
        self.write([episode(1, "passed")])
        drawn = {"title": "t", "page": "<p>", "svg": "", "summary": "s"}
        with mock.patch.object(brain_page, "draw", return_value=drawn) as draw:
            brain_page.page()
            brain_page.page()
            self.assertEqual(draw.call_count, 1)
            self.write([episode(1, "passed"), episode(2, "failed")])
            import os
            stamp = (self.memory / "timeline.json").stat().st_mtime + 5
            os.utime(self.memory / "timeline.json", (stamp, stamp))
            brain_page.page()
            self.assertEqual(draw.call_count, 2)


if __name__ == "__main__":
    unittest.main()
