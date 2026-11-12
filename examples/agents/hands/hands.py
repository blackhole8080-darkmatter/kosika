"""The Kosika coder's hands on this Windows laptop: a cursor you can watch.

    python hands.py where                 # where the cursor is, and the screen's size
    python hands.py move X Y              # glide the cursor there, so you see it go
    python hands.py move-to notepad       # glide to the middle of that program's window
    python hands.py look notepad          # every button, menu and field there, by name
    python hands.py click-on notepad Edit # click the thing called that
    python hands.py click notepad         # front that window and click its middle
    python hands.py type-into notepad "w" # front that window and type there
    python hands.py click                 # a left click where the cursor is
    python hands.py type "some words"     # type them, a letter at a time
    python hands.py key enter|tab|escape  # press one key
    python hands.py open notepad          # start one of the allowed programs

Every action is one run of this file, which a Kosika agent asks for through
its shell tool, so Kosika asks the person before each one. Holding Escape
stops it: before every step and between the letters it types, an Escape
held down ends the action at once with exit code 3, and nothing more is
done. Only the programs named in OPENABLE can be started.

It uses Windows' own SendInput through ctypes and nothing else.
"""

from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)
# Real pixels, not the scaled ones, so a move lands where a click lands.
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except (AttributeError, OSError):
    pass

ESCAPE = 0x1B
KEYS = {"enter": 0x0D, "tab": 0x09, "escape": 0x1B, "backspace": 0x08, "space": 0x20}
OPENABLE = {"notepad": ["notepad.exe"], "calculator": ["calc.exe"]}
WINDOW_CLASS = {"notepad": "Notepad", "calculator": "ApplicationFrameWindow"}
INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
LEFTDOWN, LEFTUP = 0x0002, 0x0004
KEYUP, UNICODE = 0x0002, 0x0004


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUT(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUT)]


def stopped() -> bool:
    return bool(user32.GetAsyncKeyState(ESCAPE) & 0x8000)


ACTIVE = Path.home() / ".kosika" / "hands-active"
_ring_started = False


def ring() -> None:
    """Show Kosika's own cursor (ring.py, an orange ring round the pointer)
    while the hands act: say they are acting now, and start the ring if it
    is not already there."""
    global _ring_started
    ACTIVE.parent.mkdir(parents=True, exist_ok=True)
    fresh = ACTIVE.exists() and time.time() - ACTIVE.stat().st_mtime < 50
    ACTIVE.touch()
    if not fresh and not _ring_started:
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen([sys.executable, str(Path(__file__).with_name("ring.py"))], creationflags=flags,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _ring_started = True
        time.sleep(0.4)


def guard() -> None:
    if stopped():
        print("stopped: Escape is held")
        sys.exit(3)
    ring()


def send(*inputs: INPUT) -> None:
    array = (INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))


def where() -> tuple[int, int]:
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def move(x: int, y: int) -> None:
    width, height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    x, y = max(0, min(width - 1, x)), max(0, min(height - 1, y))
    start_x, start_y = where()
    steps = 30
    for step in range(1, steps + 1):
        guard()
        part = step / steps
        ease = part * part * (3 - 2 * part)
        user32.SetCursorPos(round(start_x + (x - start_x) * ease), round(start_y + (y - start_y) * ease))
        time.sleep(0.012)


def window(name: str) -> int:
    """The program's window, waited for up to eight seconds, or 0."""
    for _ in range(40):
        handle = user32.FindWindowW(WINDOW_CLASS.get(name), None)
        if handle:
            return handle
        time.sleep(0.2)
    return 0


def to_front(handle: int) -> None:
    user32.ShowWindow(handle, 9)  # restore if minimised
    user32.SetForegroundWindow(handle)
    time.sleep(0.3)


def middle(handle: int) -> tuple[int, int]:
    box = wintypes.RECT()
    user32.GetWindowRect(handle, ctypes.byref(box))
    return (box.left + box.right) // 2, (box.top + box.bottom) // 2


EYES = Path(__file__).with_name("eyes.ps1")


def look(name: str, most: int = 150) -> list[dict]:
    """What the program's window shows, by name: eyes.ps1 reads Windows' own
    accessibility tree, so there is no picture and no model to guess."""
    run = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(EYES),
                          "-Class", WINDOW_CLASS[name], "-Most", str(most)],
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    things = []
    for line in run.stdout.splitlines():
        try:
            thing = json.loads(line)
        except ValueError:
            continue
        if "error" not in thing:
            things.append(thing)
    return things


