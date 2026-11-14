"""The Kosika coder's hands on this Windows laptop: a cursor you can watch.

    python hands.py where                 # where the cursor is, and the screen's size
    python hands.py move X Y              # glide the cursor there, so you see it go
    python hands.py move-to notepad       # glide to the middle of that program's window
    python hands.py look notepad          # every button, menu and field there, by name
    python hands.py click-on notepad Edit # click the thing called that (moves the mouse)
    python hands.py press notepad Edit    # press it with the agent's own pointer; the mouse stays put
    python hands.py click notepad         # front that window and click its middle
    python hands.py type-into notepad "w" # front that window and type there
    python hands.py write notepad "w"     # the same, the agent's own pointer showing "typing"
    python hands.py read notepad          # "read", then the words in that program's text field
    python hands.py refuse "why"          # Kosika refused the action: say why, do nothing (exit 5)
    python hands.py click                 # a left click where the cursor is
    python hands.py type "some words"     # type them, a letter at a time
    python hands.py key enter|tab|escape  # press one key
    python hands.py open notepad          # start one of the allowed programs

Every action is one run of this file, which a Kosika agent asks for through
its shell tool, so Kosika asks the person before each one. Holding Escape
stops it: before every step and between the letters it types, an Escape
held down ends the action at once with exit code 3, and nothing more is
done. Only the programs named in OPENABLE can be started.

This file is only the hands: what to do, and whether the agent may, is
decided in Kosika (main.kos, with each agent's kit in kits.kos). Kosika
names the acting agent, its pointer's colour and its typing speed before the
action, all optional:

    python hands.py --agent coder-1 --colour pink --speed fast write notepad "hi"

Speeds: "steady" (a letter at a time), "fast" (about four times quicker) and
"instant" (the whole text pasted at once, the clipboard put back after; if
the clipboard holds a picture or files it is left alone and "fast" is used).
`read` gives the field's words back, so Kosika can check that what was
typed is really there before it says so.

It uses Windows' own SendInput through ctypes and nothing else.
"""

from __future__ import annotations

import ctypes
import json
import os
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
        subprocess.Popen([sys.executable, str(Path(__file__).with_name("ring.py")), "--colour", COLOUR], creationflags=flags,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _ring_started = True
        time.sleep(0.4)


AGENT = os.environ.get("KOSIKA_AGENT", "Kosika")
COLOUR = ""
#: Set while the agent acts with its own pointer only, so the ring round
#: the person's mouse is not shown: that mouse is not Kosika's then.
OWN_POINTER_ONLY = False
POINTERS = Path.home() / ".kosika" / "pointers"


def point_at(x: int, y: int, state: str = "idle") -> None:
    """Send this agent's own pointer (pointer.py) to x, y, starting it if
    it is not on the screen; the person's mouse does not move. The state,
    "idle", "typing" or "press", changes how the pointer is drawn."""
    POINTERS.mkdir(parents=True, exist_ok=True)
    target = POINTERS / f"{AGENT}.json"
    fresh = target.exists() and time.time() - target.stat().st_mtime < 80
    target.write_text(json.dumps({"x": x, "y": y, "state": state}), encoding="utf-8")
    if not fresh:
        ready = POINTERS / f"{AGENT}.ready"
        ready.unlink(missing_ok=True)
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen([sys.executable, str(Path(__file__).with_name("pointer.py")), "--name", AGENT,
                          "--colour", COLOUR],
                         creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # The first time, the pointer's three looks are designed (cursor.kos)
        # and rendered one after another, which can take twenty seconds; wait
        # until pointer.py says its windows are up, then a moment to glide in.
        for _ in range(160):
            if ready.exists():
                break
            time.sleep(0.25)
        time.sleep(0.8)


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def idle_seconds() -> float:
    """How long since the person last touched the keyboard or the mouse."""
    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO))
    user32.GetLastInputInfo(ctypes.byref(info))
    return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000


#: Told by Kosika (kit.kos) for an action that needs the keyboard, the mouse
#: or the front window: the seconds the person must have left them alone
#: first, and the longest to wait for that before doing nothing (exit 6).
QUIET, WAIT_MOST = 0.0, 60.0


def wait_for_quiet() -> None:
    if QUIET <= 0:
        return
    started, told = time.time(), False
    while idle_seconds() < QUIET:
        if stopped():
            print("stopped: Escape is held")
            sys.exit(3)
        if time.time() - started > WAIT_MOST:
            print(f"you kept working for {WAIT_MOST:.0f} s, so nothing was done")
            sys.exit(6)
        if not told:
            print("waiting for you to stop typing or moving the mouse", flush=True)
            told = True
        time.sleep(0.25)
    if told:
        print(f"waited {time.time() - started:.0f} s for you", flush=True)


def guard() -> None:
    if stopped():
        print("stopped: Escape is held")
        sys.exit(3)
    if not OWN_POINTER_ONLY:
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


def in_front(handle: int) -> bool:
    return user32.GetForegroundWindow() == handle


