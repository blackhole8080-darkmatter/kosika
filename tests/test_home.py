"""Studio's home screen: the facts it gathers, and home.kos's say on them."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from kosika import home


class HomeFacts(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.work = self.root / "kosika-daily"
        self.work.mkdir()
        self.overlay = self.root / "overlay"

    def tearDown(self) -> None:
        self.dir.cleanup()

    def run_folder(self, name: str, request: str, passed: bool, kept: bool = False) -> None:
        folder = self.work / name
        folder.mkdir()
        (folder / "request.txt").write_text(request, encoding="utf-8")
        (folder / "report.md").write_text(f"# {request}\n\n2026-11-17, 9 s, {'passed its checks' if passed else 'did not pass'}\n", encoding="utf-8")
        (folder / "change.patch").write_text("diff\n", encoding="utf-8")
        if kept:
            (folder / "kept.txt").write_text("https://example.invalid/pr/1\n", encoding="utf-8")

    def test_agents_at_work_carry_their_pointer_colour(self) -> None:
        (self.overlay / "glow").mkdir(parents=True)
        (self.overlay / "pointers").mkdir()
        (self.overlay / "pointers" / "overlay-scene-a.json").write_text(json.dumps(
            {"agents": {"coder-1": {"colour": "#ff3ea5", "tag": "Shilpi"}}}), encoding="utf-8")
        (self.overlay / "glow" / "coder-1.json").write_text(json.dumps(
            {"name": "coder-1", "at": time.time(), "doing": "the icon", "typing": "fn icon("}), encoding="utf-8")
        (self.overlay / "glow" / "old.json").write_text(json.dumps({"name": "old", "at": time.time() - 60}), encoding="utf-8")
        found = home.working(home=self.overlay)
        self.assertEqual([w["name"] for w in found], ["coder-1"])
        self.assertEqual((found[0]["colour"], found[0]["tag"], found[0]["typing"]), ("#ff3ea5", "Shilpi", "fn icon("))

    def test_only_what_passed_and_was_not_kept_waits(self) -> None:
        self.run_folder("2026-11-17-1", "a passed one", True)
        self.run_folder("2026-11-17-2", "a failed one", False)
        self.run_folder("2026-11-17-3", "a kept one", True, kept=True)
        self.assertEqual([r["request"] for r in home.waiting(self.work)], ["a passed one"])

    def test_the_days_list_puts_work_in_hand_first(self) -> None:
        (self.work / "studio-todo.md").write_text("- [ ] later\n- [~] now\n- [x] done (2026-11-17, x)\n", encoding="utf-8")
        with mock.patch.object(home.coders, "todo_file", return_value=self.work / "todo.md"):
            listed = home.tasks(self.work)
        self.assertEqual((listed["open"], listed["taken"]), (1, 1))
        self.assertEqual([i["text"] for i in listed["items"]], ["now", "later"])

    def test_home_kos_greets_and_orders_the_cards(self) -> None:
        self.run_folder("2026-11-17-1", "a passed one", True)
        with mock.patch.dict(os.environ, {"KOSIKA_OVERLAY_HOME": str(self.overlay), "KOSIKA_PERSON": "aryan"}), \
                mock.patch.object(home.coders, "todo_file", return_value=self.work / "todo.md"):
            shown = home.home(self.work)
        self.assertTrue(shown["ok"], shown.get("error"))
        self.assertTrue(shown["look"]["greeting"].endswith(", Aryan"))
        # Nobody at work and a change waiting: what waits comes first.
        self.assertEqual(shown["look"]["cards"][0]["key"], "waiting")
        self.assertEqual(shown["look"]["tabs"], ["Today", "Chat", "Designs", "Coders"])


if __name__ == "__main__":
    unittest.main()
