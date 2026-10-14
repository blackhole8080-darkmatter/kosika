"""Both engines are held to the conformance suite.

The reference engine (Python) must print what the suite expects. The native
engine (Go) must print the same, and must split every Kosika file in the
repository into the same tokens. The native half is skipped on a machine
without Go, and says so.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from kosika import conformance
from kosika.lexer import LexError, lex

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "native"


class ReferenceTests(unittest.TestCase):
    def test_the_suite_has_programs_and_each_has_its_expected_report(self) -> None:
        programs = conformance.programs()
        self.assertGreaterEqual(len(programs), 24)
        for program in programs:
            with self.subTest(program.name):
                self.assertTrue(conformance.expected_path(program).is_file())

    def test_the_reference_engine_prints_what_is_expected(self) -> None:
        for program in conformance.programs():
            with self.subTest(program.name):
                text, code = conformance.report(program)
                wanted = conformance.expected_path(program).read_text(encoding="utf-8")
                self.assertEqual(text, wanted)
                # A case passes; a failing program fails, and is not refused;
                # a refused one never runs.
                self.assertEqual(code, conformance.EXIT_CODES[program.parent.name])

    def test_a_passing_case_has_no_failure_and_a_failing_one_has_some(self) -> None:
        for program in conformance.programs():
            with self.subTest(program.name):
                wanted = conformance.expected_path(program).read_text(encoding="utf-8")
                if program.parent.name == "refused":
                    # Refused by the checker, not on the way in: no path.
                    self.assertTrue(wanted.startswith("Check failed: "))
                    self.assertEqual(wanted.count("\n"), 1)
                    self.assertNotIn(".kos", wanted)
                    continue
                self.assertIn(" passed, ", wanted)
                if program.parent.name == "cases":
                    self.assertNotIn("FAIL  ", wanted)
                    self.assertTrue(wanted.rstrip().endswith(" passed, 0 failed"))
                else:
                    self.assertIn("FAIL  ", wanted)


@unittest.skipUnless(
    shutil.which("go"), "Go is not installed, so the native engine is not built"
)
class NativeTests(unittest.TestCase):
    engine: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls._dir = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._dir.cleanup)
        cls.engine = Path(cls._dir.name) / "kosika-native.exe"
        built = subprocess.run(
            ["go", "build", "-o", str(cls.engine), "."],
            cwd=NATIVE,
            capture_output=True,
            text=True,
        )
        if built.returncode != 0:
            raise AssertionError("the native engine does not build:\n" + built.stderr)

    def run_engine(self, *arguments: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run([str(self.engine), *arguments], capture_output=True)

    def test_it_prints_what_the_suite_expects(self) -> None:
        for program in conformance.programs():
            with self.subTest(program.name):
                ran = self.run_engine("test", str(program))
                printed = ran.stdout.decode("utf-8").replace("\r\n", "\n")
                wanted = conformance.expected_path(program).read_text(encoding="utf-8")
                self.assertEqual(printed, wanted, ran.stderr.decode("utf-8", "replace"))
                self.assertEqual(
                    ran.returncode, conformance.EXIT_CODES[program.parent.name]
                )

    def test_it_splits_every_kosika_file_into_the_reference_tokens(self) -> None:
        listed = subprocess.run(
            ["git", "ls-files", "*.kos"], cwd=ROOT, capture_output=True, text=True
        ).stdout.split()
        files = [ROOT / name for name in listed] or conformance.programs()
        self.assertGreater(len(files), 0)
        compared = 0
        for path in files:
            with self.subTest(path.name):
                source = path.read_bytes().decode("utf-8")
                ran = self.run_engine("tokens", str(path))
                got = [
                    [token["kind"], token["value"], token["line"], token["column"]]
                    for token in map(
                        json.loads, ran.stdout.decode("utf-8").splitlines()
                    )
                ]
                try:
                    wanted = [[t.kind, t.value, t.line, t.column] for t in lex(source)]
                except LexError as exc:
                    # Both must stop at the same place.
                    self.assertNotEqual(ran.returncode, 0)
                    self.assertEqual(
                        ran.stderr.decode("utf-8", "replace").split(": ")[0],
                        str(exc).split(": ")[0],
                    )
                    continue
                self.assertEqual(ran.returncode, 0)
                self.assertEqual(got, wanted)
                compared += len(wanted)
        self.assertGreater(compared, 1000)

    def test_what_it_cannot_do_yet_it_refuses_by_name(self) -> None:
        ran = self.run_engine("test", str(ROOT / "examples" / "mcp" / "librarian.kos"))
        self.assertEqual(ran.returncode, 1)
        said = ran.stderr.decode("utf-8", "replace")
        self.assertIn("kosika-native cannot run a step that asks an agent yet", said)
        self.assertEqual(ran.stdout, b"")
        with tempfile.TemporaryDirectory() as scratch:
            general = Path(scratch) / "general.kos"
            general.write_text("model assistant\n", encoding="utf-8")
            ran = self.run_engine("test", str(general))
        self.assertEqual(ran.returncode, 1)
        self.assertIn(
            "kosika-native cannot read a general model yet",
            ran.stderr.decode("utf-8", "replace"),
        )
        with tempfile.TemporaryDirectory() as scratch:
            asking = Path(scratch) / "asking.kos"
            asking.write_text(
                "record A { text: Text }\n"
                "workflow main(a: A) -> A {\n  b = ask helper(a.text)\n}\n",
                encoding="utf-8",
            )
            ran = self.run_engine("test", str(asking))
        self.assertEqual(ran.returncode, 1)
        self.assertIn(
            "kosika-native cannot run a step that asks an agent yet",
            ran.stderr.decode("utf-8", "replace"),
        )

    def test_a_text_that_is_not_kosika_is_refused_with_its_place(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            bad = Path(scratch) / "bad.kos"
            bad.write_text("fn f() -> Number {\n  return 1 +\n}\n", encoding="utf-8")
            ran = self.run_engine("test", str(bad))
        self.assertEqual(ran.returncode, 1)
        self.assertIn("Expected expression at 3:1, got '}'", ran.stderr.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
