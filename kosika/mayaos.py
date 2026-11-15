"""MayaOS: the AI layer over the systems on this computer, as an app.

MayaOS is its own app beside Kosika Studio. Studio is where agents are
built and watched; MayaOS is the home they live in: the systems on this
computer (Windows, and any Linux under WSL), the services that keep
running, and doors to Studio and to Kosi.

The two apps know each other through small notes in the app folder: each
running app writes where it can be reached, and either can open the
other. Opening is always one of Kosika's own apps, started the same way
its shortcut starts it; nothing a page sends is ever run.

MayaOS only looks. Windows is read with what Python already knows about
it; Linux is read with the fixed commands of kosika.wsl, and only the
distributions the person named, in KOSIKA_LINUX or with the switch on
MayaOS's own page. Services can be paused and resumed, as in Studio.

    python -m kosika.mayaos            open MayaOS in a window of its own
    python -m kosika.mayaos --desktop  put MayaOS on the Desktop and in Start
"""

from __future__ import annotations

import ast
import ctypes
import getpass
import hmac
import json
import os
import platform
import secrets
import shutil
import subprocess
import re
import sys
import tempfile
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from . import wsl
from .desktop_app import announce, reachable

NAME = "MayaOS"
MODULE = "kosika.mayaos"
PAGE = Path(__file__).parent / "app_assets" / "mayaos.html"
#: Maya herself: the Kosika program that decides what MayaOS says and does.
MIND = Path(__file__).resolve().parent.parent / "mayaos" / "maya.kos"
MIND_SECONDS = 150
#: Kosi, the assistant: the folder of agents the Kosi pill uses, whose
#: `assistant` program answers what Maya hands over.
KOSI_AGENTS = Path(__file__).resolve().parent.parent / "examples" / "agents"
KOSI_SECONDS = 300
MAX_BODY = 16 * 1024


class MayaError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ the two apps


def app_folder() -> Path:
    from .desktop_app import home

    return home()


#: The apps MayaOS can open, each as its own shortcut starts it.
APPS = {"studio": "kosika.desktop_app", "kosi": "kosika.pill", "mayaos": MODULE}


def open_app(name: str, starter: Callable[[list[str]], Any] | None = None) -> str:
    """Open one of Kosika's apps: a window onto it when it is running,
    otherwise start it. Says which it did."""

    if name not in APPS:
        raise MayaError("There is no such app", 404)
    url = reachable(name) if name != "kosi" else ""
    if url:
        from .app import open_window

        if open_window(url):
            return "opened"
    from .desktop_app import code_folder, launcher

    command = [str(launcher()), "-m", APPS[name]]
    if starter is not None:
        starter(command)
    else:
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(  # noqa: S603 - a fixed module of Kosika's own
            command, cwd=code_folder(), creationflags=flags,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        )
    return "started"


# ------------------------------------------------------------------ what MayaOS reads


