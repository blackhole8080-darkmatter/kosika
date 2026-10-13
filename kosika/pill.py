"""Kosi as a small pill that stays on the screen.

A small translucent, slightly grey pill stands against the right-hand edge
of the screen, half way down, above whatever else is open. Move the pointer over it and it wakes and says what
it is for. Click it, or press its key combination (Ctrl+Alt+K) from anywhere, and it opens a
line to type on. The answer appears above it.

It is the same assistant Studio shows: the Kosika program in the agents
folder named `assistant`, run with the same grants, which for that program
are a clock, its own memory, and passing a request to another agent in
the folder. The pill adds no power of its own. A folder for the agents to
work in is granted only when the person chooses one from the pill's menu,
for as long as the pill runs, and whatever an agent then wants to change
is shown above the pill with Allow and No before it happens. It does not listen,
and it does not watch the screen or the keyboard: the one key combination
it asks Windows to tell it about is its own.

This module keeps what can be tested apart from the window itself. The
window is tkinter, which comes with Python. On Windows with Pillow
installed the pill is a picture with smooth edges, a soft shadow and real
see-through; anywhere else it is drawn with plain shapes.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

NAME = "Kosi"
#: The agent the pill talks to, inside the agents folder.
ASSISTANT = "assistant/main.kos"
#: A port on this computer that only one pill can hold at a time.
ONE_PILL_PORT = 47613
#: How long an answer stays up, in seconds, for each of its characters.
SECONDS_PER_CHARACTER = 0.075
#: An answer longer than this is cut, with a pointer to Studio.
MAX_SHOWN = 700


# ------------------------------------------------------------------ what can be tested


@dataclass(frozen=True)
class Answer:
    text: str
    suggest: tuple[str, ...] = ()
    failed: bool = False


def shown(text: str) -> str:
    """An answer as the pill shows it: whole if short, cut if long."""

    text = text.strip()
    if len(text) <= MAX_SHOWN:
        return text
    return text[:MAX_SHOWN].rstrip() + "…\nOpen Kosika Studio for the rest."


def stay_seconds(text: str) -> float:
    """How long an answer is left on the screen before it fades."""

    return max(6.0, min(45.0, 3.0 + len(text) * SECONDS_PER_CHARACTER))


def place(
    area: tuple[int, int], size: tuple[int, int], margin: int = 8
) -> tuple[int, int]:
    """Where the pill's top-left corner goes: against the right-hand side of
    the area it may use, half way down. Because the right edge and the
    middle are what stay put, the pill grows to the left when it opens."""

    return (max(0, area[0] - size[0] - margin), max(0, (area[1] - size[1]) // 2))


def mix(a: str, b: str, share: float) -> str:
    """A colour `share` of the way from `a` to `b`, both written #rrggbb."""

    one = [int(a[i : i + 2], 16) for i in (1, 3, 5)]
    two = [int(b[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * share):02x}" for x, y in zip(one, two))


#: Body, ring, dot and words, at rest and awake. The fourth number of a
#: body is how solid it is, out of 255: at rest the screen shows through.
_LOOK = {
    False: ((44, 44, 48, 222), (170, 170, 176), (205, 205, 210), (236, 236, 240)),
    True: ((27, 27, 30, 255), (232, 129, 58), (246, 244, 239), (246, 244, 239)),
}


def _font(pixels: int) -> Any:
    """Kosika's own text face at a size in pixels; without its file, Segoe
    UI, and without that whatever Pillow has."""

    import os

    from PIL import ImageFont

    from .fonts import TEXT, font_path

    own = font_path(TEXT)
    if own is not None:
        try:
            face = ImageFont.truetype(str(own), pixels)
            face.set_variation_by_name("Medium")
            return face
        except (OSError, ValueError):
            pass
    folder = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name in ("segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(str(folder / name), pixels)
        except OSError:
            continue
    return ImageFont.load_default()


def pill_image(
    size: tuple[int, int],
    awake: bool,
    words: str = "",
    turn: int | None = None,
    scale: float = 1.0,
    pad: int = 0,
) -> Any:
    """The pill as a picture with smooth edges, or None without Pillow.

    A size taller than it is wide gives a pill standing on end, with the
    mark in its middle. The shapes are drawn four times too large and brought down to size,
    which is what makes a curve look like a curve; the words are then
    written at their real size so they stay sharp. `pad` is empty room on
    every side for a soft shadow. `turn` is where the turning arc stands
    while Kosi thinks, and None when it is not thinking. Every pixel says
    how solid it is, so the edge fades instead of ending in steps.
    """

    try:
        from PIL import Image, ImageDraw, ImageFilter
    except ImportError:
        return None
    width, height = size
    body, ring, dot, ink = _LOOK[awake]
    up = 4
    big = ((width + 2 * pad) * up, (height + 2 * pad) * up)
    left, top = pad * up, pad * up
    right, bottom = left + width * up - 1, top + height * up - 1
    # Lying down it is as tall as it is round; standing up, as wide.
    girth = min(width, height)
    radius = girth * up // 2
    art = Image.new("RGBA", big, (0, 0, 0, 0))
    if pad:
        drop = pad * up // 4
        ImageDraw.Draw(art).rounded_rectangle(
            (left, top + drop, right, bottom + drop),
            radius=radius,
            fill=(0, 0, 0, 96 if awake else 56),
        )
        art = art.filter(ImageFilter.GaussianBlur(pad * up / 2.6))
    draw = ImageDraw.Draw(art)
    # A hair of lighter edge, then the body inside it.
    line = max(up, round(girth * up / 34))
    edge = tuple(round(c + (255 - c) * 0.26) for c in body[:3]) + (
        min(255, body[3] + 40),
    )
    draw.rounded_rectangle((left, top, right, bottom), radius=radius, fill=edge)
    draw.rounded_rectangle(
        (left + line, top + line, right - line, bottom - line),
        radius=radius - line,
        fill=body,
    )
    # The mark, in the middle of the left-hand end: a ring with a dot, or
    # with an arc going round while it thinks.
    # Standing up, the mark is in the middle; lying down, at the left end.
    standing = height > width
    cx = left + radius + (0 if standing else up)
    cy = top + (height * up // 2 if standing else radius)
    r = round(girth * up * 0.25)
    stroke = max(2 * up, round(girth * up * 0.075))
    box = (cx - r, cy - r, cx + r, cy + r)
    draw.ellipse(box, outline=ring + (255,), width=stroke)
    if turn is None:
        d = max(up, round(girth * up * 0.085))
        draw.ellipse((cx - d, cy - d, cx + d, cy + d), fill=dot + (255,))
    else:
        draw.arc(box, start=turn, end=turn + 110, fill=dot + (255,), width=stroke)
    art = art.resize((width + 2 * pad, height + 2 * pad), Image.Resampling.LANCZOS)
    if words:
        ImageDraw.Draw(art).text(
            ((cx + r) // up + round(9 * scale), pad + height // 2 - 1),
            words,
            font=_font(round(13 * scale)),
            fill=ink + (255,),
            anchor="lm",
        )
    return art


def due_now(book: Any, now: float, told: set[str]) -> list[dict[str, Any]]:
    """The reminders whose time has come and that have not been said yet."""

    reminders = book.get("reminders") if isinstance(book, dict) else None
    if not isinstance(reminders, list):
        return []
    due = []
    for item in reminders:
        if not isinstance(item, dict):
            continue
        when, what = item.get("due"), item.get("what")
        if (
            isinstance(when, (int, float))
            and not isinstance(when, bool)
            and 0 < when <= now
            and isinstance(what, str)
            and f"{when}|{what}" not in told
        ):
            due.append(item)
    return due


def read_book(agents: Path) -> Any:
    """What the assistant remembers, straight from its memory file."""

    path = agents / "assistant" / "memory" / "books.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("me")
    except (OSError, ValueError, AttributeError):
        return None


class _NoModel:
    """Stands where a model would be when this computer has none, so the
    time, sums and reminders, which need no model, still work."""

    def complete(self, request: Any) -> Any:
        from .model import ModelProviderError

        raise ModelProviderError(
            "There is no model on this computer to answer that. "
            "Sums, the time and reminders still work."
        )


def _no_model(program: Any) -> dict[str, Any]:
    return {m.name: _NoModel() for m in program.models if m.provider is None}


def _clip(value: Any, most: int = 160) -> str:
    """A value as one short line."""

    words = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    words = " ".join(words.split())
    return words if len(words) <= most else words[: most - 1].rstrip() + "…"


def _action_words(action: dict[str, Any]) -> str:
    """One thing an agent wants to do, in a line or two."""

    given = action.get("arguments")
    given = given if isinstance(given, dict) else {}
    kind, operation = action.get("kind", ""), action.get("operation", "")
    if kind == "system:shell":
        arguments = given.get("args")
        words = (
            " ".join(str(a) for a in arguments) if isinstance(arguments, list) else ""
        )
        return f"start the program: {_clip(f'{operation} {words}'.strip())}"
    if kind == "system:files" and operation == "write_all":
        files = given.get("files")
        files = files if isinstance(files, list) else []
        names = [
            f"{item.get('path', '?')}{'' if item.get('exists') else ' (new)'}"
            for item in files[:6]
            if isinstance(item, dict)
        ]
        more = f" and {len(files) - 6} more" if len(files) > 6 else ""
        count = f"{len(files)} file{'' if len(files) == 1 else 's'}"
        note = given.get("note")
        noted = (
            f". {_clip(note, 220).rstrip('.')}"
            if isinstance(note, str) and note.strip()
            else ""
        )
        return f"write {count}: {', '.join(names)}{more}{noted}"
    if kind == "system:files" and operation in ("write", "create"):
        text = given.get("text")
        size = f"{len(text)} characters" if isinstance(text, str) else "some text"
        verb = "write" if operation == "write" else "create"
        return f"{verb} the file {given.get('path', '?')} ({size})"
    details = ", ".join(f"{key}: {_clip(value, 60)}" for key, value in given.items())
    return f"{action.get('tool', 'an action')}" + (f" ({details})" if details else "")


def ask_words(request: Any) -> str:
    """What an agent is asking to be allowed to do, for the bubble above
    the pill. It says who asks, and every change, and nothing else."""

    if not isinstance(request, dict):
        return "An agent wants to do something it could not describe."
    who = request.get("handed") or request.get("agent") or ""
    opening = f"The {who} agent wants to" if who else "An agent wants to"
    plan = request.get("plan")
    if isinstance(plan, list) and plan:
        lines = [
            f"{at}. {_action_words(step if isinstance(step, dict) else {})}"
            for at, step in enumerate(plan[:6], 1)
        ]
        if len(plan) > 6:
            lines.append(
                f"and {len(plan) - 6} more. Open Kosika Studio to read them all."
            )
        many = (
            "this change"
            if len(plan) == 1
            else f"these {len(plan)} changes, in this order"
        )
        warned = request.get("warnings")
        if isinstance(warned, list) and warned:
            lines.append(
                "Checked before asking, and something is wrong: " + _clip(warned[0])
            )
        return f"{opening} make {many}:\n" + "\n".join(lines)
    return f"{opening} {_action_words(request)}."


class Engine:
    """Runs the assistant for the pill, one request at a time."""

    def __init__(
        self,
        agents: Path,
        bind_models: Any,
        outside: tuple[tuple[str, ...], Path | None] = ((), None),
    ) -> None:
        from .app import App

        self.agents = agents
        #: How a folder is picked. The window sets this to its own folder
        #: window; until it does, no folder can be picked.
        self.pick: Callable[[], str] = lambda: ""
        self.app = App(
            agents,
            bind_models=bind_models or _no_model,
            pick_folder=lambda: self.pick(),
            outside=outside[0],
            outside_within=outside[1],
        )
        self._busy = threading.Lock()

    def folder(self) -> str:
        """The folder agents may work in, or "" when none is granted."""

        return str(self.app.allow_folder) if self.app.allow_folder else ""

    def choose_folder(self) -> str:
        """Let the person pick the folder agents may work in. What comes
        back is a sentence saying what happened."""

        from .app import AppError

        try:
            picked = self.app.pick_folder().get("folder")
            if not picked:
                return ""
            self.app.grant({"folder": "picked"})
        except AppError as exc:
            return str(exc)
        return (
            f"Agents may now read {picked}. Anything they want to change "
            "there, I will ask you first. This lasts until you quit me."
        )

    def forget_folder(self) -> str:
        from .app import AppError

        try:
            self.app.grant({"folder": "none"})
        except AppError as exc:
            return str(exc)
        return "Agents can no longer reach any folder."

    def ask(
        self,
        text: str,
        timeout: float = 900.0,
        decide: Callable[[dict[str, Any]], bool] | None = None,
    ) -> Answer:
        text = text.strip()
        if not text:
            return Answer("")
        if not self._busy.acquire(blocking=False):
            return Answer("One moment, I am still answering.", failed=True)
        try:
            try:
                job = self.app.start(ASSISTANT, {"text": text})
            except Exception as exc:  # noqa: BLE001 - shown to the person
                return Answer(f"I could not start: {exc}", failed=True)
            deadline = time.monotonic() + timeout
            asked: Any = None
            while job.state in ("running", "waiting") and time.monotonic() < deadline:
                request = job.request
                if (
                    job.state == "waiting"
                    and request is not None
                    and request is not asked
                ):
                    # The assistant itself changes nothing. An agent it
                    # handed a request to may ask to: the person is shown
                    # what, and with nobody to show it to the answer is no.
                    asked = request
                    allow = bool(decide(dict(request))) if decide else False
                    try:
                        self.app.decide(job.id, allow)
                    except Exception:  # noqa: BLE001 - it stopped waiting meanwhile
                        pass
                time.sleep(0.05)
            if job.state != "done":
                return Answer(job.error or "That took too long.", failed=True)
            result = job.result if isinstance(job.result, dict) else {}
            offers = result.get("suggest")
            return Answer(
                job.text or "",
                tuple(o for o in offers if isinstance(o, str))[:3]
                if isinstance(offers, list)
                else (),
            )
        finally:
            self._busy.release()


def local_binder() -> tuple[Any, str]:
    """A model on this computer for the assistant, as `kosika start` picks
    it, and its name. With none, the assistant still tells the time, does
    sums and keeps reminders; only open questions need a model."""

    from .model import OpenAICompatibleProvider
    from .quickstart import OLLAMA, choose_model, installed_models

    models = installed_models()
    chosen = choose_model(models) if models else None
    if not chosen:
        return None, ""
    provider = OpenAICompatibleProvider(OLLAMA, chosen, protocol="ollama", timeout=300)

    def bind(program: Any) -> dict[str, Any]:
        return {m.name: provider for m in program.models if m.provider is None}

    return bind, chosen


def only_one() -> socket.socket | None:
    """Hold the pill's port, or None when another pill already has it."""

    held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        held.bind(("127.0.0.1", ONE_PILL_PORT))
        held.listen(1)
        return held
    except OSError:
        held.close()
        return None


# ------------------------------------------------------------------ the hotkey


#: The key combinations tried, in order, until Windows grants one. Another
#: program may already hold the first; Ctrl+Alt+Space, for one, is often
#: taken. Each is (words, modifiers, key): Control is 2, Alt 1, Shift 4.
HOTKEYS: tuple[tuple[str, int, int], ...] = (
    ("Ctrl+Alt+K", 0x0002 | 0x0001, 0x4B),
    ("Ctrl+Shift+Space", 0x0002 | 0x0004, 0x20),
    ("Ctrl+Alt+J", 0x0002 | 0x0001, 0x4A),
)


def watch_hotkey(pressed: Callable[[], None]) -> str:
    """Ask Windows to say when the pill's key combination is pressed, and
    give back that combination in words, or "" when none could be had.

    Only that one combination is registered; no other key is ever seen.
    The combinations in HOTKEYS are tried in order, because another program
    may already hold one.
    """

    if sys.platform != "win32":
        return ""
    import ctypes
    from ctypes import wintypes

    got: list[str] = []
    done = threading.Event()

    def loop() -> None:
        user32 = ctypes.windll.user32
        for words, modifiers, key in HOTKEYS:
            # 0x4000 stops a held key from repeating.
            if user32.RegisterHotKey(None, 1, modifiers | 0x4000, key):
                got.append(words)
                break
        done.set()
        if not got:
            return
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message == 0x0312:  # WM_HOTKEY
                try:
                    pressed()
                except Exception:  # noqa: BLE001 - a key press must never end the pill
                    traceback.print_exc()

    threading.Thread(target=loop, daemon=True, name="kosi-hotkey").start()
    done.wait(5)
    return got[0] if got else ""


# ------------------------------------------------------------------ the window

_KEY = "#010203"  # painted where the window should be see-through
_REST, _WAKE, _INK, _ACCENT, _PAPER = (
    "#3c3c40",
    "#1b1b1e",
    "#f6f4ef",
    "#e8813a",
    "#f6f4ef",
)


def sharpen() -> None:
    """Tell Windows this program draws at the screen's real resolution.
    Without it Windows stretches the window on a scaled display, which is
    what makes it look soft."""

    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def usable_area(fallback: tuple[int, int]) -> tuple[int, int]:
    """The right and bottom edges of the screen without the taskbar."""

    if sys.platform != "win32":
        return fallback
    import ctypes
    from ctypes import wintypes

    rect = wintypes.RECT()
    try:
        # 0x0030 asks for the work area.
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return (rect.right, rect.bottom)
    except (AttributeError, OSError):
        pass
    return fallback


def _handle(window: Any) -> Any:
    """The window Windows knows a tkinter window by."""

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.GetParent.restype = wintypes.HWND
    user32.GetParent.argtypes = [wintypes.HWND]
    return user32.GetParent(window.winfo_id()) or window.winfo_id()


def round_corners(window: Any) -> None:
    """Ask Windows 11 to round a window's corners and give it a shadow.
    On an older Windows nothing happens and the corners stay square."""

    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    try:
        rounded = ctypes.c_int(2)  # 33 is the corner preference; 2 is round
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(_handle(window)),
            33,
            ctypes.byref(rounded),
            ctypes.sizeof(rounded),
        )
    except (AttributeError, OSError):
        pass


def layer(window: Any, picture: Any, x: int, y: int) -> bool:
    """Show a picture as the whole of a window, each pixel as solid as the
    picture says, and say whether that could be done.

    This is how a window gets a smooth edge and a shadow: Windows blends
    every pixel with what is behind it. Where the picture is empty a click
    goes through to whatever is underneath.
    """

    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    try:
        from PIL import Image, ImageChops
    except ImportError:
        return False

    class Header(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD), ("width", wintypes.LONG),
            ("height", wintypes.LONG), ("planes", wintypes.WORD),
            ("bits", wintypes.WORD), ("compression", wintypes.DWORD),
            ("image", wintypes.DWORD), ("xppm", wintypes.LONG),
            ("yppm", wintypes.LONG), ("used", wintypes.DWORD),
            ("important", wintypes.DWORD),
        ]  # fmt: skip

    class Blend(ctypes.Structure):
        _fields_ = [
            ("op", ctypes.c_ubyte), ("flags", ctypes.c_ubyte),
            ("alpha", ctypes.c_ubyte), ("format", ctypes.c_ubyte),
        ]  # fmt: skip

    try:
        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        handle = wintypes.HWND(_handle(window))
        long_ptr = ctypes.c_ssize_t
        user32.GetWindowLongPtrW.restype = long_ptr
        user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.SetWindowLongPtrW.restype = long_ptr
        user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, long_ptr]
        # -20 is the extended style; 0x80000 says "layered", 0x80 keeps it
        # off the taskbar.
        style = user32.GetWindowLongPtrW(handle, -20)
        if not style & 0x80000:
            user32.SetWindowLongPtrW(handle, -20, style | 0x80000 | 0x80)

        # Windows wants blue, green, red, each already multiplied by how
        # solid the pixel is.
        red, green, blue, solid = picture.split()
        data = Image.merge(
            "RGBA",
            (
                ImageChops.multiply(blue, solid),
                ImageChops.multiply(green, solid),
                ImageChops.multiply(red, solid),
                solid,
            ),
        ).tobytes()
        width, height = picture.size
        header = Header(ctypes.sizeof(Header), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)

        user32.GetDC.restype = wintypes.HDC
        user32.GetDC.argtypes = [wintypes.HWND]
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        gdi32.CreateDIBSection.argtypes = [
            wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
        ]  # fmt: skip
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.DeleteDC.argtypes = [wintypes.HDC]
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        user32.UpdateLayeredWindow.restype = wintypes.BOOL
        user32.UpdateLayeredWindow.argtypes = [
            wintypes.HWND, wintypes.HDC, ctypes.c_void_p, ctypes.c_void_p,
            wintypes.HDC, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
            wintypes.DWORD,
        ]  # fmt: skip

        screen = user32.GetDC(None)
        memory = gdi32.CreateCompatibleDC(screen)
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(
            memory, ctypes.byref(header), 0, ctypes.byref(bits), None, 0
        )
        if not bitmap or not bits:
            gdi32.DeleteDC(memory)
            user32.ReleaseDC(None, screen)
            return False
        ctypes.memmove(bits, data, len(data))
        before = gdi32.SelectObject(memory, bitmap)
        where, extent, origin = (
            wintypes.POINT(x, y),
            wintypes.SIZE(width, height),
            wintypes.POINT(0, 0),
        )
        blend = Blend(0, 0, 255, 1)  # 1: every pixel carries its own
        done = user32.UpdateLayeredWindow(
            handle, screen, ctypes.byref(where), ctypes.byref(extent), memory,
            ctypes.byref(origin), 0, ctypes.byref(blend), 2,
        )  # fmt: skip
        gdi32.SelectObject(memory, before)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory)
        user32.ReleaseDC(None, screen)
        return bool(done)
    except (AttributeError, OSError, ValueError):
        return False