def to_front(handle: int) -> bool:
    """Restore the window and bring it to the front; True only if it really
    is in front. Windows refuses SetForegroundWindow to a background process
    while the person works in another window, so the request is made from
    the front window's own input thread, and then checked."""
    user32.ShowWindow(handle, 9)  # restore if minimised
    user32.SetForegroundWindow(handle)
    time.sleep(0.15)
    if not in_front(handle):
        front = user32.GetForegroundWindow()
        theirs = user32.GetWindowThreadProcessId(front, None)
        mine = kernel32.GetCurrentThreadId()
        if theirs and theirs != mine and user32.AttachThreadInput(mine, theirs, True):
            try:
                user32.BringWindowToTop(handle)
                user32.SetForegroundWindow(handle)
            finally:
                user32.AttachThreadInput(mine, theirs, False)
    time.sleep(0.3)
    return in_front(handle)


def must_be_in_front(handle: int, name: str) -> None:
    """Keys go to whatever window is in front, so nothing is typed unless the
    program asked for is: exit 4, nothing done."""
    if not to_front(handle):
        print(f"{name} could not be brought to the front, so nothing was typed (another window has the keyboard)")
        sys.exit(4)


#: The window keys are meant for, while typing: if another window comes to
#: the front (the person clicked it), typing stops there with exit 4.
TARGET = 0


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


def letter(ch: str) -> None:
    """One letter, down and up as separate inputs: sent together, the new
    Notepad drops some letters and repeats others."""
    down, up = INPUT(type=INPUT_KEYBOARD), INPUT(type=INPUT_KEYBOARD)
    down.ki.wScan = up.ki.wScan = ord(ch)
    down.ki.dwFlags, up.ki.dwFlags = UNICODE, UNICODE | KEYUP
    send(down)
    time.sleep(PAUSES[SPEED][0])
    send(up)


#: (between down and up, after each letter) in seconds, for each speed.
#: The new Notepad falls behind at 2 ms and 4 ms (about 110 letters a
#: second) and then puts later letters in place of earlier ones ("brown oox
#: 8888"); at 4 ms and 10 ms (about 60 a second) twenty runs in a row were exact.
PAUSES = {"steady": (0.015, 0.05), "fast": (0.004, 0.010), "instant": (0.004, 0.010)}
SPEED = "steady"


def still_in_front(done: int = 0) -> None:
    if TARGET and not in_front(TARGET):
        print(f"stopped after {done} letters: another window came to the front, so no more was typed")
        sys.exit(4)


def type_text(text: str, speed: str | None = None) -> str:
    """Type the text at the kit's speed; gives back how it was typed. Escape
    is looked at before every letter (or once, for an instant paste)."""
    global SPEED
    chosen = speed or SPEED
    if chosen == "instant":
        guard()
        still_in_front()
        if paste(text):
            return "instant"
        chosen = "fast"  # the clipboard held something that is not text: not touched
    SPEED = chosen
    for done, ch in enumerate(text):
        guard()
        still_in_front(done)
        if ch == "\n":
            key(KEYS["enter"])
        else:
            letter(ch)
        time.sleep(PAUSES[chosen][1])
    return chosen


CF_UNICODETEXT, GMEM_MOVEABLE, VK_CONTROL, VK_V = 13, 0x0002, 0x11, 0x56
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
user32.GetClipboardData.restype = ctypes.c_void_p
user32.SetClipboardData.restype = ctypes.c_void_p
user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]


def clipboard_open() -> bool:
    for _ in range(20):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.05)
    return False


def clipboard_text() -> str | None:
    handle = user32.GetClipboardData(CF_UNICODETEXT)
    if not handle:
        return None
    locked = kernel32.GlobalLock(handle)
    try:
        return ctypes.wstring_at(locked)
    finally:
        kernel32.GlobalUnlock(handle)


def clipboard_put(text: str) -> None:
    data = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(data)
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
    locked = kernel32.GlobalLock(handle)
    ctypes.memmove(locked, data, size)
    kernel32.GlobalUnlock(handle)
    user32.EmptyClipboard()
    user32.SetClipboardData(CF_UNICODETEXT, handle)


def paste(text: str) -> bool:
    """Put the whole text in at once: through the clipboard, with Ctrl+V,
    then put the person's words back on the clipboard. If the clipboard holds
    a picture or files, it is left alone and False is given back."""
    if not clipboard_open():
        return False
    try:
        if user32.CountClipboardFormats() and not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return False
        before = clipboard_text()
        clipboard_put(text.replace("\r\n", "\n").replace("\n", "\r\n"))
    finally:
        user32.CloseClipboard()
    held, v = INPUT(type=INPUT_KEYBOARD), INPUT(type=INPUT_KEYBOARD)
    held.ki.wVk, v.ki.wVk = VK_CONTROL, VK_V
    let_go, v_up = INPUT(type=INPUT_KEYBOARD), INPUT(type=INPUT_KEYBOARD)
    let_go.ki.wVk, v_up.ki.wVk = VK_CONTROL, VK_V
    let_go.ki.dwFlags = v_up.ki.dwFlags = KEYUP
    send(held, v, v_up, let_go)
    time.sleep(0.25)  # the program reads the clipboard before it is put back
    if clipboard_open():
        try:
            if before is None:
                user32.EmptyClipboard()
            else:
                clipboard_put(before)
        finally:
            user32.CloseClipboard()
    return True


