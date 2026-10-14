"""Asking an outside command-line agent, without letting it touch anything.

Some people have other AI agents installed that run from the command line
and are far more able than a small local model. Kosika can ask one for
help and stay in charge of what happens:

1. The folder is copied. The outside agent is started in the copy, allowed
   to edit files there and nothing more, with one request.
2. When it stops, the copy is compared with the folder. What it changed or
   added comes back as a list of files with their whole new text.
3. Nothing in the real folder has changed. Whoever asked decides what to do
   with the list, and writing any of it is a separate step that needs a
   person's approval like every other change.

Two things an owner must know, and so two limits. Whatever the outside
agent reads is sent to the company that runs it, so it is started only in
a folder inside the one root the owner named (`within`). And it is another
program with its own mind, so it is never started in the real folder.

Which agents can be asked, and how each is started with one request and no
window, is in `COMMANDS`. Only an agent already installed is ever started;
nothing is downloaded.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence

#: How each known agent is started in a folder with one request, allowed to
#: edit files there and not to run programs. The request is the last item.
COMMANDS: Mapping[str, Callable[[Path, str], list[str]]] = {
    "devin": lambda folder, request: [
        "devin",
        "-p",
        "--permission-mode",
        "accept-edits",
        "--respect-workspace-trust",
        "false",
        "--",
        request,
    ],
    "omp": lambda folder, request: [
        "omp",
        "-p",
        "--approval-mode",
        "write",
        "--no-session",
        "--max-time",
        "8m",
        "--cwd",
        str(folder),
        request,
    ],
}
#: Folders never copied: other tools' own stores, not the person's writing.
SKIPPED = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "dist",
        "build",
        "target",
        "_trash",
        ".cache",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)
MAX_COPIED_FILES = 3000
MAX_COPIED_BYTES = 40 * 1024 * 1024
MAX_FILE_BYTES = 1024 * 1024
MAX_CHANGED_FILES = 40
TIMEOUT_SECONDS = 600
#: Said to every outside agent after the request itself.
RULES = (
    "\n\nWork only by editing files in this folder. Do not run programs, "
    "tests or installers. Change only what the task needs, and keep "
    "everything else in each file as it is."
)


def settings_path() -> Path:
    """Where the owner's choice is kept: which outside agents may be asked,
    and the one root they may be shown."""

    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "kosika" / "outside.json"


def load_settings(path: Path | None = None) -> tuple[tuple[str, ...], Path | None]:
    """The agents the owner allowed and the root they may be shown. With no
    choice made, or one that cannot be read, nobody is allowed."""

    try:
        kept = json.loads((path or settings_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return (), None
    if not isinstance(kept, dict):
        return (), None
    names = kept.get("agents")
    within = kept.get("within")
    agents = (
        tuple(name for name in names if isinstance(name, str) and name in COMMANDS)
        if isinstance(names, list)
        else ()
    )
    root = Path(within) if isinstance(within, str) and within.strip() else None
    if not agents or root is None or not root.is_dir():
        return (), None
    return agents, root


def save_settings(
    agents: Sequence[str], within: Path | None, path: Path | None = None
) -> Path:
    """Keep the owner's choice. An empty list, or no root, allows nobody."""

    unknown = [name for name in agents if name not in COMMANDS]
    if unknown:
        raise OutsideError(
            f"{unknown[0]!r} is not an outside agent Kosika knows how to ask. "
            f"It knows: {', '.join(COMMANDS)}."
        )
    if agents and (within is None or not within.is_dir()):
        raise OutsideError(
            "Name the one folder outside agents may be shown, with --within FOLDER."
        )
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "agents": list(agents),
                "within": str(within.resolve()) if within and agents else "",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return target


def kosika_notes() -> str:
    """The short guide to writing Kosika that ships with it, or "" if the
    file has gone missing."""

    try:
        return (Path(__file__).parent / "app_assets" / "writing_kosika.md").read_text(
            encoding="utf-8"
        )
    except OSError:
        return ""


def with_notes(request: str, names: Sequence[str]) -> str:
    """A request, followed by the guide to writing Kosika when the work
    is in Kosika: the folder has `.kos` files, or the request speaks of
    one. An outside agent has not been taught the language; it writes it
    from habit unless it is shown how."""

    lower = request.lower()
    if not (
        any(name.lower().endswith(".kos") for name in names)
        or ".kos" in lower
        or "kosika" in lower
    ):
        return request
    notes = kosika_notes()
    if not notes:
        return request
    return (
        request + "\n\nSome of this is written in Kosika, a language you have not "
        "been taught. This is how it is written:\n\n" + notes
    )


class OutsideError(ValueError):
    """An outside agent could not be asked, in words for a person."""


@dataclass(frozen=True)
class Change:
    path: str
    text: str
    before: str
    existed: bool