class Pill:
    """The window. Small at rest, a line to type on when opened, and the
    answer above it."""

    #: Sizes as they would be on a screen that is not scaled.
    REST = (30, 58)
    WAKE = (136, 34)
    OPEN = (460, 46)
    #: Empty room around the pill for its shadow.
    PAD = 12

    def __init__(self, engine: Engine, model: str, hotkey: str) -> None:
        import tkinter as tk
        import tkinter.font as tkfont

        sharpen()
        self.tk = tk
        self.engine = engine
        self.model = model
        self.hotkey = hotkey
        self.root = root = tk.Tk()
        root.title(NAME)
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=_KEY)
        #: How much larger than life this screen draws: 1.25 at 125%.
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
        from .fonts import TEXT, lend_to_this_program

        family = TEXT if TEXT in lend_to_this_program() else "Segoe UI"
        self.font = tkfont.Font(family=family, size=10)
        self.small = tkfont.Font(family=family, size=9)
        self.canvas = tk.Canvas(root, bg=_KEY, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        # The line to type on is a window of its own, laid over the pill.
        self.line = tk.Toplevel(root)
        self.line.withdraw()
        self.line.overrideredirect(True)
        self.line.attributes("-topmost", True)
        self.line.configure(bg=_WAKE)
        self.entry = tk.Entry(
            self.line, font=self.font, bd=0, relief="flat", bg=_WAKE, fg=_INK,
            insertbackground=_ACCENT, highlightthickness=0,
        )  # fmt: skip
        self.entry.pack(fill="both", expand=True)
        self.bubble: Any = None
        self.opened = False
        self.hovering = False
        self.thinking = False
        self.is_hint = False
        self.asking = False
        self.turn = 0
        self.told: set[str] = set()
        self.fade: str | None = None

        self.canvas.bind("<Enter>", lambda _e: self.hover(True))
        self.canvas.bind("<Leave>", lambda _e: self.hover(False))
        self.canvas.bind("<Button-1>", lambda _e: self.open())
        self.canvas.bind("<Button-3>", self.menu)
        self.entry.bind("<Return>", lambda _e: self.submit())
        self.entry.bind("<Escape>", lambda _e: self.close())
        self.entry.bind(
            "<FocusOut>", lambda _e: self.root.after(150, self.close_if_idle)
        )
        root.update_idletasks()
        #: Whether the pill is a picture Windows blends with the screen. If
        #: not, it is plain shapes with one colour shown as nothing.
        self.layered = layer(root, self.picture(), *self.window_spot())
        if not self.layered:
            try:
                root.attributes("-transparentcolor", _KEY)
            except tk.TclError:
                pass
        self.settle()
        from tkinter import filedialog

        engine.pick = lambda: filedialog.askdirectory(
            parent=root, title="A folder for Kosika's agents to work in"
        )
        root.after(400, lambda: self.ask_quietly("hello"))
        root.after(15000, self.check_due)

    # -- drawing

    def px(self, length: float) -> int:
        """A length in this screen's own pixels."""

        return round(length * self.scale)

    def awake(self) -> bool:
        return self.opened or self.hovering or self.thinking

    def size(self) -> tuple[int, int]:
        width, height = (
            self.OPEN
            if self.opened
            else self.WAKE
            if self.hovering or self.thinking
            else self.REST
        )
        return (self.px(width), self.px(height))

    def spot(self) -> tuple[int, int]:
        """Where the pill's top-left corner is for the size it has now."""

        area = usable_area(
            (self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        )
        return place(area, self.size(), self.px(8))

    def window_spot(self) -> tuple[int, int]:
        """Where the window's corner is: the pill's, less the shadow's room."""

        x, y = self.spot()
        pad = self.px(self.PAD) if getattr(self, "layered", True) else 0
        return (x - pad, y - pad)

    def words(self) -> str:
        if self.opened:
            return ""
        return f"Ask {NAME}" if self.hovering else NAME if self.thinking else ""

    def picture(self) -> Any:
        return pill_image(
            self.size(),
            self.awake(),
            self.words(),
            self.turn if self.thinking else None,
            self.scale,
            self.px(self.PAD),
        )

    def settle(self) -> None:
        """Size, place and paint the pill for what it is doing now."""

        width, height = self.size()
        x, y = self.window_spot()
        pad = self.px(self.PAD) if self.layered else 0
        self.root.geometry(f"{width + 2 * pad}x{height + 2 * pad}+{x}+{y}")
        if not self.layered:
            self.root.attributes("-alpha", 0.97 if self.awake() else 0.62)
        self.paint()
        if self.hovering and not self.thinking and not self.opened:
            self.hint(
                f"Ask {NAME}   {self.hotkey}" if self.hotkey else f"Click to ask {NAME}"
            )

    def paint(self) -> None:
        if self.layered:
            self.root.update_idletasks()
            layer(self.root, self.picture(), *self.window_spot())
            return
        canvas, (width, height) = self.canvas, self.size()
        canvas.delete("all")
        awake = self.awake()
        fill = _WAKE if awake else _REST
        radius = height // 2
        canvas.create_oval(0, 0, height, height, fill=fill, outline=fill)
        canvas.create_oval(width - height, 0, width, height, fill=fill, outline=fill)
        canvas.create_rectangle(
            radius, 0, width - radius, height, fill=fill, outline=fill
        )
        ring = _ACCENT if awake else "#a4a4a8"
        cx, cy, r = radius + 1, height // 2, round(height * 0.25)
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=ring, width=3)
        if self.thinking:
            canvas.create_arc(
                cx - r, cy - r, cx + r, cy + r, start=-self.turn, extent=-110,
                style="arc", outline=_PAPER, width=3,
            )  # fmt: skip
        else:
            canvas.create_oval(
                cx - 3, cy - 3, cx + 3, cy + 3,
                fill=_PAPER if awake else "#c4c4c8", outline="",
            )  # fmt: skip
        canvas.create_text(
            cx + r + self.px(9), cy - 1, text=self.words(), anchor="w",
            fill=_INK if awake else "#e6e6e8", font=self.font,
        )  # fmt: skip

    def hint(self, words: str) -> None:
        if self.bubble is None:
            self.say(words, hint=True)

    def spin(self) -> None:
        if not self.thinking:
            return
        self.turn = (self.turn + 24) % 360
        self.paint()
        self.root.after(50, self.spin)

    # -- the bubble above the pill

    def say(
        self,
        words: str,
        hint: bool = False,
        suggest: tuple[str, ...] = (),
        choices: tuple[tuple[str, Callable[[], None]], ...] = (),
    ) -> None:
        """Show words above the pill. `suggest` are things to ask next;
        `choices` are answers to a question, each with what it does, and a
        bubble that has them stays until one is picked."""

        if self.asking and not choices:
            # A question is waiting for its answer; nothing may cover it.
            return
        self.asking = bool(choices)
        tk = self.tk
        self.unsay()
        bubble = tk.Toplevel(self.root)
        bubble.overrideredirect(True)
        bubble.attributes("-topmost", True)
        bubble.attributes("-alpha", 0.97 if not hint else 0.92)
        bubble.configure(bg=_WAKE)
        tk.Label(
            bubble, text=words, font=self.small if hint else self.font, bg=_WAKE,
            fg=_INK, justify="left", wraplength=self.px(400), padx=self.px(16),
            pady=self.px(8 if hint else 12),
        ).pack(anchor="w")  # fmt: skip
        if suggest:
            row = tk.Frame(bubble, bg=_WAKE)
            row.pack(anchor="w", padx=self.px(12), pady=(0, self.px(11)))
            for offer in suggest:
                chip = tk.Label(
                    row,
                    text=offer,
                    font=self.small,
                    bg=mix(_WAKE, "#ffffff", 0.1),
                    fg=_ACCENT,
                    padx=self.px(10),
                    pady=self.px(4),
                    cursor="hand2",
                )
                chip.pack(side="left", padx=self.px(3))
                chip.bind("<Button-1>", lambda _e, said=offer: self.ask(said))
        if choices:
            row = tk.Frame(bubble, bg=_WAKE)
            row.pack(anchor="e", padx=self.px(12), pady=(0, self.px(12)))
            for at, (label, chosen) in enumerate(choices):
                first = at == 0
                button = tk.Label(
                    row,
                    text=label,
                    font=self.font,
                    bg=_ACCENT if first else mix(_WAKE, "#ffffff", 0.14),
                    fg="#1b1b1e" if first else _INK,
                    padx=self.px(16),
                    pady=self.px(5),
                    cursor="hand2",
                )
                button.pack(side="left", padx=self.px(4))
                button.bind("<Button-1>", lambda _e, act=chosen: act())
        bubble.update_idletasks()
        width, height = bubble.winfo_reqwidth(), bubble.winfo_reqheight()
        # Its right edge lines up with the pill's, and it sits just above.
        pill_x, pill_y = self.spot()
        x = max(self.px(8), pill_x + self.size()[0] - width)
        y = max(self.px(8), pill_y - height - self.px(10))
        bubble.geometry(f"{width}x{height}+{x}+{y}")
        bubble.update_idletasks()
        round_corners(bubble)
        if not choices:
            bubble.bind("<Button-1>", lambda _e: self.unsay())
        self.bubble = bubble
        self.is_hint = hint
        if self.fade is not None and choices:
            self.root.after_cancel(self.fade)
            self.fade = None
        if not hint and not choices:
            if self.fade is not None:
                self.root.after_cancel(self.fade)
            self.fade = self.root.after(int(stay_seconds(words) * 1000), self.unsay)

    def unsay(self) -> None:
        if self.bubble is not None:
            self.bubble.destroy()
            self.bubble = None

    # -- what the person does

    def hover(self, over: bool) -> None:
        self.hovering = over
        if not over and self.bubble is not None and self.is_hint:
            self.unsay()
        self.settle()

    def open(self) -> None:
        if self.opened:
            self.entry.focus_force()
            return
        self.opened = True
        if self.is_hint:
            self.unsay()
        self.settle()
        (width, height), (x, y) = self.size(), self.spot()
        inset = self.px(10)
        self.line.geometry(
            f"{width - height - self.px(2) - height // 2}x{height - 2 * inset}"
            f"+{x + height + self.px(2)}+{y + inset}"
        )
        self.line.deiconify()
        self.line.lift()
        self.line.focus_force()
        self.entry.focus_force()

    def close(self) -> None:
        if not self.opened:
            return
        self.opened = False
        self.line.withdraw()
        self.entry.delete(0, "end")
        self.settle()

    def close_if_idle(self) -> None:
        if (
            self.opened
            and not self.entry.get().strip()
            and self.root.focus_get() is not self.entry
        ):
            self.close()

    def toggle(self) -> None:
        self.close() if self.opened else self.open()

    def submit(self) -> None:
        text = self.entry.get().strip()
        if not text:
            return
        self.close()
        self.ask(text)

    def ask(self, text: str) -> None:
        if self.thinking:
            return
        self.unsay()
        self.thinking = True
        self.settle()
        self.spin()

        def work() -> None:
            answer = self.engine.ask(text, decide=self.confirm)
            self.root.after(0, lambda: self.answered(answer))

        threading.Thread(target=work, daemon=True, name="kosi-answer").start()

    def confirm(self, request: dict[str, Any]) -> bool:
        """Ask the person whether an agent may do what it asks. Called
        from the answering thread, which waits here; no answer in five
        minutes is a no."""

        answered = threading.Event()
        said: list[bool] = []

        def pick(allow: bool) -> None:
            said.append(allow)
            self.asking = False
            self.unsay()
            answered.set()

        def show() -> None:
            self.say(
                ask_words(request),
                choices=(("Allow", lambda: pick(True)), ("No", lambda: pick(False))),
            )

        self.root.after(0, show)
        if not answered.wait(300):
            self.root.after(0, lambda: pick(False))
        return bool(said and said[0])

    def choose_folder(self) -> None:
        if self.thinking:
            self.say("One moment, I am still answering.")
            return
        words = self.engine.choose_folder()
        if words:
            self.say(words)

    def forget_folder(self) -> None:
        self.say(self.engine.forget_folder())

    def ask_quietly(self, text: str) -> None:
        """Ask without a spinning ring, for the hello when the pill starts."""

        def work() -> None:
            answer = self.engine.ask(text)
            if answer.text and not answer.failed:
                self.root.after(
                    0, lambda: self.say(shown(answer.text), suggest=answer.suggest)
                )

        threading.Thread(target=work, daemon=True, name="kosi-hello").start()

    def answered(self, answer: Answer) -> None:
        self.thinking = False
        self.settle()
        if answer.text:
            self.say(shown(answer.text), suggest=answer.suggest)

    def check_due(self) -> None:
        try:
            for item in due_now(read_book(self.engine.agents), time.time(), self.told):
                self.told.add(f"{item['due']}|{item['what']}")
                self.say(f"Reminder: {item['what']}.", suggest=("my reminders",))
                break
        finally:
            self.root.after(15000, self.check_due)

    def menu(self, event: Any) -> None:
        tk = self.tk
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label=f"Ask {NAME}", command=self.open)
        menu.add_command(label="Open Kosika Studio", command=self.open_studio)
        menu.add_separator()
        folder = self.engine.folder()
        menu.add_command(
            label=f"Agents may work in: {Path(folder).name}"
            if folder
            else "Agents can reach no folder",
            state="disabled",
        )
        menu.add_command(
            label="Choose a folder for agents…", command=self.choose_folder
        )
        if folder:
            menu.add_command(label="Take the folder away", command=self.forget_folder)
        menu.add_separator()
        menu.add_command(
            label=f"Model: {self.model}"
            if self.model
            else "No model: time, sums and reminders only",
            state="disabled",
        )
        menu.add_command(label=f"Quit {NAME}", command=self.root.destroy)
        menu.tk_popup(event.x_root, event.y_root)

    def open_studio(self) -> None:
        import subprocess

        from .desktop_app import code_folder, launcher

        subprocess.Popen(  # noqa: S603 - our own module, our own folder
            [str(launcher()), "-m", "kosika.desktop_app", str(self.engine.agents)],
            cwd=str(code_folder()),
        )

    def run(self) -> None:
        self.root.mainloop()


