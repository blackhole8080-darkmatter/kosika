"""Which engine runs a Kosika program: this Python, or the native Go one.

Kosika is defined by its conformance suite, not by one interpreter. Any
program that prints what `conformance/` says is a Kosika engine, and two are
built here: the reference engine in this package, and the native engine in
`native/`, written in Go so that Kosika runs on a machine with no Python.

    kosika engines                  both engines, and whether each is built
    kosika run FILE --engine go     the same command, in the Go engine
    KOSIKA_ENGINE=go kosika test FILE

`--engine` is read and taken out of the arguments before any command is
parsed, so everything after it belongs to the other engine unchanged.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
NATIVE_SOURCE = PROJECT / "native"
NATIVE_NAME = "kosika-native.exe" if os.name == "nt" else "kosika-native"

#: The engine a command runs on when nothing says otherwise: the one with
#: every feature, which the other engines are held to.
DEFAULT = "python"


class EngineError(RuntimeError):
    """An engine cannot be used, in words a person can act on."""


def native_build_path() -> Path:
    """Where the Go engine is built: ~/.kosika/bin, beside Kosika's own files."""

    return Path.home() / ".kosika" / "bin" / NATIVE_NAME


def native_binary(build: bool = True) -> Path | None:
    """The Go engine, or None when it is not here and cannot be built.

    The build is redone when the Go source is newer than the program, so a
    change to the engine is never left behind while an older one is used.
    """

    if not (NATIVE_SOURCE / "main.go").is_file():
        return None
    target = native_build_path()
    try:
        newest = max(
            path.stat().st_mtime
            for path in [*NATIVE_SOURCE.glob("*.go"), *NATIVE_SOURCE.glob("std/*.kos")]
        )
    except ValueError:
        return None
    if target.is_file() and target.stat().st_mtime >= newest:
        return target
    if not build:
        return target if target.is_file() else None
    go = shutil.which("go")
    if not go:
        return target if target.is_file() else None
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(  # noqa: S603 - our own source, our own compiler
            [go, "build", "-o", str(target), "."],
            cwd=NATIVE_SOURCE,
            capture_output=True,
            timeout=300,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return target if target.is_file() else None
    return target


@dataclass(frozen=True)
class Engine:
    """One way of running Kosika, and how to start it."""

    name: str
    description: str
    #: Where a compiled engine is, when it is known: the Go program.
    binary: Path | None = None
    #: Whether the engine is built from `native/` rather than found in Python.
    built: bool = False

    def command(self) -> list[str] | None:
        """The program and arguments that start this engine, or None."""

        if self.binary is not None:
            return [str(self.binary)]
        if self.built:
            found = native_binary()
            return [str(found)] if found is not None else None
        return [sys.executable, "-m", "kosika.cli"]

    def available(self) -> bool:
        return self.command() is not None


ENGINE_PYTHON = Engine(
    "python",
    "The reference engine, in this package: every feature is here first.",
)
ENGINE_GO = Engine(
    "go",
    (
        "The native engine in native/, built with Go so Kosika runs on a "
        "machine with no Python. It is faster and lags the reference: what it "
        "cannot do yet it refuses by name."
    ),
    built=True,
)

#: The engines, in the order they are described.
ENGINES = {engine.name: engine for engine in (ENGINE_PYTHON, ENGINE_GO)}


def get(name: str) -> Engine:
    """The engine called `name`, or why there is none."""

    engine = ENGINES.get(name)
    if engine is None:
        known = ", ".join(ENGINES)
        raise EngineError(f"There is no {name!r} engine; the engines are {known}")
    return engine


def select(explicit: str | None = None) -> Engine:
    """The engine asked for, else KOSIKA_ENGINE, else the reference engine."""

    return get(explicit or os.environ.get("KOSIKA_ENGINE") or DEFAULT)


def take_engine(argv: list[str]) -> tuple[str | None, list[str]]:
    """The engine named by `--engine NAME`, and the arguments without it.

    It is taken out wherever it is written, so a command reaches its engine
    with exactly the flags it was written with; `--engine` belongs to Kosika
    itself, not to any one engine.
    """

    name: str | None = None
    rest: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--engine":
            if index + 1 >= len(argv):
                raise EngineError(
                    "`--engine` needs a name: "
                    + ", ".join(ENGINES)
                )
            name = argv[index + 1]
            index += 2
            continue
        if token.startswith("--engine="):
            name = token.split("=", 1)[1]
            index += 1
            continue
        rest.append(token)
        index += 1
    return name, rest


def delegate(engine: Engine, argv: list[str]) -> int:
    """Run `argv` on `engine`, and give back what it ended with."""

    command = engine.command()
    if command is None:
        raise EngineError(
            f"The {engine.name} engine is not built. Build it with "
            f"`go build -o {native_build_path()} .` in {NATIVE_SOURCE}, or "
            "install Go and let Kosika build it."
        )
    return subprocess.run(  # noqa: S603 - the engine's own command
        [*command, *argv], check=False
    ).returncode


def describe() -> list[str]:
    """What `kosika engines` prints."""

    lines = ["Kosika engines:"]
    for engine in ENGINES.values():
        command = engine.command()
        lines.append(f"  {engine.name:<7} {command[0] if command else 'not built'}")
        if command is not None and len(command) > 1:
            lines.append(f"          {' '.join(command[1:])}")
        lines.append(f"          {engine.description}")
    lines.append("")
    lines.append(
        "Choose one with --engine NAME, or set KOSIKA_ENGINE once for every command."
    )
    return lines


__all__ = [
    "DEFAULT",
    "ENGINE_GO",
    "ENGINE_PYTHON",
    "ENGINES",
    "Engine",
    "EngineError",
    "delegate",
    "describe",
    "get",
    "native_binary",
    "native_build_path",
    "select",
    "take_engine",
]
