"""Shell and file access for Kosika programs, behind approval every time.

A program asks for system access by declaring a tool with a `system` source:

    tool run_tests {
      source system "shell"
      operation: "python"          // the one program this tool may start
      access: execute
      input: ShellArguments        // {args: Text[]}
      output: ShellResult          // {exit_code: Number, stdout: Text, stderr: Text}
    }

    tool save_report {
      source system "files"
      operation: "write"           // read, write, or list
      access: execute
      input: FileWrite             // {path: Text, text: Text}
      output: FileWritten          // {bytes: Number}
    }

Three independent things must all be true before anything happens:

1. The declaration. A system tool must have `access: execute`, so the checker
   forces every workflow that uses one to state `require approval`. The one
   exception is a files tool whose operation is `read` or `list`: it may be
   `access: observe`, because it cannot change anything. It still needs the
   folder grant, and can see only inside that folder.
2. The host grant. Nothing here is bound unless the person running the program
   passes a folder (and, for shell tools, allows shell). A declaration alone
   reaches nothing.
3. The approval. Because the tool is `execute`, the runtime pauses before each
   call until a matching approval exists for that exact tool and arguments.

Limits that hold even after approval: a shell tool starts only the program
named in its declaration, with arguments passed as a list and never through a
shell, inside the granted folder, for at most 60 seconds. File tools touch only
paths inside the granted folder; a path that leaves it, or passes through a
symlink, is refused. This is not an operating-system sandbox: an approved
program runs with the authority of the person who approved it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from .ast import Program, ToolDecl

SYSTEM_PREFIX = "system:"
SYSTEM_KINDS = ("shell", "files")
FILE_OPERATIONS = ("read", "write", "create", "list")
READ_ONLY_OPERATIONS = ("read", "list")
SHELL_TIMEOUT_SECONDS = 60
MAX_OUTPUT_CHARACTERS = 64 * 1024
MAX_FILE_BYTES = 1024 * 1024
MAX_LISTED_NAMES = 1000

#: The record fields each kind of system tool must take and return.
SHAPES: dict[str, tuple[dict[str, str], dict[str, str]]] = {
    "shell": (
        {"args": "Text[]"},
        {"exit_code": "Number", "stdout": "Text", "stderr": "Text"},
    ),
    "read": ({"path": "Text"}, {"text": "Text"}),
    "write": ({"path": "Text", "text": "Text"}, {"bytes": "Number"}),
    # Like write, but refuses when anything is already at that path, so it
    # can add to a folder and never destroy what is there.
    "create": ({"path": "Text", "text": "Text"}, {"bytes": "Number"}),
    "list": ({"path": "Text"}, {"names": "Text[]"}),
}


class SystemToolError(ValueError):
    """A system tool is declared, granted, or called in a way that is refused."""


def system_kind(tool: ToolDecl) -> str | None:
    """`shell` or `files` for a system tool, else None."""

    if not tool.source.startswith(SYSTEM_PREFIX):
        return None
    return tool.source[len(SYSTEM_PREFIX) :]


def declaration_problem(program: Program, tool: ToolDecl) -> str | None:
    """Why a system tool's declaration is unacceptable, if it is."""

    kind = system_kind(tool)
    if kind is None:
        return None
    if kind not in SYSTEM_KINDS:
        return (
            f"Tool {tool.name!r} has unknown system source {kind!r}; "
            f"use one of: {', '.join(SYSTEM_KINDS)}"
        )
    # Looking is not changing. A files tool that only reads or lists may be
    # `observe`, which needs the folder grant but no approval for each call.
    # Writing a file and starting a program always need approval.
    looks_only = kind == "files" and tool.operation in READ_ONLY_OPERATIONS
    if tool.access != "execute" and not looks_only:
        return (
            f"Tool {tool.name!r} uses system {kind} and must declare "
            "access: execute, so that every call needs approval"
        )
    if kind == "files" and tool.operation not in FILE_OPERATIONS:
        return (
            f"Tool {tool.name!r} has unknown files operation {tool.operation!r}; "
            f"use one of: {', '.join(FILE_OPERATIONS)}"
        )
    if kind == "shell" and (
        not tool.operation.strip() or " " in tool.operation.strip()
    ):
        return (
            f"Tool {tool.name!r} must name exactly one program in operation, "
            "with its arguments passed in args"
        )
    wanted_input, wanted_output = SHAPES["shell" if kind == "shell" else tool.operation]
    records = {
        record.name: {field.name: field.type_name for field in record.fields}
        for record in program.records
    }
    for label, record_name, wanted in (
        ("input", tool.input, wanted_input),
        ("output", tool.output, wanted_output),
    ):
        if records.get(record_name or "") != wanted:
            shape = ", ".join(f"{name}: {kind_}" for name, kind_ in wanted.items())
            return (
                f"Tool {tool.name!r} {label} record must have exactly the "
                f"fields {{{shape}}}"
            )
    return None


