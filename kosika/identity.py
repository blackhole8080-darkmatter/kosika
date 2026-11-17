"""One identity for a program, computed the same way by both engines.

The reference and the native engine each hashed their own idea of a program's
tree, and no two trees agree: `program_payload` (`kosika/ast.py`) on one side,
the program's text on the other (`native/trace.go`). One program therefore had
two identities, so a run recorded by one engine could not be matched to a pack
signed by the other.

This identity is taken over the sources instead, which both engines hold and
both already digest the same way: `Program.module_hashes` is `sha256` of each
module's bytes (`kosika/modules.py`), keyed by the module's path relative to
the program's own folder, or `std/NAME.kos` for the standard library. The
native engine reads those same bytes (`native/identity.go`), so the two agree
without either reproducing the other's tree.

What it does and does not name. It names a *program*: editing a comment, a
blank line or a repository address changes it, and replacing the checker does
not. Approvals stay on `plan_identity` (`kosika/virtual_model.py`), a hash of
the tree, which is deliberately not this and must not become this: recorded
approvals are bound to it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from .ast import Program, program_payload

#: The first line of what is hashed. Raising it names every program
#: differently, so it changes only when the format itself does.
IDENTITY_SCHEMA = "kosika-program/v1"


def identity(module_hashes: Mapping[str, str]) -> str:
    """The identity of the sources named by `module_hashes`.

    What is hashed is the schema line, then one line per module in sorted path
    order: the path, a space, and that module's own digest. The sorted order
    is what makes the identity independent of the order the modules loaded in;
    the native engine sorts the same way.
    """

    lines = [IDENTITY_SCHEMA]
    for path in sorted(module_hashes):
        lines.append(f"{path} {module_hashes[path]}")
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def program_identity(program: Program) -> str:
    """The identity of a loaded program.

    A program with no module hashes was never read from a file: it has no
    sources to be named by, so it keeps the tree it was made from. That is the
    only case in which this is not the source identity, and it cannot reach
    another engine, since the native engine only ever runs files.
    """

    if program.module_hashes:
        return identity(program.module_hashes)
    return hashlib.sha256(
        json.dumps(program_payload(program), sort_keys=True).encode("utf-8")
    ).hexdigest()
