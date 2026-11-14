"""Puts a learned skill note in skills/, and does nothing else.

    python skillbook.py keep skills/learned-NAME.md TEXT    writes a new note
    python skillbook.py refuse WHY                          says why, exit 5

learn.kos decides what the note says and whether it may be kept. This only
refuses a path outside skills/, a name not of the form learned-*.md, and a
note that is already there: a learned note never replaces another.
"""

from __future__ import annotations

import sys
from pathlib import Path

SKILLS = Path(__file__).resolve().parent / "skills"


def main(args: list[str]) -> int:
    if len(args) == 2 and args[0] == "refuse":
        print(f"not kept: {args[1]}")
        return 5
    if len(args) != 3 or args[0] != "keep":
        print("usage: skillbook.py keep skills/learned-NAME.md TEXT | refuse WHY", file=sys.stderr)
        return 2
    path = (SKILLS.parent / args[1]).resolve()
    if path.parent != SKILLS or not path.name.startswith("learned-") or path.suffix != ".md":
        print(f"not kept: {args[1]} is not a learned note in skills/")
        return 5
    try:
        with path.open("x", encoding="utf-8", newline="\n") as note:
            note.write(args[2])
    except FileExistsError:
        print(f"not kept: {args[1]} is already there; give the skill another name")
        return 5
    print(f"kept {args[1]} ({len(args[2].splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
