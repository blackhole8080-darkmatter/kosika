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
