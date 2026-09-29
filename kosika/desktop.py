"""Windows desktop window and single-instance local Workbench host."""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .workbench import WorkbenchHandler, WorkbenchServer


class DesktopHandler(WorkbenchHandler):
    def do_POST(self) -> None:
        if self.path != "/api/desktop/stop":
            return super().do_POST()
        if not self._local():
            return
        if not hmac.compare_digest(
            self.headers.get("X-Kosika-Token", ""), self.server.token
        ):
            self._json(403, {"error": "Invalid desktop session"})
            return
        self._json(200, {"stopping": True})
        threading.Thread(target=self.server.shutdown, daemon=True).start()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def request(port: int, path: str, token: str | None = None) -> dict[str, Any]:
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Invalid desktop port")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=b"" if token else None,
        headers={"X-Kosika-Token": token} if token else {},
    )
    with opener.open(req, timeout=2) as response:
        data = response.read(1_048_577)
    if len(data) > 1_048_576:
        raise ValueError("Desktop response exceeds limit")
    result = json.loads(data)
    if not isinstance(result, dict):
        raise ValueError("Invalid desktop response")
    return result


def active_session(state_path: Path) -> dict[str, Any] | None:
    try:
        if state_path.stat().st_size > 4096:
            return None
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or not isinstance(state.get("token"), str):
            return None
        actual = request(state["port"], "/api/session")
        if not isinstance(actual.get("token"), str) or not hmac.compare_digest(
            actual["token"], state["token"]
        ):
            return None
        return state
    except (OSError, ValueError, KeyError, TypeError):
        return None


def browser_path() -> Path:
    for root in (
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("PROGRAMFILES"),
        os.environ.get("LOCALAPPDATA"),
    ):
        if root:
            for suffix in (
                "Microsoft/Edge/Application/msedge.exe",
                "Google/Chrome/Application/chrome.exe",
            ):
                candidate = Path(root) / suffix
                if candidate.is_file():
                    return candidate
    raise RuntimeError("Kosika Desktop needs Microsoft Edge or Google Chrome installed")


def open_window(port: int, data_dir: Path) -> None:
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Invalid desktop port")
    subprocess.Popen(
        [
            str(browser_path()),
            f"--app=http://127.0.0.1:{port}/",
            f"--user-data-dir={data_dir / 'Browser'}",
            "--no-first-run",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@contextmanager
def instance_lock(path: Path) -> Iterator[bool]:
    import msvcrt

    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            yield False
        else:
            try:
                yield True
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def launch(data_dir: Path, *, show_window: bool = True, port: int = 8792) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    state_path = data_dir / "session.json"
    with instance_lock(data_dir / "instance.lock") as owned:
        if not owned:
            for _ in range(30):
                existing = active_session(state_path)
                if existing:
                    if show_window:
                        open_window(existing["port"], data_dir)
                    return
                time.sleep(0.1)
            raise RuntimeError("Kosika is starting or stopping. Try again shortly.")
        # Never trust stale state: only this lock holder can start the desktop host.
        # Stable loopback origin preserves the dedicated browser profile's storage.
        with WorkbenchServer(port) as server:
            server.RequestHandlerClass = DesktopHandler
            state_path.write_text(
                json.dumps({"port": server.server_port, "token": server.token}),
                encoding="utf-8",
            )
            try:
                if show_window:
                    open_window(server.server_port, data_dir)
                server.serve_forever()
            finally:
                state_path.unlink(missing_ok=True)


def stop(data_dir: Path) -> bool:
    state = active_session(data_dir / "session.json")
    if state is None:
        return False
    request(state["port"], "/api/desktop/stop", state["token"])
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument(
        "--no-window",
        action="store_true",
        help="Start the local desktop host without a window",
    )
    args = parser.parse_args()
    if os.name != "nt" or not os.environ.get("LOCALAPPDATA"):
        parser.error("The desktop launcher currently supports Windows only")
    data_dir = Path(os.environ["LOCALAPPDATA"]) / "Kosika"
    if args.status:
        state = active_session(data_dir / "session.json")
        print(
            json.dumps(
                {
                    "running": state is not None,
                    "url": f"http://127.0.0.1:{state['port']}/" if state else None,
                }
            )
        )
        return 0
    data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=data_dir / "desktop.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        if args.stop:
            stop(data_dir)
        else:
            launch(data_dir, show_window=not args.no_window)
        return 0
    except Exception:
        logging.exception("Kosika Desktop could not complete the request")
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            f"Kosika could not start. See {data_dir / 'desktop.log'}",
            "Kosika",
            0x10,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
