"""The coder swarm, as Studio shows it: who is working on what, how each run
ended, the to-do list the coders take requests from, and starting them.

The swarm itself lives in kosika-agents (agents/coder/daily/daily.py). Each
coder works in a folder of its own under the work folder, one per request,
named DATE-N, and leaves there:

    request.txt   what it was asked
    report.md     how it ended: its third line says "passed its checks"
                  or "did not pass"
    change.patch  the change, to apply to Kosika when it is kept

The to-do list is a Markdown file of requests, one a line: "- [ ] ..." is
open, "- [~] ..." taken, "- [x] ..." passed and "- [!] ..." did not pass.

The work folder is KOSIKA_CODER_WORK, or kosika-daily in the home folder's
Aryan_Private or the home folder itself; the to-do list is KOSIKA_CODER_TODO,
or queue.md in the work folder.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .agents_home import agents_home

#: How many coders a swarm has, each with its name and mascot in
#: kosika-agents (agents/hands/mascots.kos): runs are shown as coder-1 to
#: coder-4 in turn, in the order they started.
SWARM_NAMES = ("coder-1", "coder-2", "coder-3", "coder-4")
RUN_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})-(\d+)$")
MARKS = {"- [ ] ": "open", "- [~] ": "taken", "- [x] ": "passed", "- [!] ": "failed"}


def work_folder() -> Path:
    told = os.environ.get("KOSIKA_CODER_WORK")
    if told:
        return Path(told).expanduser()
    for place in (
        Path.home() / "Aryan_Private" / "kosika-daily",
        Path.home() / "kosika-daily",
    ):
        if place.is_dir():
            return place
    return Path.home() / "kosika-daily"


def todo_file(work: Path | None = None) -> Path:
    told = os.environ.get("KOSIKA_CODER_TODO")
    return Path(told).expanduser() if told else (work or work_folder()) / "queue.md"


def _runs(work: Path) -> list[Path]:
    found = (
        [p for p in work.iterdir() if p.is_dir() and RUN_NAME.match(p.name)]
        if work.is_dir()
        else []
    )
    return sorted(
        found,
        key=lambda p: (
            RUN_NAME.match(p.name).group(1),
            int(RUN_NAME.match(p.name).group(2)),
        ),
    )


def _ended(report: str) -> tuple[str, str]:
    """How a run ended, and why, from its report."""
    lines = report.splitlines()
    third = lines[2] if len(lines) > 2 else ""
    result = "passed" if "passed its checks" in third else "did not pass"
    why = next(
        (
            line[len("Why it did not pass: ") :]
            for line in lines
            if line.startswith("Why it did not pass: ")
        ),
        "",
    )
    return result, why


def run_of(folder: Path, slot: int) -> dict[str, Any]:
    request_file, report_file = folder / "request.txt", folder / "report.md"
    request = (
        request_file.read_text(encoding="utf-8").strip()
        if request_file.exists()
        else ""
    )
    began = folder.stat().st_ctime
    result, why, ended = "", "", time.time()
    if report_file.exists():
        result, why = _ended(report_file.read_text(encoding="utf-8"))
        ended = report_file.stat().st_mtime
    return {
        "run": folder.name,
        "agent": SWARM_NAMES[slot % len(SWARM_NAMES)],
        "request": request,
        "seconds": max(0, int(ended - began)),
        "result": result,
        "why": why,
        "patch": (folder / "change.patch").exists(),
    }


def status(work: Path | None = None, most: int = 12) -> dict[str, Any]:
    """The latest day's runs, newest last, at most MOST of them, and the
    to-do list."""
    work = work or work_folder()
    runs = _runs(work)
    day = RUN_NAME.match(runs[-1].name).group(1) if runs else ""
    todays = [p for p in runs if p.name.startswith(day + "-")] if day else []
    shown = [run_of(p, i) for i, p in enumerate(todays)][-most:]
    return {
        "work": str(work),
        "day": day,
        "runs": shown,
        "working": sum(1 for r in shown if not r["result"]),
        "passed": sum(1 for r in shown if r["result"] == "passed"),
        "todo": todo(work),
        "can_start": daily_script().exists(),
    }


def todo(work: Path | None = None) -> dict[str, Any]:
    path = todo_file(work)
    items = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            for mark, state in MARKS.items():
                if line.startswith(mark):
                    items.append({"state": state, "text": line[len(mark) :].strip()})
    return {"file": str(path), "items": items}


def add_request(text: str, work: Path | None = None) -> dict[str, Any]:
    """Add one request to the to-do list, open, at its end."""
    request = " ".join(text.split())
    if len(request) < 8:
        raise ValueError(
            "Write the request in a sentence, such as: add a Text method shout() that gives the text in capitals, giving a Text"
        )
    if len(request) > 2000:
        raise ValueError("A request is at most 2,000 characters")
    path = todo_file(work)
    path.parent.mkdir(parents=True, exist_ok=True)
    before = path.read_text(encoding="utf-8") if path.exists() else ""
    if any(
        item["text"] == request and item["state"] in ("open", "taken")
        for item in todo(work)["items"]
    ):
        raise ValueError("That request is already waiting in the list")
    path.write_text(
        before
        + ("" if before.endswith("\n") or not before else "\n")
        + "- [ ] "
        + request
        + "\n",
        encoding="utf-8",
    )
    return todo(work)


def daily_script() -> Path:
    return agents_home() / "agents" / "coder" / "daily" / "daily.py"


def start(size: int, work: Path | None = None) -> dict[str, Any]:
    """Start a swarm of SIZE coders on the open requests, in the background.
    Each takes one request; the page shows them as they work."""
    if not 1 <= size <= len(SWARM_NAMES):
        raise ValueError(f"A swarm has 1 to {len(SWARM_NAMES)} coders")
    script = daily_script()
    if not script.exists():
        raise FileNotFoundError(f"The coder swarm is not here: {script}")
    waiting = [item for item in todo(work)["items"] if item["state"] == "open"]
    if not waiting:
        raise ValueError("Nothing is waiting in the to-do list: add a request first")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [
            sys.executable,
            str(script),
            "--todo",
            str(todo_file(work)),
            "--swarm",
            str(size),
        ],
        cwd=str(agents_home()),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    return {"started": min(size, len(waiting)), "waiting": len(waiting)}


_pictures: dict[tuple[str, str], dict[str, str]] = {}


def mascot(agent: str, state: str) -> dict[str, str]:
    """A coder's mascot, as SVG with its motion inside, and its name ("Shilpi
    the fox"), drawn by kosika-agents' mascots.kos. Kept once drawn."""
    if agent not in SWARM_NAMES and agent not in ("Kosika", "scout"):
        raise ValueError("No such coder")
    if state not in ("idle", "waiting", "typing", "done", "failed"):
        state = "idle"
    key = (agent, state)
    if key not in _pictures:
        program = agents_home() / "agents" / "hands" / "mascots.kos"
        if not program.exists():
            return {"svg": "", "title": agent}
        from .checker import check
        from .modules import load_program
        from .runtime import run

        try:
            loaded = load_program(program)
            check(loaded)
            drawn = run(
                loaded,
                "picture",
                None,
                inputs={"p": {"agent": agent, "state": state, "size": 72}},
            )
        except Exception:  # noqa: BLE001 - a coder with no picture is shown by name
            return {"svg": "", "title": agent}
        _pictures[key] = {
            "svg": str(drawn.get("svg", "")),
            "title": str(drawn.get("title", agent)),
        }
    return _pictures[key]
