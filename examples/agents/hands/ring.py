"""Kosika's own cursor on the desktop: an orange ring around the pointer
while Kosika's hands are moving it, so the person sees at once whose pointer
it is. It never takes a click: clicks go through it to what is underneath.

    python ring.py        # hands.py starts it; it ends itself after a quiet minute

hands.py touches ~/.kosika/hands-active before each action. The ring shows
while that is less than ACTIVE seconds old and hides otherwise.
"""

from __future__ import annotations

import ctypes
import time
import tkinter
from ctypes import wintypes
from pathlib import Path

ACTIVE = Path.home() / ".kosika" / "hands-active"
SHOW_FOR = 3.0
QUIT_AFTER = 60.0
SIZE = 46
KEY = "#010203"  # drawn nowhere else, so it is see-through

user32 = ctypes.WinDLL("user32", use_last_error=True)
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except (AttributeError, OSError):
    pass
GWL_EXSTYLE, WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = -20, 0x80000, 0x20, 0x80


def since_active() -> float:
    try:
        return time.time() - ACTIVE.stat().st_mtime
    except OSError:
        return 1e9


def main() -> None:
    root = tkinter.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg=KEY)
    root.attributes("-transparentcolor", KEY)
    canvas = tkinter.Canvas(root, width=SIZE, height=SIZE, bg=KEY, highlightthickness=0)
    canvas.pack()
    canvas.create_oval(4, 4, SIZE - 4, SIZE - 4, outline="#ff7a00", width=5)
    canvas.create_oval(SIZE / 2 - 3, SIZE / 2 - 3, SIZE / 2 + 3, SIZE / 2 + 3, fill="#ff7a00", outline="")
    root.update_idletasks()
    handle = user32.GetParent(root.winfo_id()) or root.winfo_id()
    style = user32.GetWindowLongW(handle, GWL_EXSTYLE)
    user32.SetWindowLongW(handle, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW)
    shown = True

    def follow() -> None:
        nonlocal shown
        quiet = since_active()
        if quiet > QUIT_AFTER:
            root.destroy()
            return
        if quiet < SHOW_FOR:
            point = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            root.geometry(f"{SIZE}x{SIZE}+{point.x - SIZE // 2}+{point.y - SIZE // 2}")
            if not shown:
                root.deiconify()
                root.attributes("-topmost", True)
                shown = True
        elif shown:
            root.withdraw()
            shown = False
        root.after(16, follow)

    follow()
    root.mainloop()


if __name__ == "__main__":
    main()
