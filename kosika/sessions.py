"""A session: a walled-off place where an agent may work freely.

Without a session, a program an agent starts runs on this computer with the
rights of whoever runs Kosika, which is why every start waits for a yes.
With one, the agent gets a *copy* of the granted folder and its programs
run behind a wall:

* they see that copy and nothing else of this computer's files;
* the system they run on is read-only;
* they have no network;
* they are stopped after a time limit.

So inside a session the agent's steps need no yes one by one. Nothing it
does reaches the real folder. When it is done, `changes()` lists what is
different in the copy, and only `apply()`, called on a person's yes, puts
those files into the real folder.

The wall is bubblewrap, on Linux directly and on Windows inside WSL. It is a
strong wall, not a perfect one: it relies on the Linux kernel, the program
still uses this computer's processor and memory, and anything written into
the copy is brought back if the person says yes without reading it.
"""

from __future__ import annotations

import filecmp
import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

#: A folder larger than this is not copied into a session.
MAX_COPY_BYTES = 200 * 1024 * 1024
MAX_COPY_FILES = 20_000
#: How many changed files are listed; the rest are counted.
MAX_LISTED_CHANGES = 500
RUN_TIMEOUT_SECONDS = 60
#: Never copied in or brought back: other tools' own stores.
_SKIPPED = {"node_modules", "__pycache__", ".venv", "venv"}

Launcher = Callable[[Sequence[str], float], "subprocess.CompletedProcess[str]"]


class SessionError(RuntimeError):
    """A session could not be made, used, or brought back."""


@dataclass(frozen=True)
class Change:
    path: str
    kind: str  # "new", "changed" or "removed"
    bytes: int


def linux_path(path: Path) -> str:
    """How a path on this computer is named inside WSL."""

    if sys.platform != "win32":
        return path.as_posix()
    pure = PureWindowsPath(path)
    drive = pure.drive.rstrip(":").lower()
    if len(drive) != 1 or not drive.isalpha():
        raise SessionError(f"{path} is not on a drive WSL can reach")
    return "/mnt/" + drive + "/" + "/".join(pure.parts[1:])


def wall(work: str, program: str, args: Sequence[str]) -> list[str]:
    """The bubblewrap command that runs one program behind the wall."""

    return [
        "bwrap",
        # No network, no view of other processes, nothing shared.
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        "--setenv", "PATH", "/usr/bin:/bin",
        "--setenv", "HOME", "/work",
        "--setenv", "LANG", "C.UTF-8",
        # The system, read-only. The person's own files are not in it.
        "--ro-bind", "/usr", "/usr",
        "--symlink", "usr/bin", "/bin",
        "--symlink", "usr/lib", "/lib",
        "--symlink", "usr/lib64", "/lib64",
        "--symlink", "usr/sbin", "/sbin",
        "--ro-bind-try", "/etc/alternatives", "/etc/alternatives",
        "--ro-bind-try", "/etc/ssl", "/etc/ssl",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        # The one place it may write: the copy.
        "--bind", work, "/work",
        "--chdir", "/work",
        "--",
        program,
        *args,
    ]  # fmt: skip