def _inside(folder: Path, relative: str) -> Path:
    """Resolve `relative` inside `folder`, refusing anything that leaves it."""

    candidate = Path(relative)
    if not relative.strip() or candidate.is_absolute() or candidate.drive:
        raise SystemToolError(
            f"path {relative!r} must be relative to the granted folder"
        )
    current = folder
    for part in candidate.parts:
        if part in ("..", ""):
            raise SystemToolError(f"path {relative!r} leaves the granted folder")
        current = current / part
        if current.is_symlink():
            raise SystemToolError(f"path {relative!r} passes through a symlink")
    resolved = current.resolve()
    if resolved != folder and folder not in resolved.parents:
        raise SystemToolError(f"path {relative!r} leaves the granted folder")
    return resolved


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARACTERS:
        return text
    return text[:MAX_OUTPUT_CHARACTERS] + "\n[output cut]"


def _shell(tool: ToolDecl, folder: Path) -> Callable[[ToolDecl, dict[str, Any]], Any]:
    def run_program(_tool: ToolDecl, arguments: dict[str, Any]) -> dict[str, Any]:
        executable = shutil.which(tool.operation)
        if executable is None:
            raise SystemToolError(f"program {tool.operation!r} was not found")
        try:
            completed = subprocess.run(
                [executable, *arguments["args"]],
                cwd=folder,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=SHELL_TIMEOUT_SECONDS,
                stdin=subprocess.DEVNULL,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise SystemToolError(
                f"program {tool.operation!r} ran longer than "
                f"{SHELL_TIMEOUT_SECONDS} seconds and was stopped"
            ) from exc
        return {
            "exit_code": completed.returncode,
            "stdout": _clip(completed.stdout),
            "stderr": _clip(completed.stderr),
        }

    return run_program


def _files(tool: ToolDecl, folder: Path) -> Callable[[ToolDecl, dict[str, Any]], Any]:
    def read(_tool: ToolDecl, arguments: dict[str, Any]) -> dict[str, Any]:
        path = _inside(folder, arguments["path"])
        if not path.is_file():
            raise SystemToolError(f"{arguments['path']!r} is not a file")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise SystemToolError(f"{arguments['path']!r} is larger than 1 MiB")
        try:
            return {"text": path.read_text(encoding="utf-8")}
        except UnicodeDecodeError as exc:
            raise SystemToolError(f"{arguments['path']!r} is not UTF-8 text") from exc

    def write(_tool: ToolDecl, arguments: dict[str, Any]) -> dict[str, Any]:
        data = arguments["text"].encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise SystemToolError("text to write is larger than 1 MiB")
        path = _inside(folder, arguments["path"])
        if path.is_dir():
            raise SystemToolError(f"{arguments['path']!r} is a folder")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"bytes": len(data)}

    def create(_tool: ToolDecl, arguments: dict[str, Any]) -> dict[str, Any]:
        data = arguments["text"].encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise SystemToolError("text to write is larger than 1 MiB")
        path = _inside(folder, arguments["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # "x" fails if the path exists, checked and created in one step.
            with path.open("xb") as stream:
                stream.write(data)
        except FileExistsError as exc:
            raise SystemToolError(
                f"{arguments['path']!r} already exists; create never overwrites"
            ) from exc
        return {"bytes": len(data)}

    def list_names(_tool: ToolDecl, arguments: dict[str, Any]) -> dict[str, Any]:
        path = (
            folder
            if arguments["path"] in (".", "")
            else _inside(folder, arguments["path"])
        )
        if not path.is_dir():
            raise SystemToolError(f"{arguments['path']!r} is not a folder")
        names = sorted(
            entry.name + ("/" if entry.is_dir() else "") for entry in path.iterdir()
        )
        if len(names) > MAX_LISTED_NAMES:
            raise SystemToolError(f"folder has more than {MAX_LISTED_NAMES} entries")
        return {"names": names}

    return {"read": read, "write": write, "create": create, "list": list_names}[
        tool.operation
    ]


def bind_system_tools(
    program: Program, *, folder: str | Path | None, allow_shell: bool = False
) -> dict[str, Callable[[ToolDecl, dict[str, Any]], Any]]:
    """Bind the program's system tools that the host has granted.

    A files tool is bound when a folder is granted. A shell tool is bound only
    when a folder is granted and shell is allowed. Anything not bound stays
    unreachable and fails the run before its first step.
    """

    system = [tool for tool in program.tools if system_kind(tool) is not None]
    for tool in system:
        problem = declaration_problem(program, tool)
        if problem is not None:
            raise SystemToolError(problem)
    if folder is None:
        if allow_shell:
            raise SystemToolError(
                "allowing shell also needs a granted folder to run in"
            )
        return {}
    root = Path(folder).resolve()
    if not root.is_dir():
        raise SystemToolError(
            f"granted folder {str(folder)!r} is not an existing folder"
        )
    bindings: dict[str, Callable[[ToolDecl, dict[str, Any]], Any]] = {}
    for tool in system:
        if system_kind(tool) == "files":
            bindings[tool.name] = _files(tool, root)
        elif allow_shell:
            bindings[tool.name] = _shell(tool, root)
    return bindings
