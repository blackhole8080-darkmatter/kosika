"""The project file: what a folder of Kosika is, and what is built from it.

A program is one `.kos` file plus the modules it pulls in. A project is that
folder seen as a unit: the file to start from, the targets built from it, and
the modules it is made of, said once in `kosika.toml`:

    name = "greeter"
    entry = "main.kos"
    modules = ["lib/greet.kos"]

    [targets.pack]
    workflow = "greet"
    output = "pack"

    [environments.local]
    models = ["voice"]

    [policy]
    allow_shell = false

Nothing in this file grants authority. Models, tools and approvals stay with
the device that runs a program, exactly as a pack already promises: the file
names what is built and what it needs, never what it may do.

`targets` is where "build" gets a meaning beyond "check". Each target names a
workflow the entry program declares, so a target that names one that is not
there is refused rather than silently building the whole program, and each
names the shape it is built into.

TOML is read with `tomllib` where the interpreter has it (3.11 and later) and
with the reader below on 3.10, which the package still supports and CI still
runs. That reader covers the part of TOML a project file uses: tables, arrays,
strings, integers and booleans, one statement per line. A file that needs more
than that is still read by `tomllib` on a current interpreter, so the subset
limits the older interpreter and never the format.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_FILE = "kosika.toml"
#: The shapes a target may be built into. One today: the pack.
OUTPUTS = ("pack",)
#: The key that signs a build, under the project's own folder.
KEY_FILE = Path(".kosika") / "pack-key.json"


class ProjectError(ValueError):
    """A project file cannot be read, or does not say what a build needs."""


@dataclass(frozen=True)
class Target:
    """One thing a project builds: a workflow, and the shape it is built into."""

    name: str
    workflow: str
    output: str = "pack"


@dataclass(frozen=True)
class Project:
    """A folder of Kosika, as its project file describes it."""

    folder: Path
    name: str
    entry: str
    modules: tuple[str, ...]
    targets: tuple[Target, ...]
    environments: dict[str, Any]
    policy: dict[str, Any]


def read_project(folder: str | Path = ".") -> Project:
    """Read and check the project file in `folder`."""

    root = Path(folder).absolute()
    path = root / PROJECT_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ProjectError(
            f"There is no {PROJECT_FILE} in {root}. Name the folder that holds "
            f"one, or write one that names the entry module and its targets."
        ) from None
    except OSError as exc:
        raise ProjectError(f"Cannot read {path}: {exc}") from exc
    data = load_toml(text)
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ProjectError(f'{path} has no name: add name = "..." at the top')
    entry = data.get("entry")
    if not isinstance(entry, str) or not entry.strip():
        raise ProjectError(f'{path} has no entry: add entry = "main.kos"')
    entry = entry.strip()
    _program_name(entry, path, "entry")
    if not (root / entry).is_file():
        raise ProjectError(
            f"{path} names {entry} as its entry, and there is no such file in {root}"
        )
    return Project(
        folder=root,
        name=name.strip(),
        entry=entry,
        modules=_modules(data.get("modules"), path),
        targets=_targets(data.get("targets"), path),
        environments=_tables(data.get("environments"), path, "environments"),
        policy=_tables(data.get("policy"), path, "policy"),
    )


#: The entry program `init` writes when a folder has no program yet.
DEFAULT_ENTRY = "main.kos"
DEFAULT_PROGRAM = """\
record Greeting {
  text: Text
}

fn greeting() -> Greeting {
  return {text: "Hello, world!"}
}