def field_text(name: str) -> str | None:
    """What the program's text field holds now, read through Windows'
    accessibility (eyes.ps1 -Read), or None if it cannot be read."""
    run = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(EYES),
                          "-Class", WINDOW_CLASS[name], "-Read"],
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    try:
        said = json.loads(run.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    return said.get("text")


def text_field(name: str) -> dict | None:
    """The program's place for words: its first Document or Edit."""
    return next((t for t in look(name) if t.get("kind") in ("Document", "Edit")), None)


def main(args: list[str]) -> None:
    global OWN_POINTER_ONLY, AGENT, COLOUR, SPEED, TARGET, QUIET, WAIT_MOST
    # Who acts, in what colour, how fast, and how long to wait for the
    # person: told by Kosika before the action.
    while len(args) > 1 and args[0] in ("--agent", "--colour", "--speed", "--quiet", "--wait-most"):
        if args[0] == "--agent":
            AGENT = args[1]
        elif args[0] == "--colour":
            COLOUR = args[1]
        elif args[0] == "--quiet":
            QUIET = float(args[1])
        elif args[0] == "--wait-most":
            WAIT_MOST = float(args[1])
        elif args[1] in PAUSES:
            SPEED = args[1]
        args = args[2:]
    if not args:
        raise SystemExit(__doc__)
    wait_for_quiet()
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
    elif what == "press" and len(rest) > 1 and rest[0] in OPENABLE:
        OWN_POINTER_ONLY = True
        # Press a thing by its name WITHOUT moving the person's mouse: the
        # agent's own pointer (pointer.py) glides there, then Windows'
        # accessibility presses it.
        if not window(rest[0]):
            print(f"no {rest[0]} window to press in")
            sys.exit(2)
        wanted = " ".join(rest[1:])
        thing = find(look(rest[0]), wanted)
        if thing is None:
            print(f"nothing called {wanted!r} in {rest[0]}")
            sys.exit(2)
        point_at(thing["x"], thing["y"])
        time.sleep(0.8)
        guard()
        point_at(thing["x"], thing["y"], "press")
        done = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(EYES),
                               "-Class", WINDOW_CLASS[rest[0]], "-Press", thing["name"]],
                              capture_output=True, text=True, encoding="utf-8", timeout=60)
        said = json.loads((done.stdout or "{}").strip().splitlines()[-1] if done.stdout.strip() else "{}")
        if "error" in said or done.returncode:
            print(said.get("error") or done.stderr.strip()[:200])
            sys.exit(2)
        time.sleep(0.35)
        point_at(thing["x"], thing["y"], "idle")
        print(f"{said['how']} {thing['kind']} {said['pressed']!r} in {rest[0]}, your mouse not moved")
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
        must_be_in_front(handle, rest[0])
        TARGET = handle
        words, started = " ".join(rest[1:]), time.time()
        how = type_text(words)
        print(f"typed {len(words)} letters into {rest[0]} ({how}, {time.time() - started:.1f} s)")
    elif what == "write" and len(rest) > 1 and rest[0] in OPENABLE:
        # Type into the program with the agent's own pointer showing where,
        # tagged "typing"; the person's mouse is not moved.
        OWN_POINTER_ONLY = True
        handle = window(rest[0])
        if not handle:
            print(f"no {rest[0]} window to write in")
            sys.exit(2)
        # Restored and in front first: a minimised window's field is far off
        # the screen, and keys only reach the window in front.
        must_be_in_front(handle, rest[0])
        field = text_field(rest[0])
        x, y = (field["x"], field["y"]) if field else middle(handle)
        point_at(x, y, "typing")
        must_be_in_front(handle, rest[0])  # looking and drawing the pointer took time
        TARGET = handle
        words, started = " ".join(rest[1:]), time.time()
        how = type_text(words)
        point_at(x, y, "idle")
        print(f"typed {len(words)} letters into {rest[0]} ({how}, {time.time() - started:.1f} s)")
    elif what == "read" and rest and rest[0] in OPENABLE:
        # The first line says whether the field could be read; the words follow.
        there = field_text(rest[0]) if window(rest[0]) else None
        print("read" if there is not None else "unreadable")
        print(there or "", end="")
    elif what == "refuse":
        print("refused:", " ".join(rest))
        sys.exit(5)
    elif what == "click":
        click()
        print("clicked at", *where())
    elif what == "type" and rest:
        words, started = " ".join(rest), time.time()
        how = type_text(words)
        print(f"typed {len(words)} letters ({how}, {time.time() - started:.1f} s); not checked: no program was named")
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
