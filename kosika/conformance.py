"""The conformance suite: what any Kosika engine must print.

    python -m kosika.conformance write    let the reference say what it prints
    python -m kosika.conformance check    is the reference still saying that?

The suite is in `conformance/`. See its README.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .modules import load_program
from .testing import run_tests, to_text

ROOT = Path(__file__).resolve().parents[1] / "conformance"
FOLDERS = ("cases", "failing")


def programs(root: Path = ROOT) -> list[Path]:
    """Every program of the suite, in a fixed order."""

    return [
        path for folder in FOLDERS for path in sorted((root / folder).glob("*.kos"))
    ]


def expected_path(program: Path) -> Path:
    return program.with_suffix(".expected.txt")


def report(program: Path) -> tuple[str, int]:
    """What the reference engine's `test` prints for a program, and the
    exit code it ends with."""

    try:
        results = run_tests(load_program(program))
    except (ValueError, TypeError) as exc:
        return f"Check failed: {exc}\n", 2
    failed = any(not result.passed for result in results)
    return to_text(results) + "\n", 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    command = (argv if argv is not None else sys.argv[1:]) or ["check"]
    if command[0] not in ("write", "check"):
        print(__doc__)
        return 2
    wrong = 0
    for program in programs():
        text, _ = report(program)
        target = expected_path(program)
        if command[0] == "write":
            target.write_text(text, encoding="utf-8", newline="\n")
            print(f"wrote {target.relative_to(ROOT)}")
        elif not target.is_file() or target.read_text(encoding="utf-8") != text:
            wrong += 1
            print(f"DIFFERS {program.relative_to(ROOT)}")
    if command[0] == "check":
        print(
            f"{len(programs()) - wrong} of {len(programs())} programs print what is expected"
        )
    return 1 if wrong else 0


if __name__ == "__main__":
    raise SystemExit(main())