def find(things: list[dict], wanted: str) -> dict | None:
    """The thing named exactly so, else the first whose name begins so,
    ignoring case: "Bold" finds "Bold (Ctrl+B)"."""
    low = wanted.lower()
    exact = [t for t in things if (t.get("name") or "").lower() == low]
    if exact:
        return exact[0]
    return next((t for t in things if (t.get("name") or "").lower().startswith(low)), None)


def click() -> None:
    guard()
    down, up = INPUT(type=INPUT_MOUSE), INPUT(type=INPUT_MOUSE)
    down.mi.dwFlags, up.mi.dwFlags = LEFTDOWN, LEFTUP
    send(down, up)


def key(code: int) -> None:
    guard()
    down, up = INPUT(type=INPUT_KEYBOARD), INPUT(type=INPUT_KEYBOARD)
    down.ki.wVk = up.ki.wVk = code
    up.ki.dwFlags = KEYUP
    send(down, up)


def type_text(text: str) -> None:
    for letter in text:
        guard()
        if letter == "\n":
            key(KEYS["enter"])
        else:
            # Down and up as separate inputs with a pause between: sent
            # together, the new Notepad drops some letters and repeats others.
            down, up = INPUT(type=INPUT_KEYBOARD), INPUT(type=INPUT_KEYBOARD)
            down.ki.wScan = up.ki.wScan = ord(letter)
            down.ki.dwFlags, up.ki.dwFlags = UNICODE, UNICODE | KEYUP
            send(down)
            time.sleep(0.015)
            send(up)
        time.sleep(0.05)


def main(args: list[str]) -> None:
    if not args:
        raise SystemExit(__doc__)
    what, rest = args[0], args[1:]
    if what == "where":
        x, y = where()
        print(f"cursor {x} {y} screen {user32.GetSystemMetrics(0)} {user32.GetSystemMetrics(1)}")
    elif what == "move" and len(rest) == 2:
        move(int(float(rest[0])), int(float(rest[1])))
        print("moved to", *where())
    elif what == "move-to" and rest and rest[0] in OPENABLE:
        handle = window(rest[0])
        if not handle:
            print(f"no {rest[0]} window to move to")
            sys.exit(2)
        to_front(handle)
        move(*middle(handle))
        print(f"moved to the middle of {rest[0]},", *where())
    elif what == "look" and rest and rest[0] in OPENABLE:
        if not window(rest[0]):
            print(f"no {rest[0]} window to look at")
            sys.exit(2)
        for thing in look(rest[0]):
            print(f"{thing['kind']}: {thing['name']}")
    elif what == "click-on" and len(rest) > 1 and rest[0] in OPENABLE:
        # Click a thing by the name the window gives it, not by guessing pixels.
        handle = window(rest[0])
        if not handle:
            print(f"no {rest[0]} window to click in")
            sys.exit(2)
        to_front(handle)
        wanted = " ".join(rest[1:])
        thing = find(look(rest[0]), wanted)
        if thing is None:
            print(f"nothing called {wanted!r} in {rest[0]}")
            sys.exit(2)
        move(thing["x"], thing["y"])
        click()
        print(f"clicked {thing['kind']} {thing['name']!r} in {rest[0]}")
    elif what == "click" and rest and rest[0] in OPENABLE:
        # Aimed at a program: front it and click its middle, whatever the
        # person clicked while answering the last question.
        handle = window(rest[0])
        if not handle:
            print(f"no {rest[0]} window to click")
            sys.exit(2)
        to_front(handle)
        move(*middle(handle))
        click()
        print(f"clicked in {rest[0]} at", *where())
    elif what == "type-into" and len(rest) > 1 and rest[0] in OPENABLE:
        handle = window(rest[0])
        if not handle:
            print(f"no {rest[0]} window to type into")
            sys.exit(2)
        to_front(handle)
        type_text(" ".join(rest[1:]))
        print(f"typed {len(' '.join(rest[1:]))} letters into {rest[0]}")
    elif what == "click":
        click()
        print("clicked at", *where())
    elif what == "type" and rest:
        type_text(" ".join(rest))
        print(f"typed {len(' '.join(rest))} letters")
    elif what == "key" and rest and rest[0] in KEYS:
        key(KEYS[rest[0]])
        print("pressed", rest[0])
    elif what == "open" and rest and rest[0] in OPENABLE:
        guard()
        subprocess.Popen(OPENABLE[rest[0]])
        handle = window(rest[0])
        if handle:
            to_front(handle)
        print("opened", rest[0])
    else:
        print(f"not something the hands do: {' '.join(args)}")
        sys.exit(2)


if __name__ == "__main__":
    main(sys.argv[1:])
