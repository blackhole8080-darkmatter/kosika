"""The two engines' standard libraries, held to each other.

Both engines carry their own copy of `std/`: the Python engine reads
`kosika/std`, the Go engine embeds `native/std`. A program is meant to mean
the same thing in both, and for a while they did not — three modules existed
on one side only and two shared ones had grown apart, quietly. They read the
same now, and this holds them that way: file for file, and by running the
newest of them in both engines, so a module the Go engine cannot parse is a
failure here rather than a discovery by whoever imports it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KOSIKA = ROOT / "kosika" / "std"
NATIVE = ROOT / "native" / "std"
ENGINE = ROOT / "native"

#: Three dimensions, a call to an artifact, and the tiny character model: the
#: modules the Go engine did not have. Both engines must print the same.
PROGRAM = """import "std/designs.kos"
import "std/designs_3d.kos"
import "std/artifact_call.kos"
import "std/tiny_char_model.kos"

record Out { said: Text }

fn says() -> Out {
  let kinds = dz_kinds()
  let scene = d3_scene_for("a coral reef", "ocean")
  let art = d3_render(scene)
  return {said: (if kinds.contains("3d") { "3d is a kind" } else { "no 3d" }) + ", " + art.title + ", " + art.faces.to_text() + " faces, " + art.palette}
}

workflow main() -> Out {
  result = says()
}
"""


def modules(where: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(where.glob("*.kos"))}


class StandardLibraryParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.kosika = modules(KOSIKA)
        self.native = modules(NATIVE)

    def test_both_engines_hold_the_same_modules(self) -> None:
        self.assertEqual(
            sorted(set(self.kosika) - set(self.native)),
            [],
            "these modules are in kosika/std and not in native/std",
        )
        self.assertEqual(
            sorted(set(self.native) - set(self.kosika)),
            [],
            "these modules are in native/std and not in kosika/std",
        )

    def test_every_module_reads_the_same_in_both_engines(self) -> None:
        apart = sorted(
            name
            for name in set(self.kosika) & set(self.native)
            if self.kosika[name] != self.native[name]
        )
        self.assertEqual(
            apart,
            [],
            "these modules read differently in the two engines; copy kosika/std "
            "over native/std so both engines mean the same thing",
        )


@unittest.skipUnless(
    shutil.which("go"), "Go is not installed, so the native engine is not built"
)
class StandardLibraryBehaviourTests(unittest.TestCase):
    engine: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls._dir = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._dir.cleanup)
        cls.engine = Path(cls._dir.name) / "kosika-native.exe"
        built = subprocess.run(
            ["go", "build", "-o", str(cls.engine), "."],
            cwd=ENGINE,
            capture_output=True,
            text=True,
        )
        if built.returncode != 0:
            raise AssertionError("the native engine does not build:\n" + built.stderr)

    def test_the_newest_modules_give_the_same_answer_in_both_engines(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            program = Path(folder) / "three.kos"
            program.write_text(PROGRAM, encoding="utf-8", newline="\n")
            native = subprocess.run(
                [str(self.engine), "run", str(program)],
                cwd=folder,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            reference = subprocess.run(
                [sys.executable, "-m", "kosika.cli", "run", str(program)],
                cwd=folder,
                env={**os.environ, "PYTHONPATH": str(ROOT)},
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        self.assertEqual(reference.returncode, 0, reference.stderr)
        self.assertEqual(native.returncode, 0, native.stderr)
        self.assertIn("3d is a kind", reference.stdout)
        self.assertEqual(
            native.stdout.replace("\r\n", "\n"),
            reference.stdout.replace("\r\n", "\n"),
        )


if __name__ == "__main__":
    unittest.main()
