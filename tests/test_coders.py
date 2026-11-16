"""Studio's Coders page: the swarm's runs, its to-do list and starting it."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kosika import coders


def _run(work: Path, name: str, request: str, ended: str | None) -> Path:
    folder = work / name
    folder.mkdir()
    (folder / "request.txt").write_text(request, encoding="utf-8")
    if ended is not None:
        why = (
            ""
            if ended == "passed its checks"
            else "\nWhy it did not pass: it changed no file\n"
        )
        (folder / "report.md").write_text(
            f"# {request}\n\n2026-11-16, 90 s, {ended}\n{why}", encoding="utf-8"
        )
        (folder / "change.patch").write_text("diff\n", encoding="utf-8")
    return folder


class CodersTests(unittest.TestCase):
    def setUp(self) -> None:
        made = tempfile.TemporaryDirectory()
        self.addCleanup(made.cleanup)
        self.work = Path(made.name)
        patcher = mock.patch.dict(
            os.environ, {"KOSIKA_CODER_WORK": str(self.work), "KOSIKA_CODER_TODO": ""}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("KOSIKA_CODER_TODO")

    def test_the_latest_days_runs_are_shown_in_turn_as_the_four_coders(self) -> None:
        _run(self.work, "2026-11-15-9", "an old one", "did not pass")
        _run(self.work, "2026-11-16-1", "add a", "passed its checks")
        _run(self.work, "2026-11-16-2", "add b", "did not pass")
        _run(self.work, "2026-11-16-10", "add c", None)
        _run(self.work, "2026-11-16-3", "add d", None)
        _run(self.work, "2026-11-16-4", "add e", None)
        (self.work / "not-a-run").mkdir()
        view = coders.status()
        self.assertEqual(view["day"], "2026-11-16")
        self.assertEqual(
            [r["run"] for r in view["runs"]],
            [
                "2026-11-16-1",
                "2026-11-16-2",
                "2026-11-16-3",
                "2026-11-16-4",
                "2026-11-16-10",
            ],
        )
        self.assertEqual(
            [r["agent"] for r in view["runs"]],
            ["coder-1", "coder-2", "coder-3", "coder-4", "coder-1"],
        )
        self.assertEqual(
            [r["result"] for r in view["runs"]], ["passed", "did not pass", "", "", ""]
        )
        self.assertEqual(view["runs"][1]["why"], "it changed no file")
        self.assertTrue(view["runs"][0]["patch"])
        self.assertEqual((view["passed"], view["working"]), (1, 3))

    def test_no_runs_yet_is_an_empty_day(self) -> None:
        view = coders.status()
        self.assertEqual((view["day"], view["runs"]), ("", []))

    def test_a_request_is_added_open_once_and_the_list_is_read_by_its_marks(
        self,
    ) -> None:
        (self.work / "queue.md").write_text(
            "# Coders\n\n- [x] add one\n- [!] add two\n- [~] add three\n",
            encoding="utf-8",
        )
        listed = coders.add_request(
            "add a Text method shout() that gives the text in capitals,   giving a Text"
        )
        self.assertEqual(
            [i["state"] for i in listed["items"]], ["passed", "failed", "taken", "open"]
        )
        self.assertEqual(
            listed["items"][-1]["text"],
            "add a Text method shout() that gives the text in capitals, giving a Text",
        )
        with self.assertRaisesRegex(ValueError, "already waiting"):
            coders.add_request(
                "add a Text method shout() that gives the text in capitals, giving a Text"
            )
        with self.assertRaisesRegex(ValueError, "already waiting"):
            coders.add_request("add three")
        with self.assertRaisesRegex(ValueError, "in a sentence"):
            coders.add_request("shout")
        self.assertTrue(
            (self.work / "queue.md")
            .read_text(encoding="utf-8")
            .endswith("giving a Text\n")
        )

    def test_the_swarm_starts_only_with_something_waiting_and_a_swarm_to_start(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "1 to 4"):
            coders.start(9)
        script = self.work / "agents" / "coder" / "daily" / "daily.py"
        with mock.patch.object(coders, "daily_script", return_value=script):
            with self.assertRaisesRegex(FileNotFoundError, "not here"):
                coders.start(2)
            script.parent.mkdir(parents=True)
            script.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Nothing is waiting"):
                coders.start(2)
            coders.add_request(
                "add a Text method shout() that gives the text in capitals, giving a Text"
            )
            with mock.patch.object(coders.subprocess, "Popen") as started:
                said = coders.start(4)
        self.assertEqual(said, {"started": 1, "waiting": 1})
        args = started.call_args.args[0]
        self.assertEqual(
            args[1:],
            [str(script), "--todo", str(self.work / "queue.md"), "--swarm", "4"],
        )

    def test_a_run_is_read_whole_and_only_one_that_passed_can_be_kept(self) -> None:
        _run(self.work, "2026-11-16-1", "add a", "passed its checks")
        _run(self.work, "2026-11-16-2", "add b", "did not pass")
        detail = coders.run_detail("2026-11-16-1")
        self.assertEqual(
            (detail["request"], detail["result"], detail["patch"], detail["kept"]),
            ("add a", "passed", "diff\n", ""),
        )
        with self.assertRaises(ValueError):
            coders.run_detail("../etc")
        with self.assertRaisesRegex(ValueError, "Only a run that passed"):
            coders.keep("2026-11-16-2")

    def test_keeping_makes_a_branch_from_studio_pushes_it_and_opens_a_pull_request(
        self,
    ) -> None:
        _run(
            self.work, "2026-11-16-1", "add a Text method shout()", "passed its checks"
        )
        said = []

        def git(args, cwd):
            said.append(args[:2])
            return (
                "https://github.com/x/kosika.git\n"
                if args[:2] == ["remote", "get-url"]
                else ""
            )

        made = mock.Mock(
            returncode=0, stdout="https://github.com/x/kosika/pull/7\n", stderr=""
        )
        with (
            mock.patch.object(coders, "_git", side_effect=git),
            mock.patch.object(coders.subprocess, "run", return_value=made) as gh,
        ):
            kept = coders.keep("2026-11-16-1")
            again = coders.keep("2026-11-16-1")
        self.assertEqual(kept["url"], "https://github.com/x/kosika/pull/7")
        self.assertEqual(again["url"], kept["url"])
        self.assertIn(["clone", "--quiet"], said)
        self.assertIn(["checkout", "--quiet"], said)
        self.assertIn(["push", "--quiet"], said)
        self.assertEqual(gh.call_count, 1)
        self.assertIn("--base", gh.call_args.args[0])
        self.assertEqual(coders.run_detail("2026-11-16-1")["kept"], kept["url"])

    def test_a_coder_that_is_not_one_has_no_mascot(self) -> None:
        with self.assertRaises(ValueError):
            coders.mascot("stranger", "idle")


if __name__ == "__main__":
    unittest.main()
