"""Approvals asked in a folder, so the person can answer from anywhere.

`kosika run --approve-in FOLDER` does not need a terminal. Each action that
needs approval is written to the folder as one ask, and the run waits for
an answer next to it:

    FOLDER/<id>.ask.json      written by the run: what would be done
    FOLDER/<id>.answer.json   written by the person: {"answer": "yes", "by": "..."}

Anything that can put a file in that folder can answer: `kosika answer`,
a phone, Studio, another Kosika program. The id is new for every ask, so an
answer left over from an earlier run can never approve a later one, and the
approval is bound to the ask's own plan and action hashes, so an answer
cannot change what is done. Whoever can write to the folder can approve:
it must be a folder only the person can write to.

The protocol is the same for both engines; the Go engine's
`kosika-native run --approve-in` and `kosika-native answer` read and write
exactly these files.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ASK = ".ask.json"
ANSWER = ".answer.json"
#: How long a run waits for an answer before it counts as a no.
DEFAULT_WAIT_SECONDS = 600
POLL_SECONDS = 0.5


class AskFolderError(ValueError):
    """The folder cannot be used, or an answer is not one."""


def says(request: dict[str, Any]) -> str:
    """One line a person can read on a phone: what would be done."""

    step = " / ".join(str(part) for part in request.get("step", []))
    arguments = json.dumps(request.get("arguments", {}), ensure_ascii=False)
    return f"{step}: {request.get('tool', '')} {arguments}".strip()


def _write_atomically(path: Path, value: dict[str, Any]) -> None:
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(partial, path)


def put_ask(folder: Path, request: dict[str, Any]) -> str:
    """Write one ask and give back its id."""

    folder.mkdir(parents=True, exist_ok=True)
    ask_id = secrets.token_hex(4)
    _write_atomically(
        folder / f"{ask_id}{ASK}",
        {
            "id": ask_id,
            "says": says(request),
            "step": list(request.get("step", [])),
            "tool": request.get("tool", ""),
            "arguments": request.get("arguments", {}),
            "plan_hash": request["plan_hash"],
            "action_hash": request["action_hash"],
            "asked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    )
    return ask_id


def read_answer(path: Path) -> tuple[bool, str]:
    """(yes, by) from an answer file; anything but a clear yes is a no."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, ""
    if not isinstance(value, dict):
        return False, ""
    answer = str(value.get("answer", "")).strip().lower()
    by = str(value.get("by", "")).strip()
    return answer in ("y", "yes"), by


