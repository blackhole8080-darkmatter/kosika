from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from kosika.ask_folder import (
    AskFolderError,
    answer,
    page,
    put_ask,
    wait_for_answer,
    waiting,
)

REQUEST = {
    "step": ["note", "typed"],
    "tool": "hand",
    "arguments": {"args": ["hands.py", "write", "notepad", "hi"]},
    "plan_hash": "p" * 64,
    "action_hash": "a" * 64,
}

PROGRAM = """
record Command { args: Text[] }
record Ran { exit_code: Number stdout: Text stderr: Text }
record In { word: Text }
tool say { source system "shell" operation: "echo" access: execute input: Command output: Ran }
agent a { can: [say] max_steps: 1 }
workflow main(i: In) -> Ran {
  one = execute a.say(args: ["one", i.word])
  two = execute a.say(args: ["two", one.stdout.trim()])
  require approval
}
"""


class AskFolderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp()) / "asks"
        self.addCleanup(shutil.rmtree, self.folder.parent, True)

    def answer_soon(self, reply: str, by: str = "aryan") -> threading.Thread:
        def later() -> None:
            for _ in range(100):
                if waiting(self.folder):
                    answer(self.folder, None, reply, by)
                    return
                time.sleep(0.05)

        thread = threading.Thread(target=later)
        thread.start()
        self.addCleanup(thread.join)
        return thread

    def test_a_yes_written_in_the_folder_approves_and_both_files_go(self) -> None:
        self.answer_soon("yes")
        self.assertEqual(wait_for_answer(self.folder, REQUEST, wait_seconds=10), (True, "aryan"))
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_a_no_refuses(self) -> None:
        self.answer_soon("no")
        self.assertEqual(wait_for_answer(self.folder, REQUEST, wait_seconds=10)[0], False)

    def test_no_answer_in_time_is_a_no_and_the_ask_is_taken_back(self) -> None:
        self.assertEqual(wait_for_answer(self.folder, REQUEST, wait_seconds=0.2)[0], False)
        self.assertEqual(waiting(self.folder), [])

    def test_a_left_over_answer_cannot_approve_a_new_ask(self) -> None:
        old = put_ask(self.folder, REQUEST)
        answer(self.folder, old, "yes", "aryan")
        (self.folder / f"{old}.ask.json").unlink()
        self.assertEqual(wait_for_answer(self.folder, REQUEST, wait_seconds=0.3)[0], False)

    def test_the_ask_says_what_would_be_done_and_carries_its_hashes(self) -> None:
        put_ask(self.folder, REQUEST)
        [ask] = waiting(self.folder)
        self.assertIn('note / typed: hand {"args": ["hands.py", "write", "notepad", "hi"]}', ask["says"])
        self.assertEqual((ask["plan_hash"], ask["action_hash"]), ("p" * 64, "a" * 64))

    def test_answering_needs_one_ask_or_its_id_and_a_clear_word(self) -> None:
        with self.assertRaises(AskFolderError):
            answer(self.folder, None, "yes", "aryan")
        first, second = put_ask(self.folder, REQUEST), put_ask(self.folder, REQUEST)
        with self.assertRaises(AskFolderError):
            answer(self.folder, None, "yes", "aryan")
        with self.assertRaises(AskFolderError):
            answer(self.folder, first, "maybe", "aryan")
        self.assertIn("refused", answer(self.folder, second, "no", "aryan"))

    def test_the_phone_page_shows_each_ask_with_a_yes_and_a_no(self) -> None:
        self.assertIn("Nothing is waiting", page(self.folder, "k"))
        ask_id = put_ask(self.folder, REQUEST)
        html = page(self.folder, "k<")
        self.assertIn(ask_id, html)
        self.assertIn("value='yes'", html)
        self.assertIn("/answer?key=k&lt;", html)
        self.assertIn("hands.py", html)

    def test_a_run_waits_in_the_folder_and_runs_once_answered(self) -> None:
        work = self.folder.parent
        (work / "p.kos").write_text(PROGRAM, encoding="utf-8")
        (work / "in.json").write_text(json.dumps({"i": {"word": "hi"}}), encoding="utf-8")
        root = Path(__file__).resolve().parents[1]
        run = subprocess.Popen(
            [sys.executable, "-m", "kosika.cli", "run", "p.kos", "--input", "in.json",
             "--allow-shell", "--allow-folder", ".", "--approve-in", "asks",
             "--approve-wait", "20"],
            cwd=work, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**__import__("os").environ, "PYTHONPATH": str(root)},
        )
        answered = 0
        deadline = time.monotonic() + 30
        while run.poll() is None and time.monotonic() < deadline:
            if waiting(self.folder):
                answer(self.folder, None, "yes", "aryan")
                answered += 1
            time.sleep(0.1)
        out, err = run.communicate(timeout=30)
        self.assertEqual(run.returncode, 0, err)
        self.assertEqual(answered, 2)
        self.assertIn("two one hi", out)
        trace = (work / "run_trace.jsonl").read_text(encoding="utf-8")
        self.assertIn('"approved_by": "aryan"', trace)


if __name__ == "__main__":
    unittest.main()