workflow main() -> Greeting {
  step greet {
    let result: Greeting = greeting()
    return result
  }
}
"""


@dataclass(frozen=True)
class Started:
    """What `init` wrote, and the project the folder now holds."""

    folder: Path
    project: Project
    workflow: str
    written: tuple[Path, ...]


def project_text(name: str, entry: str, workflow: str) -> str:
    """The project file for one entry program and one target."""

    return (
        f'name = "{name}"\n'
        f'entry = "{entry}"\n'
        "\n"
        "[targets.main]\n"
        f'workflow = "{workflow}"\n'
        'output = "pack"\n'
    )


def init_project(folder: str | Path = ".", *, force: bool = False) -> Started:
    """Write a project file for `folder`, and a program when it has none.

    The target names a workflow the entry program declares, which is the same
    promise `read_project` keeps: a project file cannot point a build at a
    workflow that is not there.
    """

    root = Path(folder).absolute()
    if not root.is_dir():
        raise ProjectError(f"{root} is not a folder")
    config = root / PROJECT_FILE
    if config.exists() and not force:
        raise ProjectError(
            f"{config} is already here. Read it, or run with --force to replace it."
        )
    written: list[Path] = []
    entry = _entry_program(root)
    if entry is None:
        entry = DEFAULT_ENTRY
        (root / entry).write_text(DEFAULT_PROGRAM, encoding="utf-8")
        written.append(root / entry)
    workflow = _first_workflow(root / entry)
    name = _project_name(root.name)
    config.write_text(project_text(name, entry, workflow), encoding="utf-8")
    written.append(config)
    return Started(
        folder=root,
        project=read_project(root),
        workflow=workflow,
        written=tuple(written),
    )


def _entry_program(root: Path) -> str | None:
    """The program to start from, or None when the folder holds none."""

    if (root / DEFAULT_ENTRY).is_file():
        return DEFAULT_ENTRY
    programs = sorted(path.name for path in root.glob("*.kos") if path.is_file())
    if not programs:
        return None
    if len(programs) > 1:
        raise ProjectError(
            f"{root} holds {len(programs)} programs ({', '.join(programs)}); "
            f"rename the one to start from to {DEFAULT_ENTRY}"
        )
    return programs[0]


def _first_workflow(path: Path) -> str:
    """The name of the first workflow the program declares."""

    from .modules import load_program

    try:
        program = load_program(path)
    except (OSError, ValueError) as exc:
        raise ProjectError(f"{path} cannot be read as a program: {exc}") from exc
    workflows = [workflow.name for workflow in program.workflows]
    if not workflows:
        raise ProjectError(
            f"{path} declares no workflow, so there is nothing for a target to build"
        )
    return workflows[0]


def _project_name(folder: str) -> str:
    """A project name from a folder's name: lowercase, dashed, never empty."""

    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in folder).strip(
        "-"
    )
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned or "project"


def load_toml(text: str) -> dict[str, Any]:
    """Read TOML with `tomllib` where it exists, and the subset reader otherwise."""

    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - the 3.10 interpreter
        return read_toml(text)
    loaded = tomllib.loads(text)
    if not isinstance(loaded, dict):  # pragma: no cover - a TOML document is a table
        raise ProjectError("A project file must be a table")
    return loaded


def read_toml(text: str) -> dict[str, Any]:
    """Read the part of TOML a project file uses, on an interpreter without it."""

    root: dict[str, Any] = {}
    table = root
    for number, raw in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw).strip()
        if not line:
            continue
        if line.startswith("["):
            if not line.endswith("]"):
                raise ProjectError(
                    f"{PROJECT_FILE} line {number}: a table header ends with ]"
                )
            name = line[1:-1].strip()
            if not name or name.startswith("["):
                raise ProjectError(
                    f"{PROJECT_FILE} line {number}: an array of tables is not read here"
                )
            table = _table_at(root, name, number)
            continue
        key, equals, value = line.partition("=")
        if not equals:
            raise ProjectError(
                f"{PROJECT_FILE} line {number}: {line!r} is not key = value"
            )
        key = key.strip()
        if not key:
            raise ProjectError(f"{PROJECT_FILE} line {number}: a value has no name")
        if key in table:
            raise ProjectError(
                f"{PROJECT_FILE} line {number}: {key} is set twice in its table"
            )
        table[key] = _value(value.strip(), number)
    return root


def _strip_comment(line: str) -> str:
    """The line without a `#` comment, leaving one inside a string alone."""

    quote = ""
    for at, char in enumerate(line):
        if quote:
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "#":
            return line[:at]
    return line


