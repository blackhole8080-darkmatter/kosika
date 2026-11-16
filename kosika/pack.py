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
(their SHA-256 hashes), and they load to a program whose identity is the one
recorded (`kosika.identity`: a hash of the sources, which the native engine
computes the same way). Two devices that hold packs with the same program
hash run the same program.

A pack may also be signed: `kosika pack sign` covers the program hash and the
module hashes with this device's Ed25519 key, kept in `.kosika/`, and
`kosika pack check --require-signature` refuses a pack that carries no
signature, or one whose signature does not verify. A signature adds who made
the pack. It does not make the program safe: the device that runs it still
grants its own models and approves its own tools.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .ast import Program
from .identity import program_identity
from .modules import load_program
from .signature import public_key as ed25519_public
from .signature import sign as ed25519_sign
from .signature import verify as ed25519_verify

SCHEMA = "kosika.pack/v1"
MAX_PACK_BYTES = 4 * 1024 * 1024
SIGNATURE_ALG = "ed25519"
KEY_SCHEMA = "kosika.pack-key/v1"
#: Where this device's signing key lives when no other path is named.
DEFAULT_KEY = Path(".kosika") / "pack-key.json"


class PackError(ValueError):
    """A pack cannot be made, or does not hold what it says it holds."""


def program_hash(program: Program) -> str:
    """The program's identity, which both engines compute the same way."""

    return program_identity(program)


def _canonical(value: Any) -> bytes:
    """The bytes a hash or a signature is taken over: sorted keys, no spaces."""

    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def signing_payload(pack: dict[str, Any]) -> bytes:
    """What a pack's signature covers: its program and its modules' hashes."""

    return _canonical(
        {"program_hash": pack["program_hash"], "module_hashes": pack["module_hashes"]}
    )


def read_key(path: str | Path = DEFAULT_KEY) -> tuple[bytes, bytes]:
    """The seed and public key at `path`, made once and then kept.

    A key that cannot be read is refused rather than replaced: silently
    minting a new key would make a device's earlier signatures unverifiable
    without saying so.
    """

    file = Path(path)
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        seed = secrets.token_bytes(32)
        public = ed25519_public(seed)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(
            json.dumps(
                {
                    "schema": KEY_SCHEMA,
                    "alg": SIGNATURE_ALG,
                    "seed": seed.hex(),
                    "public_key": public.hex(),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        try:
            os.chmod(file, 0o600)
        except OSError:
            # Some filesystems cannot narrow a mode; the key is the owner's own.
            pass
        return seed, public
    except (OSError, ValueError) as exc:
        raise PackError(f"Cannot read signing key {file}: {exc}") from exc
    if (
        not isinstance(data, dict)
        or data.get("schema") != KEY_SCHEMA
        or data.get("alg") != SIGNATURE_ALG
    ):
        raise PackError(f"{file} is not a {KEY_SCHEMA} key")
    seed_text = data.get("seed")
    public_text = data.get("public_key")
    try:
        seed = bytes.fromhex(seed_text) if isinstance(seed_text, str) else b""
        public = bytes.fromhex(public_text) if isinstance(public_text, str) else b""
    except ValueError as exc:
        raise PackError(f"Signing key {file} is not hexadecimal") from exc
    if len(seed) != 32 or len(public) != 32:
        raise PackError(f"Signing key {file} must hold a 32-byte seed and public key")
    if ed25519_public(seed) != public:
        raise PackError(f"Signing key {file} does not match its public key")
    return seed, public


def _signature_of(pack: dict[str, Any]) -> dict[str, str] | None:
    """The verified signature a pack carries, or None when it carries none."""

    signature = pack.get("signature")
    if signature is None:
        return None
    if not isinstance(signature, dict):
        raise PackError("Pack signature is not an object")
    alg = signature.get("alg")
    public_text = signature.get("public_key")
    made = signature.get("signature")
    if alg != SIGNATURE_ALG:
        raise PackError(f"Pack signature names an unknown algorithm: {alg!r}")
    if not isinstance(public_text, str) or not isinstance(made, str):
        raise PackError("Pack signature has no public key or signature")
    try:
        public = bytes.fromhex(public_text)
        raw = bytes.fromhex(made)
    except ValueError as exc:
        raise PackError("Pack signature is not hexadecimal") from exc
    if len(public) != 32 or len(raw) != 64:
        raise PackError("Pack signature is not a 32-byte key and 64 bytes more")
    if not ed25519_verify(public, signing_payload(pack), raw):
        raise PackError("Pack signature does not match its program and modules")
    return {"alg": SIGNATURE_ALG, "public_key": public_text}


def sign_pack(path: str | Path, key_path: str | Path = DEFAULT_KEY) -> dict[str, Any]:
    """Sign a pack where it sits, proving which device made it.

    The modules are re-hashed first, so a pack that no longer holds what it
    records is refused rather than signed.
    """

    pack = read_pack(path)
    for key, text in pack["modules"].items():
        if (
            hashlib.sha256(text.encode("utf-8")).hexdigest()
            != pack["module_hashes"][key]
        ):
            raise PackError(f"Module {key} does not match its recorded hash")
    seed, public = read_key(key_path)
    pack["signature"] = {
        "alg": SIGNATURE_ALG,
        "public_key": public.hex(),
        "signature": ed25519_sign(seed, signing_payload(pack)).hex(),
    }
    Path(path).write_text(
        json.dumps(pack, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="",
    )
    return pack


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
    #: The public key a pack's signature verified under, when it has one.
    signed_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry": self.entry,
            "program_hash": self.program_hash,
            "modules": list(self.modules),
            "needs": self.needs,
            "tests": list(self.tests),
            "signed_by": self.signed_by,
        }


def check_pack(
    path: str | Path, *, run_tests: bool = False, require_signature: bool = False
) -> PackReport:
    """Prove a pack holds what it says, and report what it would ask for.

    Nothing in the pack is run, except its own tests when `run_tests` is set;
    those use scripted answers and reach no model, tool, or file. A signature
    is always verified when a pack carries one, and `require_signature` makes
    an unsigned pack a refusal instead of a weaker success.
    """

    pack = read_pack(path)
    if require_signature and pack.get("signature") is None:
        raise PackError("Pack is not signed, and a signature was required")
    signature = _signature_of(pack)
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
            signature["public_key"] if signature else None,
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
    signer = f"ed25519 {report.signed_by[:16]}…" if report.signed_by else "unsigned"
    lines = [
        f"Program  {report.program_hash}",
        f"Signer   {signer}",
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
