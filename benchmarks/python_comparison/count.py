"""Count the two researchers the same way: lines that do something.

    python count.py

Blank lines and comment lines are left out. The tests (and evals) are
counted apart from the program, and each Python section is shown, so it is
plain which lines Kosika did not need because the language supplies them.
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).parent
KOSIKA = HERE.parent.parent / "examples" / "agents" / "researcher" / "main.kos"


def working(lines: list[str], comment: str) -> tuple[int, int]:
    """Lines that do something, and their characters."""

    kept: list[str] = []
    in_doc = False
    for line in lines:
        text = line.strip()
        if comment == "#" and text.startswith('"""'):
            # A docstring: one line, or the start or end of several.
            if not (len(text) > 3 and text.endswith('"""')):
                in_doc = not in_doc
            continue
        if in_doc or not text or text.startswith(comment):
            continue
        kept.append(text)
    return len(kept), sum(len(text) for text in kept)


def show(name: str, lines: list[str], comment: str) -> tuple[int, int]:
    count, size = working(lines, comment)
    print(f"  {name:<44}{count:>5} lines {size:>7} characters")
    return count, size


def main() -> int:
    kosika = KOSIKA.read_text(encoding="utf-8").splitlines()
    cut = next(i for i, line in enumerate(kosika) if line.startswith("test "))
    print("Kosika  examples/agents/researcher/main.kos")
    show("the program", kosika[:cut], "//")
    show("tests and evals", kosika[cut:], "//")

    python = (HERE / "researcher.py").read_text(encoding="utf-8").splitlines()
    marks = [i for i, line in enumerate(python) if line.startswith("# ------")]
    print("Python  benchmarks/python_comparison/researcher.py")
    show("the program", python, "#")
    show("  of which: imports and limits", python[: marks[0]], "#")
    show("  of which: folder safety and search", python[marks[0] : marks[1]], "#")
    show("  of which: calling the model", python[marks[1] : marks[2]], "#")
    show("  of which: the agent itself", python[marks[2] :], "#")
    tests = (HERE / "test_researcher.py").read_text(encoding="utf-8").splitlines()
    show("tests", tests, "#")
    measure = (HERE / "measure.py").read_text(encoding="utf-8").splitlines()
    show("measuring on a real model", measure, "#")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
