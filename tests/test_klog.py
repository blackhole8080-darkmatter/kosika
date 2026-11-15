"""Kosika logs (kosika/klog.py, native/klog.go): typed, add-only, chained.

A log written by one engine is read by the other; both write the same
bytes for the same entries, apart from the time each was added; a changed
or removed line is found, and found at the same place by both; a log of
another record, or of the same record with another shape, is refused.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kosika import klog

ROOT = Path(__file__).resolve().parents[1]

PROGRAM = """
record Step { agent: Text tool: Text args: Text[] ok: Bool saw: Text }
record Add { path: Text by: Text entry: Step }
record Added { n: Number hash: Text }
record Path { path: Text }
record Steps { rows: Step[] ok: Bool problem: Text }
record Kept { entries: Number last: Text intact: Bool problem: Text }

tool keep { source system "log" operation: "append" access: observe input: Add output: Added }
tool steps { source system "log" operation: "read" access: observe input: Path output: Steps }

agent logger {
  can: [keep, steps]
  max_steps: 1
}

fn kept(all: Steps) -> Kept {
  let last = if all.rows.length() > 0 { all.rows.last().or({agent: "", tool: "", args: [], ok: false, saw: ""}).saw } else { "" }
  return {entries: all.rows.length(), last: last, intact: all.ok, problem: all.problem}
}

