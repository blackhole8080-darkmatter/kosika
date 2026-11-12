"""A Kosika agent's own pointer on the screen, beside the person's mouse.

    python pointer.py --name coder-1        # hands.py starts one for each agent

It is an arrow with the agent's name on a tag, in a colour of its own, drawn
over everything and never taking a click. It does not move the mouse: it
glides to where the agent is about to act, which the agent writes to
~/.kosika/pointers/NAME.json ({"x": .., "y": ..}), and the agent then acts
there through Windows' accessibility (eyes.ps1 -Press), so the person's own
mouse stays where they left it and several agents can be seen at once.
It hides after a few quiet seconds and ends after a quiet minute.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import time
import tkinter
from pathlib import Path

POINTERS = Path.home() / ".kosika" / "pointers"
KEY = "#010203"
SHOW_FOR = 6.0
QUIT_AFTER = 90.0
COLOURS = ["#ff7a00", "#2f80ed", "#27ae60", "#d63384", "#8e44ad", "#16a085", "#c0392b", "#f1c40f"]

user32 = ctypes.WinDLL("user32", use_last_error=True)
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except (AttributeError, OSError):
    pass
GWL_EXSTYLE, WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = -20, 0x80000, 0x20, 0x80


def colour_of(name: str) -> str:
    return COLOURS[int(hashlib.sha256(name.encode()).hexdigest(), 16) % len(COLOURS)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="Kosika")
    args = parser.parse_args()
    target = POINTERS / f"{args.name}.json"
    colour = colour_of(args.name)
    root = tkinter.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg=KEY)
    root.attributes("-transparentcolor", KEY)
    width = 34 + 8 * len(args.name)
    canvas = tkinter.Canvas(root, width=width, height=56, bg=KEY, highlightthickness=0)
    canvas.pack()
    # An arrow pointer, its tip at (3, 3), outlined in white so it shows on any background.
    arrow = [3, 3, 3, 33, 11, 25, 17, 39, 23, 36, 17, 23, 28, 23]
    canvas.create_polygon(arrow, fill=colour, outline="white", width=2)
    canvas.create_rectangle(20, 34, 26 + 7 * len(args.name) + 10, 52, fill=colour, outline="white")
    canvas.create_text(28, 43, text=args.name, anchor="w", fill="white", font=("Segoe UI", 9, "bold"))
    root.update_idletasks()
    handle = user32.GetParent(root.winfo_id()) or root.winfo_id()
    style = user32.GetWindowLongW(handle, GWL_EXSTYLE)
    user32.SetWindowLongW(handle, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW)
    here = [root.winfo_screenwidth() / 2, root.winfo_screenheight() / 2]
    shown = True

    def step() -> None:
        nonlocal shown
        try:
            told = json.loads(target.read_text(encoding="utf-8"))
            quiet = time.time() - target.stat().st_mtime
        except (OSError, ValueError):
            told, quiet = None, 1e9
        if quiet > QUIT_AFTER:
            root.destroy()
            return
        if told and quiet < SHOW_FOR:
            # Glide a fifth of the way each frame: quick, but the eye can follow it.
            here[0] += (told["x"] - here[0]) * 0.2
            here[1] += (told["y"] - here[1]) * 0.2
            root.geometry(f"+{int(here[0]) - 3}+{int(here[1]) - 3}")
            if not shown:
                root.deiconify()
                root.attributes("-topmost", True)
                shown = True
        elif shown:
            root.withdraw()
            shown = False
        root.after(16, step)

    step()
    root.mainloop()


if __name__ == "__main__":
    main()
