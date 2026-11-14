"""A Kosika agent's own pointer on the screen, beside the person's mouse.

    python pointer.py --name coder-1 --colour pink   # hands.py starts one for each agent

Its look is designed in Kosika (cursor.kos, with Kosika Designs' palette):
a vector arrow with the agent's name on a tag, drawn as SVG and rendered
once, at this screen's real resolution, to a picture with soft edges
(~/.kosika/pointers/NAME-STATE-TINT-LOOK@SCALE.png, LOOK changing whenever the
Kosika files of its look change). It is shown in a window of its own that
is see-through wherever the picture is, above everything, and never takes a
click. It does not move the mouse: it glides to where the agent is about to
act, which the agent writes to ~/.kosika/pointers/NAME.json ({"x", "y"}),
and the agent then acts there through Windows' accessibility (eyes.ps1
-Press), so the person's own mouse stays where they left it and several
agents can be seen at once. It is drawn in three states, all in the colour
the agent's kit asks for (kits.kos): "idle", "typing" (its tag says so) and
"press" (rings spread from the tip); the agent says which in the same file
({"x", "y", "state"}). It hides after a few quiet seconds and ends
after a quiet minute and a half.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
POINTERS = Path.home() / ".kosika" / "pointers"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
SHOW_FOR = 6.0
QUIT_AFTER = 90.0

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except (AttributeError, OSError):
    pass

WS_POPUP = 0x80000000
WS_EX = 0x80000 | 0x20 | 0x8 | 0x80 | 0x08000000  # layered, click-through, topmost, tool window, no activate
ULW_ALPHA, AC_SRC_ALPHA = 2, 1
SWP = 0x0001 | 0x0004 | 0x0010  # no size, no z-order change, no activate


def scale_here() -> float:
    try:
        return max(1.0, user32.GetDpiForSystem() / 96)
    except (AttributeError, OSError):
        return 1.0


#: Runs one workflow of a Kosika file and prints its result as JSON.
RUNNER = (
    "import json, sys\n"
    "from kosika.modules import load_program\n"
    "from kosika.runtime import run\n"
    "ask = json.loads(open(sys.argv[3], encoding='utf-8').read())\n"
    "print(json.dumps(run(load_program(sys.argv[1]), sys.argv[2], None, inputs=ask)))\n"
)
STATES = ("idle", "typing", "press")

#: The Kosika files the pointer's look is drawn from. A kept picture is used
#: only while these are as they were when it was drawn: its name carries a
#: short fingerprint of them, so a change to the look (a new mascot, a new
#: palette) draws the pointer afresh instead of showing the old one.
LOOK = [HERE / "cursor.kos", HERE / "pointer_look.kos", HERE / "mascots.kos",
        REPO / "kosika" / "std" / "designs_palette.kos"]


def look_print() -> str:
    import hashlib
    seen = hashlib.sha256()
    for path in LOOK:
        if path.exists():
            seen.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return seen.hexdigest()[:8]


def picture(name: str, colour: str, state: str, scale: float) -> tuple[Path, int]:
    """The pointer's picture in one state, and where its tip is in it:
    designed by cursor.kos, rendered by Chrome, kept for next time."""
    POINTERS.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    tint = "".join(c for c in colour.lower() if c.isalnum()) or "auto"
    png = POINTERS / f"{safe}-{state}-{tint}-{look_print()}@{scale:.2f}.png"
    tip_file = png.with_suffix(".tip")
    if png.exists() and tip_file.exists():
        return png, int(tip_file.read_text())
    with tempfile.TemporaryDirectory() as work:
        ask = Path(work) / "ask.json"
        ask.write_text(json.dumps({"p": {"name": name, "scale": scale, "colour": colour, "state": state}}), encoding="utf-8")
        ran = subprocess.run([sys.executable, "-c", RUNNER, str(HERE / "cursor.kos"), "main", str(ask)], cwd=work,
                             capture_output=True, text=True, encoding="utf-8",
                             env={**os.environ, "PYTHONPATH": str(REPO)}, timeout=120)
        drawn = json.loads(ran.stdout.strip().splitlines()[-1])
        page = Path(work) / "pointer.html"
        page.write_text('<!doctype html><html><body style="margin:0;background:transparent">' + drawn["svg"] + "</body></html>",
                        encoding="utf-8")
        subprocess.run([str(CHROME), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                        "--default-background-color=00000000", f"--window-size={drawn['width']},{drawn['height']}",
                        f"--screenshot={png}", page.as_uri()], capture_output=True, timeout=120)
    tip_file.write_text(str(int(drawn["tip"])))
    return png, int(drawn["tip"])


class BLEND(ctypes.Structure):
    _fields_ = [("op", ctypes.c_byte), ("flags", ctypes.c_byte), ("alpha", ctypes.c_ubyte), ("format", ctypes.c_byte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("width", wintypes.LONG), ("height", wintypes.LONG), ("planes", wintypes.WORD),
                ("bits", wintypes.WORD), ("compression", wintypes.DWORD), ("image_size", wintypes.DWORD),
                ("xppm", wintypes.LONG), ("yppm", wintypes.LONG), ("used", wintypes.DWORD), ("important", wintypes.DWORD)]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("proc", WNDPROC), ("cls_extra", ctypes.c_int), ("wnd_extra", ctypes.c_int),
                ("instance", wintypes.HINSTANCE), ("icon", wintypes.HICON), ("cursor", wintypes.HANDLE),
                ("background", wintypes.HBRUSH), ("menu", wintypes.LPCWSTR), ("name", wintypes.LPCWSTR)]


user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT), ctypes.POINTER(wintypes.SIZE),
                                       wintypes.HDC, ctypes.POINTER(wintypes.POINT), wintypes.DWORD, ctypes.POINTER(BLEND), wintypes.DWORD]


def window_with(png: Path) -> tuple[int, int, int]:
    """A see-through, click-through, topmost window showing the picture with
    its soft edges (per-pixel alpha). Returns the window and its size."""
    from PIL import Image

    image = Image.open(png).convert("RGBA")
    width, height = image.size
    # Windows wants blue, green, red, alpha, each colour already multiplied by alpha.
    raw = image.tobytes()
    pixels = bytearray(len(raw))
    for i in range(0, len(raw), 4):
        pr, pg, pb, pa = raw[i], raw[i + 1], raw[i + 2], raw[i + 3]
        pixels[i], pixels[i + 1], pixels[i + 2], pixels[i + 3] = pb * pa // 255, pg * pa // 255, pr * pa // 255, pa
    instance = kernel32.GetModuleHandleW(None)
    if not getattr(window_with, "registered", False):
        # One window class for all the states, its procedure kept alive while they live.
        window_with.proc = WNDPROC(lambda h, m, w, l: user32.DefWindowProcW(h, m, w, l))
        cls = WNDCLASS(0, window_with.proc, 0, 0, instance, None, None, None, None, "KosikaPointer")
        user32.RegisterClassW(ctypes.byref(cls))
        window_with.cls = cls
        window_with.registered = True
    hwnd = user32.CreateWindowExW(WS_EX, "KosikaPointer", "Kosika pointer", WS_POPUP, 0, 0, width, height, None, None, instance, None)
    screen = user32.GetDC(None)
    memory = gdi32.CreateCompatibleDC(screen)
    header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    bitmap = gdi32.CreateDIBSection(screen, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
    ctypes.memmove(bits, bytes(pixels), len(pixels))
    gdi32.SelectObject(memory, bitmap)
    size, origin = wintypes.SIZE(width, height), wintypes.POINT(0, 0)
    place = wintypes.POINT(-width, -height)
    blend = BLEND(0, 0, 255, AC_SRC_ALPHA)
    user32.UpdateLayeredWindow(hwnd, screen, ctypes.byref(place), ctypes.byref(size), memory, ctypes.byref(origin), 0,
                               ctypes.byref(blend), ULW_ALPHA)
    return hwnd, width, height


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="Kosika")
    parser.add_argument("--colour", default="")
    args = parser.parse_args()
    scale = scale_here()
    windows, tips = {}, {}
    for state in STATES:
        png, tips[state] = picture(args.name, args.colour, state, scale)
        windows[state] = window_with(png)[0]
    target = POINTERS / f"{args.name}.json"
    here = [user32.GetSystemMetrics(0) / 2, user32.GetSystemMetrics(1) / 2]
    # Say the windows are up, so the hands wait for this and not just for the
    # pictures: else the first action happens while the pointer is still
    # gliding in from the middle of the screen.
    (POINTERS / f"{args.name}.ready").write_text(str(os.getpid()), encoding="utf-8")
    shown = ""
    message = wintypes.MSG()
    while True:
        while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        try:
            told = json.loads(target.read_text(encoding="utf-8"))
            quiet = time.time() - target.stat().st_mtime
        except (OSError, ValueError):
            told, quiet = None, 1e9
        if quiet > QUIT_AFTER:
            break
        state = (told or {}).get("state", "idle")
        state = state if state in windows else "idle"
        if told and quiet < SHOW_FOR:
            # Glide a fifth of the way each frame: quick, and the eye can follow it.
            here[0] += (told["x"] - here[0]) * 0.2
            here[1] += (told["y"] - here[1]) * 0.2
            tip = tips[state]
            user32.SetWindowPos(windows[state], -1, int(here[0]) - tip, int(here[1]) - tip, 0, 0, SWP)
            if shown != state:
                user32.ShowWindow(windows[state], 4)  # show, without taking the keyboard
                if shown:
                    user32.ShowWindow(windows[shown], 0)
                shown = state
        elif shown:
            user32.ShowWindow(windows[shown], 0)
            shown = ""
        time.sleep(0.016)


if __name__ == "__main__":
    main()
