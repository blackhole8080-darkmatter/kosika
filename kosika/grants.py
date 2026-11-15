"""Choosing, in Studio, what agents may touch.

A folder is granted by a person picking it in the computer's own folder
window, which Kosika opens. The page cannot name a folder itself: it can
only ask for the window, and then confirm what was picked there. So a web
page, a script or an agent cannot hand itself a folder; someone has to be
sitting at this computer.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

#: How long the folder window may stay open before the question is dropped.
PICK_SECONDS = 300

_PICKER = """
import sys, tkinter, tkinter.filedialog
root = tkinter.Tk()
root.withdraw()
root.attributes("-topmost", True)
picked = tkinter.filedialog.askdirectory(
    title="Choose the folder Kosika agents may work in", mustexist=True
)
sys.stdout.buffer.write(picked.encode("utf-8"))
"""


def native_folder_window() -> str:
    """Open the computer's folder window and give back what was picked, or
    "" when it was closed. Run apart from the server so that a stuck window
    cannot stop Studio."""

    try:
        done = subprocess.run(
            [sys.executable, "-c", _PICKER],
            capture_output=True,
            timeout=PICK_SECONDS,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.decode("utf-8", errors="replace").strip()


def too_wide(folder: Path, home: Path | None = None) -> str | None:
    """Why a folder is too much to hand to agents, or None if it is fine.

    A whole drive, a whole user folder, or anything that holds the user
    folder is refused: an agent's reach should be one piece of work.
    """

    resolved = folder.resolve()
    home = (home or Path.home()).resolve()
    if resolved == Path(resolved.anchor):
        return f"{resolved} is a whole drive. Choose one folder inside it."
    if resolved == home or resolved in home.parents:
        return (
            f"{resolved} holds everything of yours on this computer. "
            "Choose one folder inside it, such as a project or a notes folder."
        )
    for name in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        system = os.environ.get(name)
        if system:
            place = Path(system).resolve()
            if resolved == place or place in resolved.parents:
                return f"{resolved} belongs to the system. Choose a folder of your own."
    return None
