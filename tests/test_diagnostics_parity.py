"""Both engines name a problem the same way.

`kosika check --format json` and `kosika-native check --format json` print
one report shape (kosika.diagnostics/v1). The Go engine names problems from
`native/diagnostic_codes.json`, which is `python -m kosika.diagnostics`
written out; this holds the two equal, and holds both engines to the same
code, message and help for every program the conformance suite refuses.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kosika import diagnostics

ROOT = Path(__file__).resolve().parent.parent
TABLE = ROOT / "native" / "diagnostic_codes.json"


class TableTests(unittest.TestCase):
    def test_the_go_engine_embeds_the_reference_table(self) -> None:
        # Regenerate with: python -m kosika.diagnostics > native/diagnostic_codes.json
        self.assertEqual(
            json.loads(TABLE.read_text(encoding="utf-8")), diagnostics.code_table()
        )

    def test_every_help_names_a_known_code(self) -> None:
        self.assertLessEqual(set(diagnostics.HELP), set(diagnostics.CODES))

    def test_a_captured_local_is_named_and_helped(self) -> None:
        found = diagnostics.report(
            "fn f(xs: Number[], k: Number) -> Number[] {\n"
            "  let g = fn(x: Number) -> Number { x * k }\n"
            "  return for x in xs { g(x) }\n"
            "}\n"
        )
        (item,) = found["diagnostics"]
        self.assertEqual(item["code"], "check/captured-variable")
        self.assertIn("parameter", item["help"])


@unittest.skipUnless(shutil.which("go"), "Go is not installed")
class EngineTests(unittest.TestCase):
    engine: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls._dir = tempfile.TemporaryDirectory()
        cls.engine = Path(cls._dir.name) / "kosika-native.exe"
        built = subprocess.run(
            ["go", "build", "-o", str(cls.engine), "."],
            cwd=ROOT / "native",
            capture_output=True,
            text=True,
        )
        if built.returncode != 0:
            raise AssertionError("the native engine does not build:\n" + built.stderr)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._dir.cleanup()

    def reports(self, program: Path) -> tuple[dict, dict]:
        def read(command: list[str]) -> dict:
            ran = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            return json.loads(ran.stdout)

        return (
            read([sys.executable, "-m", "kosika.cli", "check", "--format", "json", str(program)]),
            read([str(self.engine), "check", "--format", "json", str(program)]),
        )

    def test_both_engines_name_every_refusal_alike(self) -> None:
        refused = sorted((ROOT / "conformance" / "refused").glob("*.kos"))
        self.assertGreater(len(refused), 50)
        for program in refused:
            with self.subTest(program.name):
                reference, native = self.reports(program)
                self.assertFalse(native["ok"])
                self.assertEqual(native["schema"], reference["schema"])
                self.assertEqual(native["codes"], reference["codes"])
                self.assertEqual(
                    [(d["code"], d["message"], d.get("help")) for d in native["diagnostics"]],
                    [(d["code"], d["message"], d.get("help")) for d in reference["diagnostics"]],
                )

    def test_a_parse_error_is_placed_and_helped_alike(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            program = Path(scratch) / "steps.kos"
            program.write_text(
                "record T { text: Text }\n"
                "workflow main(t: T) -> T {\n"
                "  out = t\n"
                "  return out\n"
                "}\n",
                encoding="utf-8",
            )
            reference, native = self.reports(program)
            (ours,), (theirs,) = reference["diagnostics"], native["diagnostics"]
            self.assertEqual(theirs["code"], "parse/workflow-item")
            for key in ("code", "message", "help", "range"):
                self.assertEqual(theirs[key], ours[key], key)

    def test_a_program_that_checks_reports_ok(self) -> None:
        reference, native = self.reports(ROOT / "conformance" / "cases" / "trees.kos")
        self.assertTrue(native["ok"])
        for key in ("ok", "codes", "diagnostics", "error_count", "schema"):
            self.assertEqual(native[key], reference[key], key)


if __name__ == "__main__":
    unittest.main()
