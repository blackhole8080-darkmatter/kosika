"""The Kosika coder's hands on this Windows laptop: a cursor you can watch.

    python hands.py where                 # where the cursor is, and the screen's size
    python hands.py move X Y              # glide the cursor there, so you see it go
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
import subprocess
import sys
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

ESCAPE = 0x1B
KEYS = {"enter": 0x0D, "tab": 0x09, "escape": 0x1B, "backspace": 0x08, "space": 0x20}
OPENABLE = {"notepad": ["notepad.exe"], "calculator": ["calc.exe"]}
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


def guard() -> None:
    if stopped():
        print("stopped: Escape is held")
        sys.exit(3)


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
            down, up = INPUT(type=INPUT_KEYBOARD), INPUT(type=INPUT_KEYBOARD)
            down.ki.wScan = up.ki.wScan = ord(letter)
            down.ki.dwFlags, up.ki.dwFlags = UNICODE, UNICODE | KEYUP
            send(down, up)
        time.sleep(0.02)


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
        time.sleep(1.5)
        print("opened", rest[0])
    else:
        print(f"not something the hands do: {' '.join(args)}")
        sys.exit(2)


if __name__ == "__main__":
    main(sys.argv[1:])
