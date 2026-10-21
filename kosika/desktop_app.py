"""Kosika Studio as an app on the desktop.

`kosika desktop` puts a shortcut on the Desktop and in the Start menu. The
shortcut starts this module with no console window. It does what
`kosika start` does, opens Studio in a window of its own, and stops when
that window is closed, as any app does.

Nothing here grants anything. The app starts with no folder, exactly like
`kosika start`; folders are chosen inside Studio, on the Permissions page.

Because there is no console, what would have been printed goes to a log
file, and a failure to start is shown in a small message box.
"""

from __future__ import annotations

import json
import math
import os
import socket
import struct
import subprocess
import sys
import time
import traceback
from pathlib import Path
from urllib.parse import urlsplit

NAME = "Kosika Studio"
#: Sizes of the icon's pictures, in pixels.
ICON_SIZES = (16, 32, 48, 256)
#: The colours of the mark: the same ink and accent as Studio's page.
_INK = (0x1D, 0x1B, 0x18)
_ACCENT = (0xE8, 0x81, 0x3A)
_PAPER = (0xF6, 0xF4, 0xEF)


def home() -> Path:
    """Where the app keeps its icon, its log and its window's settings."""

    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "kosika"


# ------------------------------------------------------------------ the icon


def _cover(x: float, y: float, size: int) -> tuple[float, float, float]:
    """How much of one point is background, ring and dot, each 0 or 1."""

    half = size / 2
    # A square with rounded corners.
    radius = size * 0.22
    dx, dy = (
        max(abs(x - half) - (half - radius), 0),
        max(abs(y - half) - (half - radius), 0),
    )
    inside = 1.0 if math.hypot(dx, dy) <= radius else 0.0
    away = math.hypot(x - half, y - half)
    ring = 1.0 if size * 0.27 <= away <= size * 0.36 else 0.0
    dot = 1.0 if away <= size * 0.12 else 0.0
    return inside, ring * inside, dot * inside


def _picture(size: int) -> bytes:
    """One square picture of the mark as the bitmap an .ico file holds:
    a dark tile, an orange ring and a pale dot."""

    samples = 4 if size <= 64 else 2
    rows: list[bytes] = []
    for row in range(size - 1, -1, -1):  # bitmaps are stored bottom row first
        line = bytearray()
        for column in range(size):
            tile = ring = dot = 0.0
            for sy in range(samples):
                for sx in range(samples):
                    a, b, c = _cover(
                        column + (sx + 0.5) / samples, row + (sy + 0.5) / samples, size
                    )
                    tile, ring, dot = tile + a, ring + b, dot + c
            count = samples * samples
            tile, ring, dot = tile / count, ring / count, dot / count
            colour = [
                _INK[i] * (1 - ring - dot) + _ACCENT[i] * ring + _PAPER[i] * dot
                for i in range(3)
            ]
            line += bytes(
                (
                    int(round(colour[2])),
                    int(round(colour[1])),
                    int(round(colour[0])),
                    int(round(255 * tile)),
                )
            )
        rows.append(bytes(line))
    mask_row = bytes(((size + 31) // 32) * 4)
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    return header + b"".join(rows) + mask_row * size


def icon_bytes() -> bytes:
    """The whole .ico file: the mark at each size."""

    pictures = [_picture(size) for size in ICON_SIZES]
    offset = 6 + 16 * len(pictures)
    entries = b""
    for size, picture in zip(ICON_SIZES, pictures):
        entries += struct.pack(
            "<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(picture), offset
        )
        offset += len(picture)
    return struct.pack("<HHH", 0, 1, len(pictures)) + entries + b"".join(pictures)


def write_icon() -> Path:
    path = home() / "kosika.ico"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(icon_bytes())
    return path


# ------------------------------------------------------------------ the shortcuts


#: The second app: Kosi as a pill that stays on the screen.
PILL_NAME = "Kosi"
PILL_MODULE = "kosika.pill"
STUDIO_MODULE = "kosika.desktop_app"
#: The third app: MayaOS, the home the agents live in.
MAYAOS_NAME = "MayaOS"
MAYAOS_MODULE = "kosika.mayaos"
#: What each app's shortcut says about it.
WORDS = {
    STUDIO_MODULE: "Kosika Studio: build, run and watch AI agents",
    PILL_MODULE: "Kosi: your assistant, always on the screen",
    MAYAOS_MODULE: "MayaOS: your AI layer over the systems on this computer",
}


def shortcut_places(name: str = NAME) -> list[Path]:
    """Where the shortcut goes: the Desktop and the Start menu."""

    places: list[Path] = []
    profile = Path(os.environ.get("USERPROFILE") or Path.home())
    for desktop in (profile / "Desktop", profile / "OneDrive" / "Desktop"):
        if desktop.is_dir():
            places.append(desktop / f"{name}.lnk")
            break
    roaming = os.environ.get("APPDATA")
    if roaming:
        programs = Path(roaming) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        if programs.is_dir():
            places.append(programs / f"{name}.lnk")
    return places


def launcher() -> Path:
    """The Python that runs with no console window, beside the one in use."""

    quiet = Path(sys.executable).with_name("pythonw.exe")
    return quiet if quiet.is_file() else Path(sys.executable)


def code_folder() -> Path:
    """The folder Python must start in to find the kosika package."""

    return Path(__file__).resolve().parent.parent


_MAKE = (
    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:KOSIKA_LNK); "
    "$s.TargetPath = $env:KOSIKA_TARGET; "
    "$s.Arguments = $env:KOSIKA_ARGS; "
    "$s.WorkingDirectory = $env:KOSIKA_DIR; "
    "$s.IconLocation = $env:KOSIKA_ICON; "
    "$s.Description = $env:KOSIKA_WORDS; "
    "$s.Save()"
)


def shortcut_arguments(agents: Path | None, module: str = STUDIO_MODULE) -> str:
    """What the shortcut hands to Python. The folder is quoted, and a folder
    whose name holds a quotation mark is refused rather than guessed at."""

    arguments = f"-m {module}"
    if agents is not None:
        text = str(agents.resolve())
        if '"' in text:
            raise ValueError("The agents folder has a quotation mark in its name")
        arguments += f' "{text}"'
    return arguments


def make_shortcuts(
    agents: Path | None, name: str = NAME, module: str = STUDIO_MODULE
) -> list[Path]:
    """Put the shortcut on the Desktop and in the Start menu. Windows only."""

    if sys.platform != "win32":
        raise OSError("Desktop shortcuts are made on Windows only, for now")
    icon = write_icon()
    made: list[Path] = []
    for place in shortcut_places(name):
        # Everything is handed over in the environment, never pasted into
        # the command, so no folder name can be read as PowerShell.
        environment = {
            **os.environ,
            "KOSIKA_LNK": str(place),
            "KOSIKA_TARGET": str(launcher()),
            "KOSIKA_ARGS": shortcut_arguments(agents, module),
            "KOSIKA_WORDS": WORDS.get(module, WORDS[STUDIO_MODULE]),
            "KOSIKA_DIR": str(code_folder()),
            "KOSIKA_ICON": str(icon),
        }
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _MAKE],
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if done.returncode != 0 or not place.is_file():
            raise OSError(
                f"Could not make {place}: {(done.stderr or done.stdout).strip()[:300]}"
            )
        made.append(place)
    if not made:
        raise OSError("Neither a Desktop nor a Start menu folder was found")
    return made


