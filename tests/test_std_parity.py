"""The two engines' standard libraries, held to each other.

Kosika runs on two engines — the one in Python under `kosika/` and the one in
Go under `native/` — and each carries its own copy of `std/`. A program is
meant to mean the same thing in both. Nothing checked that the copies agreed,
so they stopped agreeing quietly: three modules exist on one side only, and
two shared ones have grown apart.

What is missing is named here, once, so the gap is reported rather than
discovered. The test fails when a module is added to one engine alone, or a
shared module starts to differ, and it fails the other way too: when the Go
engine catches up and an entry here is no longer true, it says so, so this
list cannot rot.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KOSIKA = ROOT / "kosika" / "std"
NATIVE = ROOT / "native" / "std"

#: Modules the Python engine has and the Go engine does not, yet. Each is a
#: feature a program written against kosika/std cannot use in native.
MISSING_FROM_NATIVE = {
    "artifact_call.kos",
    "designs_3d.kos",
    "tiny_char_model.kos",
}

#: Modules in both trees that no longer read the same: designs.kos and
#: brain.kos know a "3d" kind, which lives in designs_3d.kos and is missing
#: from the Go engine; brain.kos also widens saved preference models.
DIFFERENT = {
    "brain.kos",
    "designs.kos",
}


def modules(where: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(where.glob("*.kos"))}


class StandardLibraryParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.kosika = modules(KOSIKA)
        self.native = modules(NATIVE)

    def test_a_module_exists_in_both_engines_unless_it_is_named_here(self) -> None:
        only_python = set(self.kosika) - set(self.native)
        only_native = set(self.native) - set(self.kosika)
        self.assertEqual(
            sorted(only_python - MISSING_FROM_NATIVE),
            [],
            "these modules were added to kosika/std and not to native/std",
        )
        self.assertEqual(
            sorted(only_native),
            [],
            "these modules are in native/std but not in kosika/std",
        )

    def test_a_shared_module_reads_the_same_unless_it_is_named_here(self) -> None:
        apart = sorted(
            name
            for name in set(self.kosika) & set(self.native)
            if self.kosika[name] != self.native[name]
        )
        self.assertEqual(
            [name for name in apart if name not in DIFFERENT],
            [],
            "these modules read differently in the two engines, and are not "
            "in this test's list of known differences",
        )

    def test_the_named_gap_is_still_a_gap(self) -> None:
        # The list is only worth having while it is true: when the Go engine
        # catches up, this says so and the entry comes out.
        caught_up = sorted(name for name in MISSING_FROM_NATIVE if name in self.native)
        self.assertEqual(
            caught_up,
            [],
            "native/std has these now, so they can come out of MISSING_FROM_NATIVE",
        )
        same_again = sorted(
            name
            for name in DIFFERENT
            if name in self.kosika and self.kosika[name] == self.native.get(name)
        )
        self.assertEqual(
            same_again,
            [],
            "these now read the same, so they can come out of DIFFERENT",
        )


if __name__ == "__main__":
    unittest.main()