def _launch(command: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def _prefix() -> list[str]:
    """What goes before a Linux command so that it runs in Linux."""

    return ["wsl.exe", "--"] if sys.platform == "win32" else []


def unavailable(launcher: Launcher = _launch) -> str | None:
    """Why sessions cannot be used on this computer, or None if they can."""

    if sys.platform == "win32" and shutil.which("wsl.exe") is None:
        return "Sessions need WSL, which is not installed on this computer."
    try:
        probe = launcher([*_prefix(), *wall("/tmp", "true", [])], 30.0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"The session wall could not be started: {exc}"
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout or "").strip().splitlines()
        return "The session wall (bubblewrap) is not working: " + (
            detail[-1] if detail else f"exit code {probe.returncode}"
        )
    return None


def _files(root: Path) -> dict[str, Path]:
    """Every file under a folder, by its path from that folder."""

    found: dict[str, Path] = {}
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_symlink() or entry.name in _SKIPPED:
                continue
            if entry.is_dir():
                pending.append(entry)
            elif entry.is_file():
                found[entry.relative_to(root).as_posix()] = entry
    return found


class Session:
    """One agent's copy of a folder, and the wall its programs run behind."""

    def __init__(
        self,
        folder: Path,
        home: Path,
        *,
        launcher: Launcher = _launch,
        name: str | None = None,
    ) -> None:
        self.folder = folder.resolve()
        if not self.folder.is_dir():
            raise SessionError(f"{folder} is not a folder")
        self.id = name or uuid.uuid4().hex[:12]
        self.root = home / self.id
        self.work = self.root / "work"
        self.launcher = launcher
        if not self.work.is_dir():
            self._copy_in()

    def _copy_in(self) -> None:
        files = _files(self.folder)
        total = 0
        for path in files.values():
            try:
                total += path.stat().st_size
            except OSError:
                continue
        if len(files) > MAX_COPY_FILES or total > MAX_COPY_BYTES:
            raise SessionError(
                f"{self.folder} is too large for a session "
                f"({len(files)} files, {total // (1024 * 1024)} MB); "
                f"the limit is {MAX_COPY_FILES} files and "
                f"{MAX_COPY_BYTES // (1024 * 1024)} MB"
            )
        self.work.mkdir(parents=True)
        for relative, path in files.items():
            target = self.work / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)

    def run(
        self, program: str, args: Sequence[str], timeout: float = RUN_TIMEOUT_SECONDS
    ) -> dict[str, object]:
        """Run one program behind the wall, in the copy."""

        if not program or any(mark in program for mark in " /\\"):
            raise SessionError(f"{program!r} is not the name of one program")
        command = [*_prefix(), *wall(linux_path(self.work), program, list(args))]
        try:
            done = self.launcher(command, timeout)
        except subprocess.TimeoutExpired as exc:
            raise SessionError(
                f"program {program!r} ran longer than {int(timeout)} seconds "
                "and was stopped"
            ) from exc
        except OSError as exc:
            raise SessionError(
                f"the session could not start {program!r}: {exc}"
            ) from exc
        if done.returncode != 0 and "bwrap: execvp" in (done.stderr or ""):
            # The wall started but the program is not on its Linux side.
            raise SessionError(
                f"program {program!r} is not installed where sessions run "
                "(the Linux side of this computer)"
            )
        return {
            "exit_code": done.returncode,
            "stdout": done.stdout,
            "stderr": done.stderr,
        }

    def changes(self) -> list[Change]:
        """What is different in the copy from the real folder, by name."""

        real, copy = _files(self.folder), _files(self.work)
        found: list[Change] = []
        for relative, path in copy.items():
            if relative not in real:
                found.append(Change(relative, "new", path.stat().st_size))
            elif not filecmp.cmp(path, real[relative], shallow=False):
                found.append(Change(relative, "changed", path.stat().st_size))
        found.extend(
            Change(relative, "removed", 0) for relative in real if relative not in copy
        )
        return sorted(found, key=lambda change: change.path)

    def apply(self, paths: Sequence[str] | None = None) -> list[Change]:
        """Put the copy's changes into the real folder. Only ever called on
        a person's yes. With `paths`, only those of the changes."""

        wanted = None if paths is None else set(paths)
        done: list[Change] = []
        for change in self.changes():
            if wanted is not None and change.path not in wanted:
                continue
            target = self.folder / change.path
            # The copy cannot name a place outside the real folder, but a
            # folder in the real one may have become a link since.
            resolved = target.parent.resolve()
            if resolved != self.folder and self.folder not in resolved.parents:
                raise SessionError(f"{change.path} would land outside the folder")
            if change.kind == "removed":
                if target.is_file():
                    target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.work / change.path, target)
            done.append(change)
        return done

    def discard(self) -> None:
        """Throw the copy away. The real folder is untouched."""

        shutil.rmtree(self.root, ignore_errors=True)


def default_home() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "kosika" / "sessions"