def remove_shortcuts(name: str = NAME) -> list[Path]:
    gone: list[Path] = []
    for place in shortcut_places(name):
        if place.is_file():
            place.unlink()
            gone.append(place)
    return gone


# ------------------------------------------------------------------ the apps find each other


def announce(name: str, url: str, folder: Path | None = None) -> None:
    """Write where a running app can be reached, for Kosika's other apps to
    read. The note holds the app's key, so it stays in this person's own
    app folder."""

    folder = (folder or home()) / "running"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.json").write_text(
        json.dumps({"url": url, "pid": os.getpid(), "at": time.time()}), encoding="utf-8"
    )


def reachable(name: str, folder: Path | None = None) -> str:
    """The address of a running app, or "" when it is not running."""

    try:
        note = json.loads(((folder or home()) / "running" / f"{name}.json").read_text(encoding="utf-8"))
        url = str(note["url"])
        port = urlsplit(url).port
    except (OSError, ValueError, KeyError, TypeError):
        return ""
    if not port:
        return ""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return url
    except OSError:
        return ""


# ------------------------------------------------------------------ starting


def _tell(message: str) -> None:
    """Say something to a person who has no console to read."""

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, NAME, 0x10)
            return
        except Exception:  # noqa: BLE001 - the log still has it
            pass


def main(argv: list[str] | None = None) -> int:
    """Start Studio as an app: no console, its own window, and an end when
    the window is closed."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    log_path = home() / "studio.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", buffering=1) as log:
        # With no console, Python has nowhere to print. Everything goes to
        # the log, which is started afresh each time.
        sys.stdout = sys.stderr = log
        try:
            from . import cli

            sys.argv = ["kosika", "start", *arguments[:1], "--until-closed"]
            code = cli.main()
            if code != 0:
                _tell(
                    "Kosika Studio could not start.\n\n"
                    + log_path.read_text(encoding="utf-8")[-600:]
                    + f"\n\nThe whole message is in {log_path}"
                )
            return code
        except SystemExit as stopped:
            return int(stopped.code or 0) if isinstance(stopped.code, int) else 1
        except Exception:  # noqa: BLE001 - a person must be told, whatever it was
            traceback.print_exc(file=log)
            _tell(f"Kosika Studio stopped unexpectedly. The reason is in {log_path}")
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