def _windows_memory() -> tuple[int, int]:
    class Status(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
            ("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong),
            ("page_total", ctypes.c_ulonglong), ("page_free", ctypes.c_ulonglong),
            ("virtual_total", ctypes.c_ulonglong), ("virtual_free", ctypes.c_ulonglong),
            ("extended", ctypes.c_ulonglong),
        ]

    status = Status()
    status.length = ctypes.sizeof(Status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
        return 0, 0
    return status.total - status.free, status.total


def windows() -> dict[str, Any]:
    """This computer, as Windows tells Python. Only looks."""

    found: dict[str, Any] = {
        "name": platform.node(),
        "system": f"{platform.system()} {platform.release()}",
        "version": platform.version(),
        "cores": os.cpu_count() or 0,
    }
    drive = Path(os.environ.get("SYSTEMDRIVE", "C:") + "\\") if sys.platform == "win32" else Path("/")
    try:
        disk = shutil.disk_usage(drive)
        found["disk"] = f"{disk.used / 2**30:.0f} of {disk.total / 2**30:.0f} GB used on {str(drive).rstrip(chr(92))}"
    except OSError:
        found["disk"] = "unknown"
    if sys.platform == "win32":
        used, total = _windows_memory()
        found["memory"] = f"{used / 2**20:.0f} of {total / 2**20:.0f} MB used" if total else "unknown"
        seconds = ctypes.windll.kernel32.GetTickCount64() // 1000  # type: ignore[attr-defined]
        hours, minutes = divmod(seconds // 60, 60)
        days, hours = divmod(hours, 24)
        found["uptime"] = (f"{days} d " if days else "") + f"{hours} h {minutes} min"
    else:
        found["memory"] = found["uptime"] = "unknown"
    return found


def _share(text: Any) -> float:
    """The share used from a line such as "629 of 7575 MB used", or -1."""

    found = re.search(r"([\d.]+)\s*\w*\s+of\s+([\d.]+)", str(text or ""))
    if not found or float(found.group(2)) == 0:
        return -1
    return round(min(1.0, float(found.group(1)) / float(found.group(2))), 4)


def reading(system: str, kind: str, memory: Any, disk: Any, updates: Any, awake: bool) -> dict[str, Any]:
    count = str(updates or "")
    return {
        "system": system, "kind": kind, "memory": _share(memory), "disk": _share(disk),
        "updates": int(count) if count.isdigit() else -1, "awake": awake,
    }


def _said_of(stdout: str) -> dict[str, Any]:
    """The record `kosika run` printed last."""

    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                value = ast.literal_eval(line)
            except (ValueError, SyntaxError):
                continue
            if isinstance(value, dict):
                return value
    raise MayaError("Maya gave no answer", 502)


def think(msg: dict[str, Any], services: Path | None, runner: Callable[..., Any] | None = None,
          linux: str = "") -> dict[str, Any]:
    """Run maya.kos once on what was said, with only the grants it declares,
    each to look: this computer, the one Linux she was let read, and the
    services file. It is run as `kosika run`, as any Kosika program is."""

    # A Linux reading may outlive a run that timed out and keep its folder
    # busy for a moment; that must not turn an answer into a failure.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        inputs = Path(work) / "input.json"
        inputs.write_text(json.dumps({"msg": msg}), encoding="utf-8")
        command = [sys.executable, "-m", "kosika.cli", "run", str(MIND), "--input", str(inputs),
                   "--trace", str(Path(work) / "trace.jsonl")]
        command += ["--computer"]
        if linux:
            command += ["--linux", linux]
        if services is not None:
            command += ["--services", str(services)]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        env["PYTHONPATH"] = str(MIND.parent.parent) + os.pathsep + env.get("PYTHONPATH", "")
        if runner is not None:
            done = runner(command)
        else:
            try:
                done = subprocess.run(  # noqa: S603 - Kosika running its own program
                    command, cwd=MIND.parent, env=env, capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=MIND_SECONDS, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except subprocess.TimeoutExpired as exc:
                raise MayaError("Maya took too long to answer", 504) from exc
        if done.returncode != 0:
            raise MayaError("Maya could not answer: " + (done.stderr or done.stdout).strip()[-300:], 502)
        return _said_of(done.stdout)


class Maya:
    """What the MayaOS page can ask for."""

    def __init__(self, services: Path | None = None, linux: list[str] | None = None,
                 settings: Path | None = None) -> None:
        named = services or (Path(os.environ["KOSIKA_SERVICES"]) if os.environ.get("KOSIKA_SERVICES") else None)
        self.services_file = named.resolve() if named is not None else None
        self.settings = settings or app_folder() / "mayaos.json"
        self._from_env = linux if linux is not None else [
            name.strip() for name in os.environ.get("KOSIKA_LINUX", "").split(",") if name.strip()
        ]
        self.token = secrets.token_urlsafe(24)
        self._lock = threading.Lock()
        self._kosi: Any = None
        # What Linux said when last read, so Maya can be told without
        # waking every distribution again.
        self._linux_seen: dict[str, dict[str, Any]] = {}

    # The Linux the person named: in KOSIKA_LINUX, or switched on here.
    def _saved(self) -> dict[str, Any]:
        try:
            saved = json.loads(self.settings.read_text(encoding="utf-8"))
            return saved if isinstance(saved, dict) else {}
        except (OSError, ValueError):
            return {}

    def named_linux(self) -> list[str]:
        chosen = [name for name in self._saved().get("linux", []) if isinstance(name, str)]
        return list(dict.fromkeys([*self._from_env, *chosen]))

    def name_linux(self, body: dict[str, Any]) -> dict[str, Any]:
        name, on = body.get("name"), body.get("on")
        if not isinstance(name, str) or not isinstance(on, bool):
            raise MayaError("Send the Linux's name and on: true or false")
        if name in self._from_env and not on:
            raise MayaError(f"{name} is named in KOSIKA_LINUX; take it out there", 409)
        if on and name not in wsl.distributions():
            raise MayaError(f"{name} is not installed under WSL", 404)
        with self._lock:
            saved = self._saved()
            chosen = [item for item in saved.get("linux", []) if isinstance(item, str) and item != name]
            saved["linux"] = chosen + ([name] if on else [])
            self.settings.parent.mkdir(parents=True, exist_ok=True)
            self.settings.write_text(json.dumps(saved, indent=2), encoding="utf-8")
        return self.home()

    def home(self) -> dict[str, Any]:
        from . import services as running

        services: list[dict[str, Any]] = []
        if self.services_file is not None:
            try:
                services = running.status(self.services_file)
            except (OSError, ValueError):
                services = []
        return {
            "person": getpass.getuser(),
            "windows": windows(),
            "linux": {"installed": wsl.distributions(), "named": self.named_linux()},
            "services_file": str(self.services_file or ""),
            "services": services,
            "studio": bool(reachable("studio")),
        }

    def linux_status(self, name: str) -> dict[str, Any]:
        if name not in self.named_linux():
            raise MayaError("MayaOS was not let read that Linux", 403)
        try:
            found = wsl.status(name)
        except wsl.WslError as exc:
            raise MayaError(str(exc), 502) from exc
        self._linux_seen[name] = found
        return found

    # ---- Maya's mind is a Kosika program; this only hands it what the app
    # can see and runs it with the one grant it declares.
    def readings(self, granted: str) -> list[dict[str, Any]]:
        """The Linux Maya is not reading herself this time, in the shape
        maya.kos reads: what was last seen of a named one, and the rest
        marked asleep. She reads this computer and `granted` herself."""

        named = self.named_linux()
        told = []
        for name in wsl.distributions():
            if name == granted:
                continue
            found = self._linux_seen.get(name) or {}
            told.append(reading(name, "linux", found.get("memory"), found.get("disk"), found.get("upgrades"), name in named))
        return told

    def kosi(self, text: str) -> Any:
        """What Kosi says to a request Maya handed over. Kosi is the
        assistant program in Kosi's folder of agents, run as the Kosi pill
        runs it: with a model on this computer if there is one, and with
        no to anything an agent asks to change, since no one is asked here."""

        if self._kosi is None:
            from .pill import Engine, local_binder

            self._kosi = Engine(KOSI_AGENTS, local_binder()[0])
        return self._kosi.ask(text, timeout=KOSI_SECONDS)

    def ask(self, body: dict[str, Any], runner: Callable[..., Any] | None = None,
            kosi: Callable[[str], Any] | None = None) -> dict[str, Any]:
        """Put what the person said to Maya, and do what she decides: only
        ever opening one of Kosika's own apps."""

        text = body.get("text", "")
        if not isinstance(text, str) or len(text) > 500:
            raise MayaError("Say something of at most 500 characters")
        installed = wsl.distributions()
        granted = next((name for name in self.named_linux() if name in installed), "")
        said = think(
            {"text": text, "person": getpass.getuser(), "readings": self.readings(granted),
             "studio": bool(reachable("studio"))},
            self.services_file, runner, granted,
        )
        hand = said.get("hand")
        if isinstance(hand, str) and hand:
            answer = (kosi or self.kosi)(hand)
            said.update(title="Kosi", text=answer.text, suggest=list(answer.suggest), by="kosi")
        opened = said.get("open")
        if opened in ("studio", "kosi"):
            try:
                said["did"] = open_app(opened)
            except (MayaError, OSError) as exc:
                said["text"] = f"{said.get('text', '')} It could not be opened: {exc}"
        return said

    def pause_service(self, body: dict[str, Any]) -> dict[str, Any]:
        from . import services as running

        if self.services_file is None:
            raise MayaError("MayaOS was started without a services file", 404)
        name, paused = body.get("name"), body.get("paused")
        if not isinstance(name, str) or not isinstance(paused, bool):
            raise MayaError("Send the service's name and paused: true or false")
        try:
            running.set_paused(self.services_file, name, paused)
        except (OSError, ValueError) as exc:
            raise MayaError(str(exc), 404) from exc
        return self.home()

    def open(self, body: dict[str, Any]) -> dict[str, Any]:
        name = body.get("app")
        if not isinstance(name, str) or name == "mayaos":
            raise MayaError("Name an app to open: studio or kosi")
        return {"app": name, "did": open_app(name)}


# ------------------------------------------------------------------ the server


def _handler(maya: Maya, port: list[int]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "MayaOS"

        def log_message(self, *_: Any) -> None:
            return

        def _send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; connect-src 'self'; font-src 'self'",
            )
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: Any) -> None:
            self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_BODY:
                raise MayaError("The request is empty or too large")
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                raise MayaError("The request must be an object")
            return body

        def _allowed(self, query: dict[str, list[str]]) -> bool:
            here = (f"127.0.0.1:{port[0]}", f"localhost:{port[0]}")
            if self.headers.get("Host", "") not in here:
                return False
            origin = self.headers.get("Origin")
            if origin and origin not in tuple("http://" + item for item in here):
                return False
            given = self.headers.get("X-Kosika-Token") or (query.get("token") or [""])[0]
            return hmac.compare_digest(given, maya.token)

        def _serve(self, method: str) -> None:
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            if method == "GET" and url.path.startswith("/fonts/"):
                from .fonts import served

                font = served(url.path[len("/fonts/"):])
                if font is None:
                    self._json(404, {"error": "There is no such font."})
                else:
                    self._send(200, font, "font/ttf")
                return
            if method == "GET" and url.path == "/favicon.ico":
                from .desktop_app import MAYAOS_COLOURS, icon_bytes

                self._send(200, icon_bytes(MAYAOS_COLOURS), "image/x-icon")
                return
            if not self._allowed(query):
                self._json(403, {"error": "This page was not opened from MayaOS."})
                return
            try:
                if method == "GET" and url.path == "/":
                    self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                elif method == "GET" and url.path == "/api/home":
                    self._json(200, maya.home())
                elif method == "GET" and url.path == "/api/linux/status":
                    self._json(200, maya.linux_status((query.get("name") or [""])[0]))
                elif method == "POST" and url.path == "/api/linux/name":
                    self._json(200, maya.name_linux(self._body()))
                elif method == "POST" and url.path == "/api/services/pause":
                    self._json(200, maya.pause_service(self._body()))
                elif method == "POST" and url.path == "/api/ask":
                    self._json(200, maya.ask(self._body()))
                elif method == "POST" and url.path == "/api/open":
                    self._json(200, maya.open(self._body()))
                else:
                    self._json(404, {"error": "Not found"})
            except MayaError as exc:
                self._json(exc.status, {"error": str(exc)})
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
            except Exception as exc:  # noqa: BLE001 - the page is told, not left hanging
                traceback.print_exc()
                self._json(500, {"error": f"MayaOS stumbled: {exc}"})

        def do_GET(self) -> None:  # noqa: N802
            self._serve("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._serve("POST")

    return Handler


def make_server(maya: Maya, port: int = 0) -> ThreadingHTTPServer:
    holder = [port]
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler(maya, holder))
    holder[0] = server.server_address[1]
    return server


def address(maya: Maya, server: ThreadingHTTPServer) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}/?token={maya.token}"


# ------------------------------------------------------------------ starting


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--desktop" in arguments:
        from .desktop_app import make_shortcuts

        for path in make_shortcuts(None, NAME, MODULE):
            print(f"  {path}")
        return 0
    # Started from its shortcut there is no console; what would be printed
    # goes to a log in the app folder.
    quiet = sys.stdout is None or "pythonw" in Path(sys.executable).name.lower()
    log = None
    if quiet:
        app_folder().mkdir(parents=True, exist_ok=True)
        log = (app_folder() / "mayaos.log").open("w", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
    try:
        existing = reachable("mayaos")
        if existing and "--no-window" not in arguments:
            # One MayaOS at a time: show the one that is running.
            from .app import open_window

            open_window(existing)
            return 0
        maya = Maya()
        server = make_server(maya, int(os.environ.get("MAYAOS_PORT", "0")))
        url = address(maya, server)
        announce("mayaos", url)
        print(f"MayaOS\n  {url}")
        if "--no-window" not in arguments:
            from .app import open_app_window

            window = open_app_window(url, app_folder() / "mayaos-window", announce_as="")
            if window is not None:
                def stop_with_window() -> None:
                    window.wait()
                    server.shutdown()

                threading.Thread(target=stop_with_window, daemon=True).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    except Exception:  # noqa: BLE001 - a person with no console must be told
        traceback.print_exc()
        if quiet and sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(None, f"MayaOS stopped. The reason is in {app_folder() / 'mayaos.log'}", NAME, 0x10)  # type: ignore[attr-defined]
        return 1
    finally:
        if log is not None:
            log.close()


if __name__ == "__main__":
    raise SystemExit(main())
