"""No change to the AST may move a program's hash.

`plan_identity` and `plan_hash` are folded into recorded approvals
(`kosika/approval_ledger.py`), and a durable session raises `plan_mismatch`
when the hash it bound no longer matches (`kosika/durable.py:135`). So a
program whose payload shifts silently invalidates every approval made against
it. `_ABSENT_DEFAULTS` (`kosika/ast.py`) exists to stop that happening when a
field is added; this test is the enforcement.

Both canonicalizations are hashed, because they differ and neither may be
changed: `plan_identity` uses `ensure_ascii=False` with default separators
(`kosika/virtual_model.py:169`), and `plan_hash` uses `separators=(",", ":")`
(`kosika/agent.py:97`).

Regenerate only with `python tests/test_hash_stability.py --write`, and only in
a change that means to move hashes.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from kosika.ast import program_payload
from kosika.modules import load_program

ROOT = Path(__file__).resolve().parents[1]
BASELINE = Path(__file__).resolve().parent / "hash_baseline.json"
#: The folders scanned for programs. A folder that does not exist yields
#: nothing, so the list can name a path before it is created.
CORPUS = ("conformance", "examples", "kosika/std")


def corpus_hashes(root: Path = ROOT) -> dict[str, list[str]]:
    """Each loadable program's two payload digests, by repo-relative path.

    A program that does not load is skipped: the suite holds refused programs
    on purpose, and their refusal text is compared elsewhere
    (`tests/test_conformance.py:199`).
    """

    hashes: dict[str, list[str]] = {}
    for folder in CORPUS:
        for path in sorted((root / folder).glob("**/*.kos")):
            try:
                program = load_program(path)
            except (ValueError, TypeError):
                continue
            payload = program_payload(program)
            identity = json.dumps(payload, sort_keys=True, ensure_ascii=False)
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            hashes[path.relative_to(root).as_posix()] = [
                hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            ]
    return dict(sorted(hashes.items()))


def test_program_hashes_have_not_moved() -> None:
    recorded = json.loads(BASELINE.read_text(encoding="utf-8"))
    current = corpus_hashes()
    moved = sorted(
        name
        for name in set(recorded) | set(current)
        if recorded.get(name) != current.get(name)
    )
    assert not moved, (
        f"{len(moved)} program(s) changed payload hash, first: {moved[:5]}. "
        "Adding an AST field must not change an existing program's hash: give "
        "it the value a program without the feature has, and register that "
        "value in _ABSENT_DEFAULTS (kosika/ast.py). Regenerate the baseline "
        "only if this change means to invalidate recorded approvals."
    )


if __name__ == "__main__":
    hashes = corpus_hashes()
    BASELINE.write_text(
        json.dumps(hashes, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {len(hashes)} program hashes to {BASELINE.name}")
    raise SystemExit(0 if "--write" in sys.argv else 2)