def main(argv: list[str] | None = None) -> int:
    """Start the pill. With no console, what would be printed goes to a log."""

    from .desktop_app import _tell, home
    from .quickstart import default_folder

    arguments = list(sys.argv[1:] if argv is None else argv)
    agents = Path(arguments[0]) if arguments else default_folder()
    log_path = home() / "pill.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", buffering=1) as log:
        sys.stdout = sys.stderr = log
        held = only_one()
        if held is None:
            print("Another Kosi is already on the screen.")
            return 0
        try:
            if not (agents / ASSISTANT).is_file():
                _tell(
                    f"{NAME} needs the assistant agent, and {agents / ASSISTANT} is not there."
                )
                return 2
            bind, model = local_binder()
            print(f"Kosi for {agents}; model: {model or 'none'}")
            from .outside import load_settings

            engine = Engine(agents, bind, load_settings())
            pill_holder: list[Pill] = []

            def pressed() -> None:
                if pill_holder:
                    pill_holder[0].root.after(0, pill_holder[0].toggle)

            hotkey = watch_hotkey(pressed)
            print(f"Shortcut: {hotkey or 'none could be had'}")
            pill = Pill(engine, model, hotkey)
            pill_holder.append(pill)
            pill.run()
            return 0
        except Exception:  # noqa: BLE001 - a person must be told, whatever it was
            traceback.print_exc(file=log)
            _tell(f"{NAME} stopped unexpectedly. The reason is in {log_path}")
            return 1
        finally:
            held.close()


if __name__ == "__main__":
    raise SystemExit(main())
