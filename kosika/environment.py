"""Environments: what a project's programs may reach, written down once.

A project keeps a file named `kosika.env` beside its programs. Each
`environment` in it names the models that answer, the MCP servers its tools
reach, the folder, sites and shell it may touch, its budgets, and the folder
where its traces, journals and memories are kept:

    environment live {
      model writer: "qwen2.5:7b" at "http://localhost:11434" protocol ollama
      mcp urlscan: "python servers/urlscan.py" pinned "urlscan.manifest.json"
      allow folder "./notes"
      allow shell
      allow site "en.wikipedia.org"
      budget 20 model calls, 50 tool calls, 300 seconds
      keep "./.kosika/live"
    }

    environment test {
      models answer from "fixtures/answers.json"
      keep "./.kosika/test"
    }

`kosika run main.kos --env live` runs with exactly that: a run given an
environment takes no model, server or grant from the command line, so what
it may reach is what the file says and nothing else. Paths are read from
the file's own folder.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .lexer import Token, lex

ENVIRONMENT_FILE = "kosika.env"
PROTOCOLS = ("openai", "ollama")


class EnvironmentError_(ValueError):
    """An environment file that cannot be read, or an environment not in it."""


@dataclass(frozen=True)
class ModelRoute:
    model: str
    url: str | None = None
    protocol: str = "openai"


@dataclass(frozen=True)
class McpRoute:
    command: tuple[str, ...]
    manifest: Path


@dataclass
class Environment:
    name: str
    folder: Path
    models: dict[str, ModelRoute] = field(default_factory=dict)
    fixture: Path | None = None
    mcp: dict[str, McpRoute] = field(default_factory=dict)
    allow_folder: Path | None = None
    allow_shell: bool = False
    sites: list[str] = field(default_factory=list)
    budget: dict[str, int | float] = field(default_factory=dict)
    keep: Path | None = None

    def trace_path(self) -> Path | None:
        """Where a run's trace goes: one file a run, named by when it began."""

        if self.keep is None:
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        return self.keep / "traces" / f"run-{stamp}.jsonl"

    def journal_path(self, program: Path, workflow: str, inputs: Any) -> Path | None:
        """Where a run's journal goes: one a run, named by its program,
        workflow and input, so that the same run finds it again."""

        if self.keep is None:
            return None
        named = json.dumps(
            [str(program.resolve()), workflow, inputs],
            sort_keys=True,
            ensure_ascii=True,
        )
        return (
            self.keep / "journals" / (hashlib.sha256(named.encode()).hexdigest()[:16])
        )

    def memory_folder(self) -> Path | None:
        return None if self.keep is None else self.keep / "memory"


class _Reader:
    def __init__(self, tokens: list[Token], where: Path):
        self.tokens, self.index, self.where = tokens, 0, where

    @property
    def current(self) -> Token:
        return self.tokens[self.index]

    def fail(self, message: str) -> None:
        raise EnvironmentError_(f"{self.where}: {message}")

    def check(self, value: str) -> bool:
        token = self.current
        return token.kind in ("IDENT", "SYMBOL", "OP") and token.value == value

    def accept(self, value: str) -> bool:
        if self.check(value):
            self.index += 1
            return True
        return False

    def expect(self, value: str) -> Token:
        if not self.check(value):
            token = self.current
            self.fail(
                f"Expected {value!r} at {token.line}:{token.column}, got {token.value!r}"
            )
        token = self.current
        self.index += 1
        return token

    def name(self) -> str:
        token = self.current
        if token.kind != "IDENT":
            self.fail(
                f"Expected a name at {token.line}:{token.column}, got {token.value!r}"
            )
        self.index += 1
        return token.value

    def text(self) -> str:
        token = self.current
        if token.kind != "STRING":
            self.fail(
                f"Expected text in quotes at {token.line}:{token.column}, got {token.value!r}"
            )
        self.index += 1
        return token.value

    def number(self) -> int | float:
        token = self.current
        if token.kind != "NUMBER":
            self.fail(
                f"Expected a number at {token.line}:{token.column}, got {token.value!r}"
            )
        self.index += 1
        return float(token.value) if "." in token.value else int(token.value)


def _command(reader: _Reader, folder: Path) -> tuple[str, ...]:
    """A server's command: a text split at spaces, or a list of texts. A part
    that names a file in the environment's folder is given as its full path."""

    if reader.accept("["):
        parts: list[str] = []
        while not reader.accept("]"):
            parts.append(reader.text())
            reader.accept(",")
    else:
        parts = reader.text().split()
    if not parts:
        reader.fail("An MCP server needs a command")
    return tuple(
        str((folder / part).resolve()) if (folder / part).is_file() else part
        for part in parts
    )