def wait_for_answer(
    folder: Path,
    request: dict[str, Any],
    *,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    tell: Callable[[str], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[bool, str]:
    """Ask in the folder and wait. (yes, by); a wait that runs out is a no.

    The ask and its answer are removed once read, or when the wait ends,
    so the folder only ever holds what is waiting now.
    """

    ask_id = put_ask(folder, request)
    ask, answer = folder / f"{ask_id}{ASK}", folder / f"{ask_id}{ANSWER}"
    if tell is not None:
        tell(
            f"Waiting for an answer to ask {ask_id} in {folder}: {says(request)}\n"
            f"  answer with: kosika answer {folder} {ask_id} yes"
        )
    ends = clock() + wait_seconds
    try:
        while clock() < ends:
            if answer.exists():
                return read_answer(answer)
            if not ask.exists():
                # Someone took the ask away: that is a no.
                return False, ""
            sleep(POLL_SECONDS)
        return False, ""
    finally:
        for path in (ask, answer):
            try:
                path.unlink()
            except OSError:
                pass


def waiting(folder: Path) -> list[dict[str, Any]]:
    """The asks waiting in a folder, oldest first; an answered one is not."""

    asks = []
    if not folder.is_dir():
        return asks
    for path in folder.glob(f"*{ASK}"):
        if path.with_name(path.name[: -len(ASK)] + ANSWER).exists():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict) and isinstance(value.get("id"), str):
            asks.append(value)
    return sorted(asks, key=lambda ask: (str(ask.get("asked_at", "")), ask["id"]))


def answer(folder: Path, ask_id: str | None, reply: str, by: str) -> str:
    """Answer one ask. With no id, the only ask waiting is answered."""

    reply = reply.strip().lower()
    if reply not in ("yes", "y", "no", "n"):
        raise AskFolderError("answer yes or no")
    asks = waiting(folder)
    if ask_id is None:
        if len(asks) != 1:
            raise AskFolderError(
                "nothing is waiting" if not asks else
                f"{len(asks)} asks are waiting; name one: "
                + ", ".join(ask["id"] for ask in asks)
            )
        ask_id = asks[0]["id"]
    found = next((ask for ask in asks if ask["id"] == ask_id), None)
    if found is None:
        raise AskFolderError(f"no ask {ask_id!r} is waiting in {folder}")
    yes = reply in ("yes", "y")
    _write_atomically(
        folder / f"{ask_id}{ANSWER}",
        {"answer": "yes" if yes else "no", "by": by},
    )
    return f"{'approved' if yes else 'refused'} {ask_id}: {found.get('says', '')}"


# A page for answering from a phone on the same network.
#
# `kosika answer FOLDER --serve` shows what is waiting, with a yes and a no
# button for each. The address carries a key made when the page starts, and
# nothing is shown or answered without it, so another device on the network
# that does not have the address can do nothing.

PAGE_STYLE = (
    ":root{--bg:#07080d;--panel:#111428;--ink:#eef0f7;--soft:#a3a8bd;--amber:#ff8a4c;--violet:#a993ff}"
    "*{box-sizing:border-box}body{margin:0;min-height:100vh;padding:24px 16px;color:var(--ink);"
    "font:16px/1.5 system-ui,-apple-system,'Segoe UI',sans-serif;background:radial-gradient(700px 400px at 0 -10%,#1b1f4a,transparent 60%),"
    "radial-gradient(600px 400px at 110% 0,#3a1d10,transparent 55%),var(--bg)}"
    "h1{margin:0 0 4px;font-size:28px;background:linear-gradient(90deg,var(--amber),#f5b45c 40%,var(--violet));"
    "-webkit-background-clip:text;background-clip:text;color:transparent}p.sub{margin:0 0 20px;color:var(--soft)}"
    ".ask{border-radius:18px;padding:16px;margin:0 0 14px;background:linear-gradient(160deg,#ff8a4c1c,var(--panel) 60%);"
    "border:1px solid #ff8a4c66;box-shadow:0 14px 40px -18px #ff8a4c99;animation:rise .5s both}"
    ".ask code{display:block;white-space:pre-wrap;word-break:break-word;font:13px ui-monospace,Menlo,monospace;color:var(--ink);"
    "background:#ffffff0d;border-radius:10px;padding:10px;margin:8px 0 12px}.ask small{color:var(--soft)}"
    "form{display:flex;gap:10px}button{flex:1;font:700 17px system-ui;padding:14px;border-radius:14px;border:0;color:#07080d}"
    "button.yes{background:linear-gradient(90deg,#4fe0a6,#55d6ff)}button.no{background:#ff6b81}"
    ".none{color:var(--soft);text-align:center;padding:40px 0}"
    "@keyframes rise{from{opacity:0;transform:translateY(12px)}}"
)


def page(folder: Path, key: str, said: str = "") -> str:
    from html import escape

    asks = waiting(folder)
    cards = "".join(
        "<section class='ask'>"
        f"<small>ask {escape(ask['id'])} · {escape(str(ask.get('asked_at', '')))}</small>"
        f"<code>{escape(str(ask.get('says', '')))}</code>"
        f"<form method='post' action='/answer?key={escape(key)}'>"
        f"<input type='hidden' name='id' value='{escape(ask['id'])}'>"
        "<button class='yes' name='reply' value='yes'>Yes, do it</button>"
        "<button class='no' name='reply' value='no'>No</button></form></section>"
        for ask in asks
    ) or "<p class='none'>Nothing is waiting.</p>"
    note = f"<p class='sub'>{escape(said)}</p>" if said else ""
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta http-equiv='refresh' content='4'>"
        f"<title>Kosika asks</title><style>{PAGE_STYLE}</style></head><body>"
        "<h1>Kosika asks</h1><p class='sub'>Your agents wait for your answer before each action.</p>"
        f"{note}{cards}</body></html>"
    )


def serve(folder: Path, by: str, port: int = 8795, host: str = "0.0.0.0") -> None:
    """Serve the answering page until stopped; prints the address to open."""

    import socket
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse

    key = secrets.token_urlsafe(12)

    class Handler(BaseHTTPRequestHandler):
        def _keyed(self) -> bool:
            given = parse_qs(urlparse(self.path).query).get("key", [""])[0]
            return secrets.compare_digest(given, key)

        def _send(self, status: int, body: str) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            if not self._keyed():
                self._send(403, "This page needs the address with its key.")
                return
            self._send(200, page(folder, key))

        def do_POST(self) -> None:  # noqa: N802
            if not self._keyed() or urlparse(self.path).path != "/answer":
                self._send(403, "This page needs the address with its key.")
                return
            length = min(int(self.headers.get("Content-Length") or 0), 4096)
            form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
            try:
                said = answer(folder, form.get("id", [""])[0], form.get("reply", [""])[0], by)
            except AskFolderError as exc:
                said = f"Not answered: {exc}"
            self.send_response(303)
            self.send_header("Location", f"/?key={key}")
            self.end_headers()
            print(said, flush=True)

        def log_message(self, *_: Any) -> None:
            pass

    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("10.255.255.255", 1))
        address = probe.getsockname()[0]
        probe.close()
    except OSError:
        address = "127.0.0.1"
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Open on your phone (same Wi-Fi): http://{address}:{port}/?key={key}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