@dataclass(frozen=True)
class Proposal:
    agent: str
    said: str
    seconds: float
    changes: tuple[Change, ...] = ()
    #: Things it did that are not offered: files it deleted, files that are
    #: not text, files too large. Told, never applied.
    left_out: tuple[str, ...] = field(default=())


def installed(names: Sequence[str] = tuple(COMMANDS)) -> list[str]:
    """The known agents that are installed on this computer."""

    return [name for name in names if name in COMMANDS and shutil.which(name)]


def inside(folder: Path, within: Path) -> bool:
    """Whether a folder is `within` or somewhere under it, links resolved."""

    try:
        folder.resolve().relative_to(within.resolve())
    except (OSError, ValueError):
        return False
    return True


def _secret(name: str) -> bool:
    """Whether a file's name says it holds a secret. Such a file is never
    shown to an outside agent, whatever else is."""

    last = name.rsplit("/", 1)[-1].lower()
    return (
        last.startswith(".env")
        or last.endswith((".pem", ".key", ".pfx", ".p12", ".keystore"))
        or last.startswith(("id_rsa", "id_ed25519", "id_ecdsa"))
        or last in ("credentials.json", "secrets.json", ".npmrc", ".pypirc", ".netrc")
    )


def _git_names(folder: Path) -> list[str] | None:
    """In a git project, the files git knows of or would add: what the
    project's own ignore rules keep out stays out. None when the folder is
    not the top of a git project or git cannot be asked."""

    if not (folder / ".git").exists() or shutil.which("git") is None:
        return None
    try:
        done = subprocess.run(  # noqa: S603, S607 - git, asked only to list
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=folder,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    return [
        name for name in done.stdout.decode("utf-8", "replace").split(chr(0)) if name
    ]


def _git_ignored(folder: Path, names: Sequence[str]) -> set[str]:
    """Which of some paths a git project would ignore. They need not
    exist. Empty when the folder is not a git project."""

    if not names or not (folder / ".git").exists() or shutil.which("git") is None:
        return set()
    try:
        done = subprocess.run(  # noqa: S603, S607 - git, asked only what it ignores
            ["git", "check-ignore", "-z", "--stdin"],
            cwd=folder,
            input=chr(0).join(names).encode("utf-8"),
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {
        name for name in done.stdout.decode("utf-8", "replace").split(chr(0)) if name
    }


def _files(folder: Path) -> dict[str, Path]:
    """Every file under a folder that is the person's own, by its path.

    In a git project that is what git tracks or would add, so caches,
    builds and anything the project ignores are left out. Elsewhere it is
    everything outside the folders in SKIPPED. Files whose names say they
    hold secrets are always left out.
    """

    found: dict[str, Path] = {}
    total = 0
    listed = _git_names(folder)
    if listed is not None:
        for name in sorted(listed):
            path = folder / name
            if _secret(name) or path.is_symlink() or not path.is_file():
                continue
            total += path.stat().st_size
            found[name] = path
            if len(found) > MAX_COPIED_FILES or total > MAX_COPIED_BYTES:
                raise OutsideError(
                    "The folder is too large to hand to an outside agent: more "
                    f"than {MAX_COPIED_FILES} files or {MAX_COPIED_BYTES // (1024 * 1024)} "
                    "MB. Grant a smaller folder."
                )
        return found
    stack = [folder]
    while stack:
        here = stack.pop()
        for entry in sorted(here.iterdir()):
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name not in SKIPPED:
                    stack.append(entry)
                continue
            if not entry.is_file() or _secret(entry.name):
                continue
            size = entry.stat().st_size
            total += size
            found[entry.relative_to(folder).as_posix()] = entry
            if len(found) > MAX_COPIED_FILES or total > MAX_COPIED_BYTES:
                raise OutsideError(
                    "The folder is too large to hand to an outside agent: more "
                    f"than {MAX_COPIED_FILES} files or {MAX_COPIED_BYTES // (1024 * 1024)} "
                    "MB. Grant a smaller folder."
                )
    return found


def _text(path: Path) -> str | None:
    """A file's text, or None when it is too large or is not text."""

    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def compare(folder: Path, copy: Path) -> tuple[list[Change], list[str]]:
    """What a copy holds that its folder does not: the files changed or
    added, and a sentence for each thing that is not offered."""

    before, after = _files(folder), _files(copy)
    changes: list[Change] = []
    left_out: list[str] = []
    ignored = _git_ignored(folder, [name for name in after if name not in before])
    for name, path in sorted(after.items()):
        old = before.get(name)
        if old is not None and old.read_bytes() == path.read_bytes():
            continue
        if name in ignored:
            # A cache or a build it left behind, which the project itself
            # keeps out. It is not part of the work.
            continue
        new_text = _text(path)
        old_text = _text(old) if old is not None else ""
        if new_text is None or old_text is None:
            left_out.append(f"{name} is not text or is over 1 MB, so it is not offered")
            continue
        changes.append(Change(name, new_text, old_text, old is not None))
    left_out.extend(
        f"it deleted {name}, which is not done for it"
        for name in sorted(before)
        if name not in after
    )
    if len(changes) > MAX_CHANGED_FILES:
        raise OutsideError(
            f"It changed {len(changes)} files, more than the {MAX_CHANGED_FILES} "
            "that can be looked at in one go. Ask for something smaller."
        )
    return changes, left_out


def propose(
    agent: str,
    request: str,
    folder: Path,
    *,
    within: Path | None,
    allowed: Sequence[str],
    runner: Callable[[list[str], Path], tuple[int, str]] | None = None,
) -> Proposal:
    """Ask an outside agent to do something in a copy of a folder, and
    give back what it changed there. The folder itself is not touched.

    `allowed` are the agents the owner lets be asked and `within` the one
    root they may be shown; with either missing, nobody is asked. `runner`
    starts the command in the copy and gives its exit code and what it
    printed; a test hands in its own.
    """

    if agent not in COMMANDS:
        raise OutsideError(
            f"{agent!r} is not an outside agent Kosika knows how to ask. "
            f"It knows: {', '.join(COMMANDS)}."
        )
    if agent not in allowed:
        raise OutsideError(f"The outside agent {agent} has not been allowed here.")
    if within is None or not inside(folder, within):
        where = f" inside {within}" if within is not None else ""
        raise OutsideError(
            f"An outside agent is shown only folders{where} that the owner "
            f"named for it, and {folder} is not one."
        )
    if runner is None and shutil.which(agent) is None:
        raise OutsideError(f"{agent} is not installed on this computer.")
    if not request.strip():
        raise OutsideError("There was nothing to ask.")
    files = _files(folder)
    with tempfile.TemporaryDirectory(
        prefix=f"kosika-outside-{agent}-", ignore_cleanup_errors=True
    ) as scratch:
        copy = Path(scratch) / "work"
        copy.mkdir()
        for name, path in files.items():
            target = copy / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        started = time.monotonic()
        code, said = (runner or _run)(
            COMMANDS[agent](copy, with_notes(request.strip() + RULES, list(files))),
            copy,
        )
        seconds = round(time.monotonic() - started, 1)
        _stopped(agent, code, said)
        changes, left_out = compare(folder, copy)
    return Proposal(
        agent, said.strip()[-2000:], seconds, tuple(changes), tuple(left_out)
    )


#: Said before a question that is only to be answered.
ANSWER_RULES = (
    "Answer the question at the end in plain sentences, as if speaking "
    "aloud to one person: no lists, no headings, no code, no symbols, and "
    "at most six sentences. Give real substance: name the things and say "
    "why. Do not create, change or read any file, and do not run anything. "
    "Give only the answer itself.\n\n"
)
#: The most of an outside agent's answer that is passed on.
MAX_ANSWER_CHARS = 1500


def answer(
    agent: str,
    question: str,
    *,
    allowed: Sequence[str],
    runner: Callable[[list[str], Path], tuple[int, str]] | None = None,
) -> tuple[str, str]:
    """Ask an outside agent a question and give back who answered and
    what it said. It is started in an empty folder made for the purpose,
    so it is shown no file at all: the question is everything it gets.
    The question itself still goes to the company that runs the agent,
    which is why only agents the owner `allowed` are asked.
    """

    if agent not in COMMANDS:
        raise OutsideError(
            f"{agent!r} is not an outside agent Kosika knows how to ask. "
            f"It knows: {', '.join(COMMANDS)}."
        )
    if agent not in allowed:
        raise OutsideError(f"The outside agent {agent} has not been allowed here.")
    if runner is None and shutil.which(agent) is None:
        raise OutsideError(f"{agent} is not installed on this computer.")
    if not question.strip():
        raise OutsideError("There was nothing to ask.")
    with tempfile.TemporaryDirectory(
        prefix=f"kosika-outside-{agent}-", ignore_cleanup_errors=True
    ) as scratch:
        empty = Path(scratch) / "empty"
        empty.mkdir()
        code, said = (runner or _run)(
            COMMANDS[agent](empty, ANSWER_RULES + question.strip()), empty
        )
        _stopped(agent, code, said)
    lines = [
        line.strip()
        for line in said.strip().splitlines()
        if line.strip() and not line.strip().lower().startswith("working")
    ]
    if not lines:
        raise OutsideError(f"{agent} said nothing.")
    return agent, " ".join(lines)[:MAX_ANSWER_CHARS]


def _stopped(agent: str, code: int, said: str) -> None:
    """Refuse an answer from an agent that ended with an error: it has run
    out of its daily allowance, is not signed in, or broke. What it printed
    then is not work, and whoever asked should try another."""

    if code == 0:
        return
    lines = [line.strip() for line in said.strip().splitlines() if line.strip()]
    why = next(
        (line for line in lines if "error" in line.lower()), lines[0] if lines else ""
    )
    if "quota" in said.lower() or "resource_exhausted" in said.lower():
        raise OutsideError(f"{agent} has used up its allowance for now.")
    raise OutsideError(
        f"{agent} stopped with an error"
        + (f": {why[:240]}" if why else " and said nothing.")
    )


#: The folder, inside a reviewer's copy, that holds changed files as they
#: were before the change.
BEFORE = "_before"


def review_request(task: str, paths: Sequence[str]) -> str:
    """What a reviewing agent is asked."""

    return (
        "Review a change another programmer has just made in this folder. "
        "Do not edit any file and do not run anything.\n"
        f"The task they were given: {task.strip()}\n"
        f"The files they changed: {', '.join(paths)}. Each is in this folder as "
        f"it is AFTER the change. The folder `{BEFORE}` holds the same files as "
        "they were BEFORE the change; a file not there is new.\n"
        "Read both versions of each file. Check: does the change do the task "
        "correctly, including edge cases; do the tests really test it; is "
        "anything changed or added that the task did not ask for; is anything "
        "that was there before lost.\n"
        "Answer in a few short sentences: either that you found no problem, or "
        "one sentence for each problem, most serious first."
    )


def review(
    agent: str,
    task: str,
    changes: Sequence[Change],
    folder: Path,
    *,
    within: Path | None,
    allowed: Sequence[str],
    runner: Callable[[list[str], Path], tuple[int, str]] | None = None,
) -> Proposal:
    """Ask an outside agent to read a proposed change and say what is wrong
    with it. The change is applied in a copy, with the old files beside it,
    so the reviewer reads real files. Nothing it edits is kept, and the
    folder itself is not touched.
    """

    if agent not in COMMANDS:
        raise OutsideError(
            f"{agent!r} is not an outside agent Kosika knows how to ask. "
            f"It knows: {', '.join(COMMANDS)}."
        )
    if agent not in allowed:
        raise OutsideError(f"The outside agent {agent} has not been allowed here.")
    if within is None or not inside(folder, within):
        raise OutsideError(
            "An outside agent is shown only folders the owner named for it, "
            f"and {folder} is not one."
        )
    if runner is None and shutil.which(agent) is None:
        raise OutsideError(f"{agent} is not installed on this computer.")
    if not changes:
        raise OutsideError("There is no change to review.")
    files = _files(folder)
    with tempfile.TemporaryDirectory(
        prefix=f"kosika-review-{agent}-", ignore_cleanup_errors=True
    ) as scratch:
        copy = Path(scratch) / "work"
        copy.mkdir()
        for name, path in files.items():
            target = copy / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        for change in changes:
            target = (copy / change.path).resolve()
            if not inside(target, copy):
                raise OutsideError(f"{change.path!r} would leave the folder.")
            if change.existed:
                kept = copy / BEFORE / change.path
                kept.parent.mkdir(parents=True, exist_ok=True)
                kept.write_text(change.before, encoding="utf-8", newline="")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(change.text, encoding="utf-8", newline="")
        started = time.monotonic()
        code, said = (runner or _run)(
            COMMANDS[agent](copy, review_request(task, [c.path for c in changes])), copy
        )
        seconds = round(time.monotonic() - started, 1)
        _stopped(agent, code, said)
    return Proposal(agent, said.strip()[-2000:], seconds)


def _program(name: str) -> str:
    """The program file an agent's name stands for.

    It is started directly, never through a shell. A shell on Windows cuts
    an argument at its first line break and reads `&`, `|` and `"` as its
    own, so a request of several lines would arrive as its first line
    alone, without the rules that follow it. A name that leads to a script
    the shell would have to run (.cmd, .bat) is refused for that reason.
    """

    found = shutil.which(name)
    if found is None:
        raise OutsideError(f"{name} is not installed on this computer.")
    if sys.platform == "win32" and Path(found).suffix.lower() in (
        ".cmd",
        ".bat",
        ".ps1",
    ):
        raise OutsideError(
            f"{name} is installed as a script ({Path(found).name}), which cannot "
            "be handed a request of several lines safely."
        )
    return found


def _run(command: list[str], folder: Path) -> tuple[int, str]:
    try:
        done = subprocess.run(  # noqa: S603 - a known agent, in a scratch copy
            [_program(command[0]), *command[1:]],
            cwd=folder,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OutsideError(
            f"{command[0]} did not finish in {TIMEOUT_SECONDS // 60} minutes."
        ) from exc
    except OSError as exc:
        raise OutsideError(f"{command[0]} could not be started: {exc}") from exc
    return done.returncode, (done.stdout or "") + "\n" + (done.stderr or "")