def _table_at(root: dict[str, Any], dotted: str, number: int) -> dict[str, Any]:
    table = root
    for part in dotted.split("."):
        part = part.strip()
        if not part:
            raise ProjectError(
                f"{PROJECT_FILE} line {number}: {dotted!r} has an empty part"
            )
        made = table.setdefault(part, {})
        if not isinstance(made, dict):
            raise ProjectError(
                f"{PROJECT_FILE} line {number}: {part} is a value, not a table"
            )
        table = made
    return table


def _value(text: str, number: int) -> Any:
    """One TOML value of the subset: a list, a string, a boolean or an integer."""

    if not text:
        raise ProjectError(f"{PROJECT_FILE} line {number}: a value is missing")
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_value(part, number) for part in _split_list(inner)]
    if text == "true":
        return True
    if text == "false":
        return False
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        inner = text[1:-1]
        return _unescape(inner) if text[0] == '"' else inner
    try:
        return int(text.replace("_", ""))
    except ValueError:
        raise ProjectError(
            f"{PROJECT_FILE} line {number}: {text!r} is not a string, number or list"
        ) from None


def _split_list(inner: str) -> list[str]:
    """The items of `[a, "b, c"]`, keeping a comma inside a string."""

    parts: list[str] = []
    current: list[str] = []
    quote = ""
    for char in inner:
        if quote:
            if char == quote:
                quote = ""
            current.append(char)
        elif char in "\"'":
            quote = char
            current.append(char)
        elif char == ",":
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


_ESCAPES = {"\\": "\\", '"': '"', "n": "\n", "t": "\t", "r": "\r"}


def _unescape(text: str) -> str:
    out: list[str] = []
    escaped = False
    for char in text:
        if escaped:
            out.append(_ESCAPES.get(char, char))
            escaped = False
        elif char == "\\":
            escaped = True
        else:
            out.append(char)
    return "".join(out)


def _program_name(name: str, path: Path, what: str) -> None:
    """A .kos file named relative to the project, and never outside it."""

    if "\\" in name or name.startswith("/") or ".." in Path(name).parts:
        raise ProjectError(
            f"{path}: {what} is {name!r}, which is not a path inside the project"
        )
    if not name.endswith(".kos"):
        raise ProjectError(f"{path}: {what} is {name!r}, which is not a .kos file")


def _modules(value: Any, path: Path) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ProjectError(
            f'{path}: modules is a list of file names, such as modules = ["lib/a.kos"]'
        )
    named: list[str] = []
    for item in value:
        _program_name(item, path, "a module")
        if item in named:
            raise ProjectError(f"{path}: module {item} is named twice")
        named.append(item)
    return tuple(named)


def _targets(value: Any, path: Path) -> tuple[Target, ...]:
    if value is None:
        return ()
    if not isinstance(value, dict):
        raise ProjectError(f"{path}: targets is a table, such as [targets.pack]")
    made: list[Target] = []
    for name, body in value.items():
        if not isinstance(body, dict):
            raise ProjectError(f"{path}: target {name} is a value, not a table")
        workflow = body.get("workflow")
        if not isinstance(workflow, str) or not workflow.strip():
            raise ProjectError(
                f'{path}: target {name} has no workflow: add workflow = "..."'
            )
        output = body.get("output", "pack")
        if output not in OUTPUTS:
            raise ProjectError(
                f"{path}: target {name} is built into {output!r}, and this device "
                f"knows {', '.join(OUTPUTS)}"
            )
        made.append(Target(name=name, workflow=workflow.strip(), output=str(output)))
    return tuple(made)


def _tables(value: Any, path: Path, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(
        isinstance(body, dict) for body in value.values()
    ):
        raise ProjectError(f"{path}: {label} is a table of tables")
    return value


__all__ = [
    "DEFAULT_ENTRY",
    "DEFAULT_PROGRAM",
    "KEY_FILE",
    "OUTPUTS",
    "PROJECT_FILE",
    "Project",
    "ProjectError",
    "Started",
    "Target",
    "init_project",
    "load_toml",
    "project_text",
    "read_project",
    "read_toml",
]

if sys.version_info < (3, 11):  # pragma: no cover - read by the 3.10 interpreter
    # Nothing to do: `load_toml` already falls back to `read_toml`.
    pass
