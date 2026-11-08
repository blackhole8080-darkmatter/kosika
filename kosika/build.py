"""Building a project: a pack, a manifest, and a hash that does not move.

`kosika build` turns the folder a project file describes into an artifact:

    build/program.kpack   the pack of the program the project names
    build/manifest.json   which targets were built, what the program needs,
                          the hash of every file written, and who built it

The build hash is taken over content only: the schema, the project's name and
entry, the targets, the program's identity and module hashes, what the program
needs, and the digest of each file written. It is deliberately not taken over
this device's signing key, so two machines with different keys build the same
hash and carry different signatures. A signature says who built it; it never
changes what it is.

The whole module graph is checked first (`report_path`), so a project that
does not check writes nothing at all rather than a partial artifact. Building
again with nothing changed writes nothing either, and says so with
`changed = False`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .diagnostics import report_path
from .modules import load_program
from .pack import PackError, make_pack, read_key
from .project import KEY_FILE, Project, read_project
from .signature import sign as ed25519_sign

SCHEMA = "kosika.build/v1"
MANIFEST_NAME = "manifest.json"
PACK_NAME = "program.kpack"
SIGNATURE_ALG = "ed25519"
#: What a build's hash and signature leave out: themselves.
UNSIGNED = ("build_hash", "signature")


class BuildError(ValueError):
    """A project cannot be built, in words for a person."""


@dataclass(frozen=True)
class Built:
    """What a build wrote, and the manifest that records it."""

    folder: Path
    out: Path
    manifest: Mapping[str, Any]
    changed: bool

    @property
    def build_hash(self) -> str:
        """The identity of what was built, over content only."""

        return str(self.manifest["build_hash"])

    @property
    def program_hash(self) -> str:
        """The identity of the program that was packed."""

        return str(self.manifest["program_hash"])

    @property
    def targets(self) -> tuple[str, ...]:
        return tuple(str(target["name"]) for target in self.manifest["targets"])

    @property
    def files(self) -> tuple[str, ...]:
        return tuple(sorted(self.manifest["files"]))

    @property
    def signer(self) -> str | None:
        """The public key of the device that built this, or None."""

        signature = self.manifest.get("signature")
        if isinstance(signature, Mapping):
            public = signature.get("public_key")
            return str(public) if public else None
        return None

    def to_dict(self) -> dict[str, Any]:
        """The manifest, plus where it was written and whether it changed."""

        return {**dict(self.manifest), "changed": self.changed, "out": str(self.out)}


def build(
    folder: str | Path = ".",
    out: str | Path | None = None,
    key_path: str | Path | None = None,
) -> Built:
    """Build the project in `folder` into `out`, and sign the manifest."""

    project = read_project(folder)
    entry = project.folder / project.entry
    _check_graph(project, entry)
    program = load_program(entry)
    _check_modules(project, sorted(program.module_hashes))
    _check_targets(project, [workflow.name for workflow in program.workflows], entry)
    try:
        pack = make_pack(entry)
    except PackError as exc:
        raise BuildError(f"The pack cannot be made: {exc}") from exc
    written = {PACK_NAME: _canonical(pack) + b"\n"}
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "project": project.name,
        "entry": project.entry,
        "program_hash": pack["program_hash"],
        "module_hashes": pack["module_hashes"],
        "targets": [
            {"name": target.name, "workflow": target.workflow, "output": target.output}
            for target in project.targets
        ],
        "needs": pack["needs"],
        "files": {name: _digest(data) for name, data in sorted(written.items())},
    }
    build_hash = _digest(manifest_payload(payload))
    target_dir = Path(out).absolute() if out is not None else project.folder / "build"
    unchanged = _read_manifest(target_dir / MANIFEST_NAME)
    if (
        unchanged is not None
        and unchanged.get("build_hash") == build_hash
        and _holds(target_dir, payload["files"])
    ):
        return Built(project.folder, target_dir, unchanged, False)
    seed, public = read_key(
        Path(key_path) if key_path is not None else project.folder / KEY_FILE
    )
    manifest = dict(payload)
    manifest["build_hash"] = build_hash
    manifest["signature"] = {
        "alg": SIGNATURE_ALG,
        "public_key": public.hex(),
        "signature": ed25519_sign(seed, manifest_payload(payload)).hex(),
    }
    target_dir.mkdir(parents=True, exist_ok=True)
    for name, data in sorted(written.items()):
        _write(target_dir / name, data)
    _write(target_dir / MANIFEST_NAME, _pretty(manifest) + b"\n")
    return Built(project.folder, target_dir, manifest, True)


def manifest_payload(manifest: Mapping[str, Any]) -> bytes:
    """What a build's hash and signature cover: everything but themselves."""

    return _canonical(
        {key: value for key, value in manifest.items() if key not in UNSIGNED}
    )


def to_text(built: Built) -> str:
    """What a build did, in the words a person reads."""

    targets = ", ".join(built.targets) or "(none)"
    return "\n".join(
        (
            f"Project   {built.manifest['project']}",
            f"Entry     {built.manifest['entry']}",
            f"Program   {built.program_hash}",
            f"Targets   {targets}",
            f"Build     {built.build_hash}",
            f"Signer    {built.signer or '(unsigned)'}",
            f"Files     {', '.join(built.files)}",
            f"State     {'built' if built.changed else 'unchanged'}",
            f"Written   {built.out}",
        )
    )


def _check_graph(project: Project, entry: Path) -> None:
    """Refuse the build before it writes anything, if the program does not check."""

    report = report_path(entry)
    if report.get("ok"):
        return
    codes = ", ".join(str(code) for code in report.get("codes") or ())
    counted = report.get("error_count")
    raise BuildError(
        f"{entry.name} does not check, so nothing was built: {counted} error(s)"
        + (f" ({codes})" if codes else "")
    )


def _check_modules(project: Project, loaded: list[str]) -> None:
    """A module the project declares must be one its program actually loads."""

    for name in project.modules:
        if name not in loaded:
            known = ", ".join(loaded) or "none"
            raise BuildError(
                f"The project declares {name}, which its program does not load. "
                f"It loads: {known}"
            )


def _check_targets(project: Project, workflows: list[str], entry: Path) -> None:
    """A target names a workflow the entry program declares, or it is refused."""

    for target in project.targets:
        if target.workflow not in workflows:
            known = ", ".join(workflows) or "none"
            raise BuildError(
                f"Target {target.name} names the workflow {target.workflow!r}, "
                f"which {entry.name} does not declare. It declares: {known}"
            )


def _canonical(value: Any) -> bytes:
    """The bytes a hash is taken over: sorted keys, no spaces."""

    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _pretty(value: Any) -> bytes:
    """The bytes a person reads: sorted keys, indented."""

    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False).encode(
        "utf-8"
    )


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _read_manifest(path: Path) -> dict[str, Any] | None:
    """The manifest already written here, or None when there is not one."""

    try:
        kept = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return kept if isinstance(kept, dict) else None


def _holds(folder: Path, digests: Mapping[str, Any]) -> bool:
    """Whether every file the manifest names is there and still as it was."""

    for name, recorded in digests.items():
        try:
            data = (folder / name).read_bytes()
        except OSError:
            return False
        if _digest(data) != recorded:
            return False
    return True


__all__ = [
    "MANIFEST_NAME",
    "PACK_NAME",
    "SCHEMA",
    "BuildError",
    "Built",
    "build",
    "manifest_payload",
    "to_text",
]
