"""Packs: carry a Kosika program to another device and prove it is the same.

A pack is one JSON file holding every module of a program together with the
program's hash. A second device can check the pack, see exactly what the
program would ask for, and unpack it:

    kosika pack make main.kos --out helper.kpack   # on the first device
    kosika pack check helper.kpack --test          # on the second
    kosika pack unpack helper.kpack helper/

What a pack carries is code and nothing else. It holds no model, no tool
binding, no approval, and no credentials. The device that receives a pack
grants its own models and approves its own tools, exactly as it would for a
program written there, so a pack cannot bring authority with it.

What checking proves: the modules are byte for byte the ones that were packed
(their SHA-256 hashes), and they load to the same program (its hash). Two
devices that hold packs with the same program hash run the same program.

What it does not prove: who made the pack. A pack is not signed, so the hash
must reach the second device by a way its owner trusts.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .ast import Program, program_payload
from .modules import load_program

SCHEMA = "kosika.pack/v1"
MAX_PACK_BYTES = 4 * 1024 * 1024


class PackError(ValueError):
    """A pack cannot be made, or does not hold what it says it holds."""


def program_hash(program: Program) -> str:
    return hashlib.sha256(
        json.dumps(program_payload(program), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _needs(program: Program) -> dict[str, Any]:
    """What a device must grant before the program can run."""

    return {
        "models": sorted(m.name for m in program.models if m.provider is None),
        "tools": [
            {"name": tool.name, "source": tool.source, "access": tool.access}
            for tool in sorted(program.tools, key=lambda item: item.name)
        ],
        "workflows": sorted(workflow.name for workflow in program.workflows),
        "tests": len(program.tests),
    }


def make_pack(entry: str | Path) -> dict[str, Any]:
    """Build a pack from the program whose entry module is `entry`."""

    path = Path(entry).absolute()
    if path.is_dir():
        raise PackError(
            f"A pack is made from one program file, not a folder: {path}. "
            "Name the program inside it, such as main.kos."
        )
    if path.suffix != ".kos":
        raise PackError(f"A pack is made from a .kos program file, not {path.name}")
    program = load_program(path)
    root = path.parent
    modules: dict[str, str] = {}
    for key, recorded in sorted(program.module_hashes.items()):
        data = (root / key).read_bytes()
        if hashlib.sha256(data).hexdigest() != recorded:
            raise PackError(f"Module {key} changed while it was being packed")
        modules[key] = data.decode("utf-8")
    return {
        "schema": SCHEMA,
        "entry": path.name,
        "program_hash": program_hash(program),
        "module_hashes": dict(sorted(program.module_hashes.items())),
        "modules": modules,
        "needs": _needs(program),
    }


def write_pack(entry: str | Path, out: str | Path) -> dict[str, Any]:
    pack = make_pack(entry)
    Path(out).write_text(
        json.dumps(pack, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="",
    )
    return pack


def _safe_key(key: Any) -> bool:
    if not isinstance(key, str) or not key or "\\" in key or ":" in key:
        return False
    parts = PurePosixPath(key).parts
    return (
        key.endswith(".kos")
        and not key.startswith("/")
        and all(part not in ("", ".", "..") for part in parts)
    )


def read_pack(path: str | Path) -> dict[str, Any]:
    """Read a pack file and check its shape. Does not load the program."""

    source = Path(path)
    try:
        if source.stat().st_size > MAX_PACK_BYTES:
            raise PackError(f"Pack is larger than {MAX_PACK_BYTES} bytes")
        pack = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        if isinstance(exc, PackError):
            raise
        raise PackError(f"Cannot read pack {source}: {exc}") from exc
    if not isinstance(pack, dict) or pack.get("schema") != SCHEMA:
        raise PackError(f"Not a {SCHEMA} pack")
    modules = pack.get("modules")
    hashes = pack.get("module_hashes")
    if (
        not isinstance(modules, dict)
        or not isinstance(hashes, dict)
        or not modules
        or set(modules) != set(hashes)
        or not all(_safe_key(key) for key in modules)
        or not all(isinstance(text, str) for text in modules.values())
    ):
        raise PackError("Pack modules are missing, unsafe, or do not match its hashes")
    if pack.get("entry") not in modules:
        raise PackError("Pack entry is not one of its modules")
    if not isinstance(pack.get("program_hash"), str):
        raise PackError("Pack has no program hash")
    return pack


def _write_modules(pack: dict[str, Any], directory: Path) -> None:
    for key, text in pack["modules"].items():
        target = directory / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))


@dataclass(frozen=True)
class PackReport:
    entry: str
    program_hash: str
    modules: tuple[str, ...]
    needs: dict[str, Any]
    tests: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry": self.entry,
            "program_hash": self.program_hash,
            "modules": list(self.modules),
            "needs": self.needs,
            "tests": list(self.tests),
        }


def check_pack(path: str | Path, *, run_tests: bool = False) -> PackReport:
    """Prove a pack holds what it says, and report what it would ask for.

    Nothing in the pack is run, except its own tests when `run_tests` is set;
    those use scripted answers and reach no model, tool, or file.
    """

    pack = read_pack(path)
    for key, text in pack["modules"].items():
        if (
            hashlib.sha256(text.encode("utf-8")).hexdigest()
            != pack["module_hashes"][key]
        ):
            raise PackError(f"Module {key} does not match its recorded hash")
    with tempfile.TemporaryDirectory(prefix="kosika-pack-") as directory:
        _write_modules(pack, Path(directory))
        try:
            program = load_program(Path(directory) / pack["entry"])
        except ValueError as exc:
            raise PackError(f"Pack does not load: {exc}") from exc
        actual = program_hash(program)
        if actual != pack["program_hash"]:
            raise PackError(
                "Pack loads to a different program than it records: "
                f"{actual} is not {pack['program_hash']}"
            )
        if set(program.module_hashes) != set(pack["modules"]):
            raise PackError("Pack holds modules its program does not use")
        results: tuple[dict[str, Any], ...] = ()
        if run_tests:
            from .testing import run_tests as run_program_tests

            results = tuple(r.to_dict() for r in run_program_tests(program))
        return PackReport(
            pack["entry"],
            actual,
            tuple(sorted(pack["modules"])),
            _needs(program),
            results,
        )


def unpack(path: str | Path, directory: str | Path) -> PackReport:
    """Check a pack, then write its modules into a new or empty directory."""

    report = check_pack(path)
    target = Path(directory)
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise PackError(f"Refusing to unpack into {target}: it is not empty")
    target.mkdir(parents=True, exist_ok=True)
    _write_modules(read_pack(path), target)
    return report


def to_text(report: PackReport) -> str:
    needs = report.needs
    lines = [
        f"Program  {report.program_hash}",
        f"Entry    {report.entry}",
        f"Modules  {', '.join(report.modules)}",
        f"Runs     {', '.join(needs['workflows']) or 'nothing'}",
        "",
        "To run it, this device must grant:",
        f"  models: {', '.join(needs['models']) or 'none'}",
    ]
    if needs["tools"]:
        for tool in needs["tools"]:
            kind = "changes the world" if tool["access"] == "execute" else "read-only"
            lines.append(f"  tool {tool['name']} from {tool['source']} ({kind})")
    else:
        lines.append("  tools: none")
    if report.tests:
        failed = [test for test in report.tests if not test["passed"]]
        lines += [
            "",
            f"Tests    {len(report.tests) - len(failed)} passed, {len(failed)} failed",
        ]
        lines += [f"  FAIL {test['name']}: {test['message']}" for test in failed]
    elif needs["tests"]:
        lines += ["", f"Tests    {needs['tests']} included; add --test to run them"]
    return "\n".join(lines)
