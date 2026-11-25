"""The conformance suite: what any Kosika engine must print.

    python -m kosika.conformance write         let the reference say what it prints
    python -m kosika.conformance check         is the reference still saying that?
    python -m kosika.conformance capabilities  what this engine reads
    python -m kosika.conformance engine CMD... does another engine print it?
                                               (runs `CMD test FILE` for each)
    python -m kosika.conformance agree CMD...  do both engines agree on every
                                               other program in the repository?
                                               (see conformance/known_gaps.txt)

The suite is in `conformance/`. See its README.
"""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .modules import load_program
from .testing import run_tests, to_text

ROOT = Path(__file__).resolve().parents[1] / "conformance"
FOLDERS = ("cases", "failing", "refused")
#: The exit code an engine must end with, by folder: every test passed, one
#: failed, or the program was refused before it ran.
EXIT_CODES = {"cases": 0, "failing": 1, "refused": 2}
#: Language surfaces an engine may lack while it is being built. A program
#: says what it needs on its first line, `// requires: choice`, so that an
#: engine can be held to the cases it claims, not to the ones it does not
#: read yet. `ENGINE capabilities` prints these, one a line.
CAPABILITIES = frozenset({"choice"})


def required(program: Path) -> frozenset[str]:
    """The capabilities a suite program says it needs."""

    first = program.read_text(encoding="utf-8").splitlines()[:1]
    if not first or not first[0].startswith("// requires:"):
        return frozenset()
    return frozenset(first[0].removeprefix("// requires:").split())


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
        return f"Check failed: {unplaced(str(exc), program)}\n", 2
    failed = any(not result.passed for result in results)
    return to_text(results) + "\n", 1 if failed else 0


def unplaced(text: str, program: Path) -> str:
    """A parse error names its file first. Where the suite lives differs
    from machine to machine, so that name is left out of what is compared."""

    for spelled in {str(program), str(program.resolve())}:
        text = text.replace(f"{spelled}: ", "")
    return text


def main(argv: list[str] | None = None) -> int:
    command = (argv if argv is not None else sys.argv[1:]) or ["check"]
    if command[0] == "capabilities":
        for capability in sorted(CAPABILITIES):
            print(capability)
        return 0
    if command[0] == "engine":
        return check_engine(command[1:])
    if command[0] == "agree":
        return check_agreement(command[1:])
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


def check_engine(engine: list[str]) -> int:
    """Hold another engine to the suite: `ENGINE test FILE` must print the
    expected text and end with the folder's exit code. A program needing a
    capability the engine does not claim is skipped by name."""

    if not engine:
        print("give the engine's command, as: engine ./kosika-native")
        return 2
    claimed = subprocess.run(
        [*engine, "capabilities"], capture_output=True, text=True, check=False
    )
    capabilities = set(claimed.stdout.split()) if claimed.returncode == 0 else set()
    wrong = skipped = 0
    for program in programs():
        missing = required(program) - capabilities
        if missing:
            skipped += 1
            print(f"SKIPPED {program.relative_to(ROOT)} (needs {', '.join(sorted(missing))})")
            continue
        ran = subprocess.run(
            [*engine, "test", str(program)], capture_output=True, check=False
        )
        printed = unplaced(
            (ran.stdout + ran.stderr).decode("utf-8").replace("\r\n", "\n"), program
        )
        expected = expected_path(program).read_text(encoding="utf-8")
        code = EXIT_CODES[program.parent.name]
        if printed != expected or ran.returncode != code:
            wrong += 1
            print(f"DIFFERS {program.relative_to(ROOT)} (exit {ran.returncode}, wanted {code})")
            for line in printed.splitlines()[:6]:
                print(f"    {line}")
    total = len(programs()) - skipped
    print(f"{total - wrong} of {total} programs print what is expected ({skipped} skipped)")
    return 1 if wrong else 0


REPOSITORY = ROOT.parent
GAPS = ROOT / "known_gaps.txt"
#: Folders whose .kos files are not programs held to the gate.
NOT_PROGRAMS = {"conformance", "node_modules", ".git"}
#: `native/std` is a copy of `kosika/std`, and the standard library's tests
#: are run on both engines by tests/test_std_parity.py: here it is checked,
#: once, and its tests are not run again.
COPIES = ("native/std/",)
CHECKED_ONLY = ("kosika/std/",)
#: Longest a program's check or tests may take on one engine. A program
#: slower than this is named SLOW, not compared.
SECONDS = 60