def read_environments(path: Path) -> dict[str, Environment]:
    """Every environment in a `kosika.env` file."""

    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EnvironmentError_(f"Cannot read {path}: {exc}") from exc
    try:
        tokens = lex(source)
    except ValueError as exc:
        raise EnvironmentError_(f"{path}: {exc}") from exc
    reader = _Reader(tokens, path)
    folder = path.parent.resolve()
    found: dict[str, Environment] = {}
    while reader.current.kind != "EOF":
        reader.expect("environment")
        name = reader.name()
        if name in found:
            reader.fail(f"Environment {name!r} is declared twice")
        environment = Environment(name, folder)
        reader.expect("{")
        while not reader.accept("}"):
            token = reader.current
            if reader.accept("model"):
                declared = reader.name()
                reader.expect(":")
                if declared in environment.models:
                    reader.fail(f"Environment {name!r} names model {declared!r} twice")
                model = reader.text()
                url = reader.text() if reader.accept("at") else None
                protocol = "openai"
                if reader.accept("protocol"):
                    protocol = reader.name()
                    if protocol not in PROTOCOLS:
                        reader.fail(
                            f"Environment {name!r} model {declared!r} protocol must be "
                            f"one of {', '.join(PROTOCOLS)}, not {protocol!r}"
                        )
                environment.models[declared] = ModelRoute(model, url, protocol)
            elif reader.accept("models"):
                reader.expect("answer")
                reader.expect("from")
                if environment.fixture is not None:
                    reader.fail(f"Environment {name!r} answers models from two files")
                environment.fixture = (folder / reader.text()).resolve()
            elif reader.accept("mcp"):
                source = reader.name()
                reader.expect(":")
                if source in environment.mcp:
                    reader.fail(
                        f"Environment {name!r} names MCP source {source!r} twice"
                    )
                command = _command(reader, folder)
                reader.expect("pinned")
                environment.mcp[source] = McpRoute(
                    command, (folder / reader.text()).resolve()
                )
            elif reader.accept("allow"):
                if reader.accept("folder"):
                    if environment.allow_folder is not None:
                        reader.fail(
                            f"Environment {name!r} allows a second folder; it may allow one"
                        )
                    environment.allow_folder = (folder / reader.text()).resolve()
                elif reader.accept("shell"):
                    environment.allow_shell = True
                elif reader.accept("site"):
                    environment.sites.append(reader.text())
                else:
                    word = reader.current
                    reader.fail(
                        f"Expected 'folder', 'shell' or 'site' at {word.line}:{word.column}, "
                        f"got {word.value!r}"
                    )
            elif reader.accept("budget"):
                while True:
                    amount = reader.number()
                    if reader.accept("model"):
                        reader.expect("calls")
                        kind = "model_calls"
                    elif reader.accept("tool"):
                        reader.expect("calls")
                        kind = "tool_calls"
                    else:
                        reader.expect("seconds")
                        kind = "seconds"
                    if kind in environment.budget:
                        reader.fail(
                            f"Environment {name!r} budget names {kind.replace('_', ' ')} more than once"
                        )
                    whole = type(amount) is int and 1 <= amount <= 1_000_000
                    if kind == "seconds":
                        whole = 0 < amount <= 86_400
                    if not whole:
                        reader.fail(
                            f"Environment {name!r} budget for {kind.replace('_', ' ')} is out of range, got {amount}"
                        )
                    environment.budget[kind] = amount
                    if not reader.accept(","):
                        break
            elif reader.accept("keep"):
                if environment.keep is not None:
                    reader.fail(f"Environment {name!r} keeps its things in two places")
                environment.keep = (folder / reader.text()).resolve()
            else:
                reader.fail(
                    f"Unexpected {token.value!r} at {token.line}:{token.column} in environment "
                    f"{name!r}; an environment has model, models, mcp, allow, budget and keep"
                )
        if environment.fixture is not None and environment.models:
            reader.fail(
                f"Environment {name!r} answers models from a file and also names model "
                "servers; choose one"
            )
        if environment.allow_shell and environment.allow_folder is None:
            reader.fail(
                f"Environment {name!r} allows the shell but no folder for it to work in"
            )
        found[name] = environment
    return found


def find_environment(program: Path, name: str) -> Environment:
    """The environment `name` in the `kosika.env` beside a program, or in
    the nearest folder above it that has one."""

    for folder in [program.resolve().parent, *program.resolve().parent.parents]:
        candidate = folder / ENVIRONMENT_FILE
        if candidate.is_file():
            environments = read_environments(candidate)
            if name not in environments:
                have = ", ".join(sorted(environments)) or "none"
                raise EnvironmentError_(
                    f"{candidate} has no environment {name!r}; it has: {have}"
                )
            return environments[name]
    raise EnvironmentError_(
        f"No {ENVIRONMENT_FILE} found beside {program} or in a folder above it"
    )


#: Run options an environment decides; given with --env, they are refused.
DECIDED = (
    ("model", "--model"),
    ("model_url", "--model-url"),
    ("model_binding", "--model-binding"),
    ("model_fixture", "--model-fixture"),
    ("mcp_server", "--mcp-server"),
    ("mcp_manifest", "--mcp-manifest"),
    ("allow_folder", "--allow-folder"),
    ("allow_shell", "--allow-shell"),
    ("allow_site", "--allow-site"),
    ("journal", "--journal"),
    ("binding_id", "--binding-id"),
    ("memory", "--memory"),
    ("notify", "--notify"),
    # An environment cannot name a database yet (see KOSIKA_DATA_ROADMAP.md,
    # Phase 4), so granting one alongside --env would widen what the file
    # allows.
    ("database", "--database"),
)
