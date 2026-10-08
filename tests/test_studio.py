"""Kosika Studio: watching a run as it happens, and opening its own window."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from kosika import app as studio
from kosika.modules import load_program
from kosika.runtime import LiveEvents, run
from kosika.story import build_story, read_trace, to_text

SOURCE = """
record In { n: Number }
record Out { n: Number }

fn double(x: In) -> Out {
  return {n: x.n * 2}
}

workflow inner(x: In) -> Out {
  twice = double(x)
}

workflow main(x: In) -> Out {
  first = call inner(x)
  again = double({n: first.n})
}
"""


class LiveEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.folder = Path(self._dir.name)
        (self.folder / "main.kos").write_text(SOURCE, encoding="utf-8")
        self.program = load_program(self.folder / "main.kos")

    def go(self, name: str, **extra: Any) -> dict[str, Any]:
        return run(
            self.program, "main", self.folder / name, inputs={"x": {"n": 2}}, **extra
        )

    def test_a_watcher_is_told_each_event_and_how_deep_it_is(self) -> None:
        seen: list[tuple[str, Any, int]] = []
        result = self.go(
            "a.jsonl",
            on_event=lambda event, depth: seen.append(
                (event["event"], event.get("step"), depth)
            ),
        )
        self.assertEqual(result, {"n": 8})
        self.assertEqual(seen[0][0], "run_start")
        self.assertEqual(seen[-1], ("run_complete", None, 0))
        self.assertIn(("step_start", "first", 0), seen)
        self.assertIn(("step_start", "twice", 1), seen)
        self.assertIn(("step_complete", "again", 0), seen)
        order = [seen.index(("step_start", "first", 0))]
        order.append(seen.index(("step_start", "twice", 1)))
        order.append(seen.index(("step_complete", "first", 0)))
        self.assertEqual(order, sorted(order))

    def test_watching_changes_nothing_about_the_run(self) -> None:
        def strip(path: Path) -> list[str]:
            import json

            kept = []
            for line in path.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                for key in ("timestamp", "run_id", "elapsed_seconds", "trace"):
                    event.pop(key, None)
                kept.append(event.get("event", ""))
            return kept

        plain = self.go("plain.jsonl")
        watched = self.go("watched.jsonl", on_event=lambda event, depth: None)
        self.assertEqual(plain, watched)
        self.assertEqual(
            strip(self.folder / "plain.jsonl"), strip(self.folder / "watched.jsonl")
        )

    def test_a_watcher_that_breaks_cannot_break_the_run(self) -> None:
        def broken(event: dict[str, Any], depth: int) -> None:
            raise RuntimeError("the display fell over")

        self.assertEqual(self.go("b.jsonl", on_event=broken), {"n": 8})

    def test_the_story_tells_what_a_called_workflow_did(self) -> None:
        self.go("s.jsonl")
        story = build_story(read_trace(self.folder / "s.jsonl"))
        first = story["steps"][0]
        self.assertEqual(first["did"], "ran workflow inner")
        self.assertEqual([step["name"] for step in first["inside"]], ["twice"])
        self.assertNotIn("inside", story["steps"][1])
        told = to_text(story)
        self.assertIn("1. first: ran workflow inner", told)
        self.assertIn("     1. twice: computed double in code", told)

    def test_live_events_are_still_an_ordinary_list(self) -> None:
        events = LiveEvents(lambda event, depth: None)
        events.append({"event": "x"})
        self.assertEqual(list(events), [{"event": "x"}])
        self.assertEqual(events.child().depth, 1)
        self.assertEqual(events.same_depth().depth, 0)


class WindowTests(unittest.TestCase):
    def test_the_page_is_opened_as_its_own_window(self) -> None:
        browser = Path("C:/somewhere/msedge.exe")
        with (
            mock.patch.object(studio, "window_browsers", return_value=[browser]),
            mock.patch.object(studio.subprocess, "Popen") as popen,
        ):
            self.assertTrue(studio.open_window("http://127.0.0.1:1/?token=t"))
        command = popen.call_args.args[0]
        self.assertEqual(command[0], str(browser))
        self.assertEqual(command[1], "--app=http://127.0.0.1:1/?token=t")

    def test_without_such_a_browser_the_caller_is_told(self) -> None:
        with mock.patch.object(studio, "window_browsers", return_value=[]):
            self.assertFalse(studio.open_window("http://127.0.0.1:1/"))

    def test_a_browser_that_will_not_start_is_skipped(self) -> None:
        with (
            mock.patch.object(studio, "window_browsers", return_value=[Path("x")]),
            mock.patch.object(studio.subprocess, "Popen", side_effect=OSError),
        ):
            self.assertFalse(studio.open_window("http://127.0.0.1:1/"))


if __name__ == "__main__":
    unittest.main()