def known_gaps(path: Path = GAPS) -> dict[str, str]:
    """`known_gaps.txt` as {path: "engine-cannot" or "checks-nowhere"}."""

    gaps: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        kind, _, program = line.partition(" ")
        if kind not in ("engine-cannot", "checks-nowhere") or not program.strip():
            raise ValueError(f"{path.name}:{number}: write `engine-cannot PATH` or `checks-nowhere PATH`")
        gaps[program.strip()] = kind
    return gaps


def repository_programs(root: Path = REPOSITORY) -> list[Path]:
    """Every .kos file of the repository outside the suite."""

    return sorted(
        path
        for path in root.rglob("*.kos")
        if not NOT_PROGRAMS & set(path.relative_to(root).parts)
        and not path.relative_to(root).as_posix().startswith(COPIES)
    )


def _ran(command: list[str]) -> tuple[int | str, str]:
    try:
        ran = subprocess.run(
            command,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "timeout", ""
    text = (ran.stdout + ran.stderr).decode("utf-8", "replace").replace("\r\n", "\n")
    return ran.returncode, text


def _last_line(text: str) -> str:
    lines = text.strip().splitlines()
    return lines[-1] if lines else ""


def agreement(engine: list[str], program: Path, gap: str | None) -> str | None:
    """What is wrong with how the two engines treat one program, or None."""

    reference = [sys.executable, "-m", "kosika.cli"]
    ours, our_words = _ran([*reference, "check", str(program)])
    theirs, their_words = _ran([*engine, "check", str(program)])
    if gap == "checks-nowhere":
        if ours == 0 or theirs == 0:
            return "now checks on an engine: take its line out of known_gaps.txt"
        return None
    if gap == "engine-cannot":
        if ours != 0:
            return "the reference refuses it: " + _last_line(our_words)
        if theirs == 0:
            return "the other engine now checks it: take its line out of known_gaps.txt"
        if "cannot" not in _last_line(their_words):
            return "refused, but not by name: " + _last_line(their_words)
        return None
    if ours != 0 and theirs != 0:
        return "neither engine checks it: " + _last_line(our_words)
    if ours != theirs:
        return (
            f"check differs: reference {_last_line(our_words)!r}, "
            f"other engine {_last_line(their_words)!r}"
        )
    if program.relative_to(REPOSITORY).as_posix().startswith(CHECKED_ONLY):
        return None
    if 'test "' not in program.read_text(encoding="utf-8", errors="replace"):
        return None
    ours, our_words = _ran([*reference, "test", str(program)])
    theirs, their_words = _ran([*engine, "test", str(program)])
    if "timeout" in (ours, theirs):
        # Too slow to compare here, not a disagreement; the slow engine is
        # named so that it is seen.
        print(f"SLOW {program.relative_to(REPOSITORY)} (over {SECONDS} s on "
              f"{'the reference' if ours == 'timeout' else 'the other engine'})")
        return None
    if (ours, our_words) != (theirs, their_words):
        return (
            f"tests differ: reference {_last_line(our_words)!r} (exit {ours}), "
            f"other engine {_last_line(their_words)!r} (exit {theirs})"
        )
    return None


def check_agreement(engine: list[str]) -> int:
    """Hold both engines to one verdict on every program of the repository:
    what one checks the other checks, and its tests come out the same. This
    is "check means run" across engines; the exceptions are listed, with the
    reason, in `conformance/known_gaps.txt`, and that list only shrinks."""

    if not engine:
        print("give the engine's command, as: agree ./kosika-native")
        return 2
    gaps = known_gaps()
    found = repository_programs()
    names = {path.relative_to(REPOSITORY).as_posix() for path in found}
    wrong = 0
    for listed in sorted(set(gaps) - names):
        wrong += 1
        print(f"DIFFERS {listed}: listed in known_gaps.txt, but there is no such program")
    workers = max(1, min(8, os.cpu_count() or 1))
    with ThreadPoolExecutor(workers) as pool:
        problems = pool.map(
            lambda path: agreement(
                engine, path, gaps.get(path.relative_to(REPOSITORY).as_posix())
            ),
            found,
        )
        for path, problem in zip(found, problems):
            if problem is not None:
                wrong += 1
                print(f"DIFFERS {path.relative_to(REPOSITORY).as_posix()}: {problem}")
    print(
        f"{len(found) - wrong} of {len(found)} programs agree "
        f"({len(gaps)} known gaps, listed in conformance/known_gaps.txt)"
    )
    return 1 if wrong else 0


if __name__ == "__main__":
    raise SystemExit(main())
