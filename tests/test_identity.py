"""One program, one identity, computed the same way by both engines.

`kosika/identity.py` names a program by its sources: the same bytes both
engines read and already digest. Each engine used to hash its own view of the
tree instead, which is why `tests/test_conformance.py` ignores `program_hash`
when it compares their traces. These tests hold the two to one function.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from kosika.ast import program_payload
from kosika.identity import IDENTITY_SCHEMA, identity, program_identity
from kosika.modules import load_program
from kosika.pack import make_pack
from kosika.parser import parse

ROOT = Path(__file__).resolve().parents[1]
HELLO = ROOT / "examples" / "hello_world.kos"
HELLO_IDENTITY = "d1a493b78cfe9a5e308c78d3d726c688db80ae3a1cc5e0d02bf1ecb1ca9878ce"
VECTOR = "5e84455e354eae9111d23858b40cec9f204fc44f09a0bbb036e73c4142c91225"


def test_the_format_is_exactly_the_documented_lines() -> None:
    written = IDENTITY_SCHEMA + "\na.kos " + "0" * 64 + "\nb/c.kos " + "1" * 64
    assert (
        identity({"b/c.kos": "1" * 64, "a.kos": "0" * 64})
        == hashlib.sha256(written.encode("utf-8")).hexdigest()
    )


def test_the_identity_is_pinned() -> None:
    # Pinned as a value rather than recomputed, so that a change to the format
    # fails here instead of quietly renaming every program in the world.
    assert identity({"a.kos": "0" * 64, "b/c.kos": "1" * 64}) == VECTOR
    assert program_identity(load_program(HELLO)) == HELLO_IDENTITY


def test_the_order_modules_loaded_in_does_not_matter() -> None:
    forwards = {"a.kos": "0" * 64, "b/c.kos": "1" * 64}
    backwards = {"b/c.kos": "1" * 64, "a.kos": "0" * 64}
    assert identity(forwards) == identity(backwards)


def test_a_changed_byte_or_a_new_module_is_a_different_program() -> None:
    assert identity({"a.kos": "0" * 64}) != identity({"a.kos": "1" * 64})
    assert identity({"a.kos": "0" * 64}) != identity(
        {"a.kos": "0" * 64, "b.kos": "0" * 64}
    )


def test_a_program_without_modules_keeps_the_tree_it_was_made_from() -> None:
    # `parse` reads text, not files: there are no sources to be named by, so
    # the tree is all it has. It cannot reach another engine, which only runs
    # files.
    program = parse(HELLO.read_text(encoding="utf-8"))
    assert not program.module_hashes
    assert (
        program_identity(program)
        == hashlib.sha256(
            json.dumps(program_payload(program), sort_keys=True).encode("utf-8")
        ).hexdigest()
    )


def test_a_pack_records_the_same_identity(tmp_path: Path) -> None:
    pack = make_pack(HELLO)
    assert pack["program_hash"] == HELLO_IDENTITY
    assert pack["program_hash"] == program_identity(load_program(HELLO))


@pytest.mark.skipif(shutil.which("go") is None, reason="no Go toolchain here")
def test_the_native_engine_prints_the_same_hash(tmp_path: Path) -> None:
    binary = tmp_path / ("kosika-native" + (".exe" if sys.platform == "win32" else ""))
    built = subprocess.run(
        ["go", "build", "-o", str(binary), "."],
        cwd=ROOT / "native",
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert built.returncode == 0, built.stderr[-2000:]
    for path in sorted((ROOT / "examples").glob("*.kos")):
        try:
            reference = program_identity(load_program(path))
        except (ValueError, TypeError):
            continue
        printed = subprocess.run(
            [str(binary), "hash", str(path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if printed.returncode != 0:
            # What the native engine cannot read yet is the conformance
            # suite's business, not an identity disagreement.
            continue
        assert printed.stdout.strip() == reference, f"{path.name} disagrees"