workflow main(s: Step) -> Kept {
  added = execute logger.keep(path: "steps.kl", by: s.agent, entry: s)
  all   = execute logger.steps(path: "steps.kl")
  result = kept(all)
}
"""

ENTRIES = [
    {"agent": "browser_hands", "tool": "web", "args": ["open", "https://example.com/é"], "ok": True,
     "saw": "Kosika: a language \"for\" agents\nline two\ttab   ✓"},
    {"agent": "eyes", "tool": "look", "args": [], "ok": False, "saw": ""},
]


def _without_time(text: str) -> list:
    lines = [json.loads(line) for line in filter(None, text.split("\n"))]
    for line in lines[1:]:
        # The time differs between runs, and so does every hash after it.
        line[1] = "T"
        if line[0] > 1:
            line[3] = "H"
    return lines


class EngineRun:
    def __init__(self, folder: Path, engine: list[str]) -> None:
        self.folder, self.engine = folder, engine
        (folder / "steps.kos").write_text(PROGRAM, encoding="utf-8")

    def add(self, entry: dict) -> dict:
        (self.folder / "in.json").write_text(json.dumps({"s": entry}), encoding="utf-8")
        ran = subprocess.run(
            self.engine + ["run", "steps.kos", "--input", "in.json", "--allow-folder", "."],
            cwd=self.folder, capture_output=True, text=True, encoding="utf-8",
            env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8"},
        )
        if ran.returncode != 0:
            raise AssertionError(ran.stdout + ran.stderr)
        # Both engines print the result first, as Python's repr writes it.
        return ast.literal_eval(ran.stdout.split("\n")[0])

    @property
    def log(self) -> str:
        return (self.folder / "steps.kl").read_text(encoding="utf-8")


PYTHON = [sys.executable, "-m", "kosika.cli"]


class PythonLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.run_ = EngineRun(Path(self._dir.name), PYTHON)

    def test_entries_are_added_checked_and_chained(self) -> None:
        for entry in ENTRIES:
            kept = self.run_.add(entry)
        self.assertEqual(kept, {"entries": 2, "last": "", "intact": True, "problem": ""})
        lines = self.run_.log.split("\n")
        head = json.loads(lines[0])
        self.assertEqual(head["type"], "Step")
        self.assertEqual(head["fields"], ["agent", "tool", "args", "ok", "saw"])
        first = json.loads(lines[1])
        self.assertEqual(first[0], 1)
        self.assertEqual(first[3], klog.hash_of(lines[0]))
        self.assertEqual(first[4:], list(ENTRIES[0].values()))
        self.assertEqual(json.loads(lines[2])[3], klog.hash_of(lines[1]))

    def test_a_changed_line_is_found_where_it_was_changed(self) -> None:
        for entry in ENTRIES:
            self.run_.add(entry)
        path = self.run_.folder / "steps.kl"
        lines = path.read_text(encoding="utf-8").split("\n")
        lines[1] = lines[1].replace("open", "shut")
        path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        with self.assertRaises(AssertionError) as caught:
            self.run_.add(ENTRIES[1])
        self.assertIn("line 3 does not follow line 2: line 2 was changed; nothing was added",
                      str(caught.exception))
        self.assertIn("CHANGED: line 2 was changed", klog.show(path))

    def test_a_log_of_another_shape_is_refused(self) -> None:
        self.run_.add(ENTRIES[0])
        path = self.run_.folder / "steps.kl"
        lines = path.read_text(encoding="utf-8").split("\n")
        head = json.loads(lines[0])
        head["shape"] = "0" * 16
        lines[0] = json.dumps(head)
        path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        with self.assertRaises(AssertionError) as caught:
            self.run_.add(ENTRIES[0])
        self.assertIn("another shape", str(caught.exception))

    def test_show_gives_a_table_and_named_records(self) -> None:
        for entry in ENTRIES:
            self.run_.add(entry)
        path = self.run_.folder / "steps.kl"
        table = klog.show(path)
        self.assertIn("n  at", table)
        self.assertIn("✓", table)
        self.assertIn("✗", table)
        self.assertTrue(table.endswith("chain intact"))
        named = klog.show(path, named=True).split("\n")
        self.assertEqual(json.loads(named[1])["agent"], "browser_hands")


@unittest.skipUnless(shutil.which("go"), "Go is not installed, so the native engine is not built")
class BothEnginesTests(unittest.TestCase):
    engine: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls._dir = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._dir.cleanup)
        cls.engine = Path(cls._dir.name) / "kosika-native.exe"
        built = subprocess.run(["go", "build", "-o", str(cls.engine), "."], cwd=ROOT / "native",
                               capture_output=True, text=True)
        if built.returncode != 0:
            raise AssertionError("the native engine does not build:\n" + built.stderr)

    def folders(self) -> tuple[EngineRun, EngineRun]:
        made = tempfile.TemporaryDirectory()
        self.addCleanup(made.cleanup)
        base = Path(made.name)
        (base / "py").mkdir()
        (base / "go").mkdir()
        return EngineRun(base / "py", PYTHON), EngineRun(base / "go", [str(self.engine)])

    def test_both_engines_write_the_same_lines(self) -> None:
        py, go = self.folders()
        for entry in ENTRIES:
            self.assertEqual(py.add(entry), go.add(entry))
        self.assertEqual(py.log.split("\n")[0], go.log.split("\n")[0])
        self.assertEqual(_without_time(py.log), _without_time(go.log))
        # Line by line the bytes match, apart from the time and the chain
        # hashes that cover it.
        for a, b in zip(py.log.split("\n")[1:-1], go.log.split("\n")[1:-1]):
            ca, cb = json.loads(a), json.loads(b)
            self.assertEqual(klog.line_of(ca[4:]), klog.line_of(cb[4:]))
            self.assertEqual(a.split(",", 4)[4], b.split(",", 4)[4])

    def test_each_engine_reads_what_the_other_wrote(self) -> None:
        py, go = self.folders()
        py.add(ENTRIES[0])
        shutil.copy(py.folder / "steps.kl", go.folder / "steps.kl")
        kept = go.add(ENTRIES[1])
        self.assertEqual(kept, {"entries": 2, "last": "", "intact": True, "problem": ""})
        shutil.copy(go.folder / "steps.kl", py.folder / "steps.kl")
        self.assertEqual(py.add(ENTRIES[0])["entries"], 3)

    def test_both_engines_find_a_change_at_the_same_place(self) -> None:
        py, go = self.folders()
        for entry in ENTRIES:
            py.add(entry)
        changed = py.log.replace("https://example.com", "https://elsewhere.com", 1)
        self.assertNotEqual(changed, py.log)
        for run in (py, go):
            (run.folder / "steps.kl").write_bytes(changed.encode("utf-8"))
        # Neither adds to a changed log, and both say where it was changed.
        for run in (py, go):
            with self.assertRaises(AssertionError) as caught:
                run.add(ENTRIES[0])
            self.assertIn("line 3 does not follow line 2: line 2 was changed; nothing was added",
                          str(caught.exception))
            self.assertEqual(run.log, changed)


if __name__ == "__main__":
    unittest.main()
