"""What a comment above a declaration says it does, and which part of the
product it belongs to.

Kosika's parser drops comments: they are tokens to the lexer and nothing to the
program. So this is the one reader of them, and everything that needs to know
what a comment says goes through it — the coder, which is given a declaration's
line as the specification it writes the body against.

A pointer is one line, above the declaration it belongs to::

    // element: cart.total — adds a row's line total, before discount
    # element: calc.add — adds two numbers, both of which must be numbers

The first field names the part of the product; the rest is what the declaration
is for, in the words of whoever wrote it. Both comment styles are read, so a
Python or JavaScript file is held to the same rule as a Kosika one, and the
declaration under a pointer is found the way that language writes one: a
`def` and a `class` in Python, a `function`, `class` or `const` in JavaScript,
and Kosika's own declarations in a `.kos` file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .project_map import _lines

#: `element: cart.total — what it is`, in either comment style, the dash being
#: any of the three a person's keyboard offers.
_POINTER = re.compile(
    r"^\s*(?://|#)\s*element:\s*([A-Za-z0-9_.]+)\s*(?:[\u2014\u2013-]\s*(.*))?$"
)
#: A line that is a comment and so may sit between a pointer and its code.
_NOTE = re.compile(r"^\s*(?://|#)")
#: A declaration in a language that is not Kosika. A bare `const x =` counts:
#: in JavaScript a function is often one, and it is still a thing with a name.
_DECLARATION = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
    r"(def|class|function|fn|func|workflow|agent|model|tool|swarm|memory|board"
    r"|record|choice)\s+([A-Za-z_]\w*)"
    r"|^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_]\w*)\s*="
)


@dataclass(frozen=True)
class Pointer:
    """A `// element:` (or `# element:`) line, and the declaration below it."""

    element: str
    what: str
    line: int
    declaration: str
    kind: str
    name: str


def _declarations(source: str, path: str) -> dict[int, tuple[str, str]]:
    """Where each declaration starts: line -> (kind, name)."""

    if path.lower().endswith(".kos"):
        # Kosika's own declarations, read the way the index reads them.
        return {line: (kind, name) for (kind, name), line in _lines(source).items()}
    found: dict[int, tuple[str, str]] = {}
    for number, line in enumerate(source.splitlines(), 1):
        match = _DECLARATION.match(line)
        if match is None:
            continue
        kind = match.group(1) or "const"
        found.setdefault(number, (kind, match.group(2) or match.group(3)))
    return found


def read(source: str, path: str = "") -> list[Pointer]:
    """Every pointer in one file, joined to the declaration under it.

    A comment or a blank line may sit between the pointer and the declaration,
    because a section header often does; anything else ends the search, since a
    pointer followed by code it is not about would be a lie about that code.
    """

    lines = source.splitlines()
    starts = _declarations(source, path)
    order = sorted(starts)
    found: list[Pointer] = []
    for number, line in enumerate(lines, 1):
        match = _POINTER.match(line)
        if match is None:
            continue
        closer = next((at for at in order if at > number), None)
        blocked = closer is not None and any(
            filler.strip() and not _NOTE.match(filler)
            for filler in lines[number : closer - 1]
        )
        element = match.group(1)
        what = (match.group(2) or "").strip()
        if closer is None or blocked:
            found.append(
                Pointer(
                    element=element,
                    what=what,
                    line=number,
                    declaration="",
                    kind="",
                    name="",
                )
            )
            continue
        kind, name = starts[closer]
        found.append(
            Pointer(
                element=element,
                what=what,
                line=number,
                declaration=f"{kind} {name}",
                kind=kind,
                name=name,
            )
        )
    return found
