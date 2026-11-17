"""Studio's home screen: the facts of the day, and Kosika's say on showing them.

The facts are gathered here, from the files the agents already write:

* who is at work now: the overlay's heartbeats (~/.kosika/glow/NAME.json,
  fresh for six seconds), with each agent's pointer colour and mascot name
  from the overlay's kept looks (~/.kosika/pointers/overlay-scene-*.json);
* what waits for your Keep: coder runs that passed their checks and have not
  been kept (kosika-daily/DATE-N without kept.txt);
* the day's list: the requests in the coders' to-do lists;
* what the swarm did: today's runs and how each ended.

How they are shown (the greeting, which card comes first, what each card
says, the colours) is decided by app_assets/home.kos, run in-process.
"""

from __future__ import annotations

import datetime
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from . import coders

HOME_KOS = Path(__file__).parent / "app_assets" / "home.kos"
#: A heartbeat older than this means the agent has stopped (as overlay.kos says).
FRESH = 6.0


def overlay_home() -> Path:
    return Path(os.environ.get("KOSIKA_OVERLAY_HOME") or (Path.home() / ".kosika"))


def looks(home: Path | None = None) -> dict[str, dict[str, Any]]:
    """Each agent's colour and tag, as the overlay last drew them."""

    kept = sorted((home or overlay_home()).joinpath("pointers").glob("overlay-scene-*.json"),
                  key=lambda p: p.stat().st_mtime)
    for path in reversed(kept):
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("agents", {})
        except (OSError, ValueError):
            continue
    return {}


def working(now: float | None = None, home: Path | None = None) -> list[dict[str, Any]]:
    """Every agent at work now, by name."""

    place = (home or overlay_home()) / "glow"
    moment = time.time() if now is None else now
    seen = looks(home)
    out: list[dict[str, Any]] = []
    for path in sorted(place.glob("*.json")) if place.exists() else []:
        try:
            said = json.loads(path.read_text(encoding="utf-8"))
            at = float(said.get("at", path.stat().st_mtime))
        except (OSError, ValueError, TypeError):
            continue
        if moment - at > FRESH:
            continue
        name = str(said.get("name") or path.stem)
        look = seen.get(name, {})
        out.append({"name": name, "tag": str(look.get("tag") or name), "colour": str(look.get("colour") or ""),
                    "doing": str(said.get("doing") or ""), "typing": str(said.get("typing") or "")})
    return out


def todo_files(work: Path) -> list[Path]:
    """The coders' to-do lists: the main one and the swarm's Studio lists."""

    files = [coders.todo_file(work), *sorted(work.glob("studio-todo*.md"))]
    return [f for f in dict.fromkeys(files) if f.exists()]


def tasks(work: Path, most: int = 6) -> dict[str, Any]:
    items: list[dict[str, str]] = []
    for path in todo_files(work):
        for line in path.read_text(encoding="utf-8").splitlines():
            for mark, state in coders.MARKS.items():
                if line.startswith(mark):
                    text = line[len(mark):].split(" (20")[0].strip()
                    items.append({"state": state, "text": text, "list": path.name})
    live = [i for i in items if i["state"] in ("taken", "open")]
    live.sort(key=lambda i: 0 if i["state"] == "taken" else 1)
    return {"open": sum(1 for i in items if i["state"] == "open"),
            "taken": sum(1 for i in items if i["state"] == "taken"), "items": live[:most]}


def waiting(work: Path, most: int = 6) -> list[dict[str, Any]]:
    """Runs that passed and wait for a Keep, newest first."""

    out: list[dict[str, Any]] = []
    for folder in reversed(coders._runs(work)):
        if (folder / "kept.txt").exists() or not (folder / "report.md").exists():
            continue
        run = coders.run_of(folder, 0)
        if run["result"] == "passed" and run["patch"]:
            out.append({"run": run["run"], "request": run["request"]})
        if len(out) >= most:
            break
    return out


def finished_today(work: Path) -> dict[str, Any]:
    today = datetime.date.today().isoformat()
    runs = [coders.run_of(p, i) for i, p in enumerate(p for p in coders._runs(work) if p.name.startswith(today + "-"))]
    ended = [r for r in runs if r["result"]]
    return {"finished": len(ended), "passed": sum(1 for r in ended if r["result"] == "passed"),
            "recent": [{"run": r["run"], "request": r["request"], "result": r["result"]} for r in ended[-5:]][::-1]}


def person() -> str:
    name = os.environ.get("KOSIKA_PERSON") or os.environ.get("USERNAME") or ""
    return name[:1].upper() + name[1:]


def shown(facts: dict[str, Any]) -> dict[str, Any]:
    """home.kos's say on the facts."""

    from .modules import load_program
    from .runtime import run

    with tempfile.TemporaryDirectory(prefix="kosika-home-") as scratch:
        made = run(load_program(HOME_KOS), "home", Path(scratch) / "run.jsonl", inputs={"h": {
            "hour": facts["hour"], "person": facts["person"],
            "working": [{"name": w["name"], "colour": w["colour"], "doing": w["doing"]} for w in facts["working"]],
            "waiting": facts.get("waiting_total", len(facts["waiting"])), "open": facts["tasks"]["open"], "taken": facts["tasks"]["taken"],
            "finished": facts["done"]["finished"], "passed": facts["done"]["passed"]}})
    return made.get("shown", made) if isinstance(made, dict) else made


def home(work: Path | None = None) -> dict[str, Any]:
    work = work or coders.work_folder()
    facts = {"hour": datetime.datetime.now().hour, "person": person(), "working": working(),
             "waiting": waiting(work), "waiting_total": len(waiting(work, most=10_000)),
             "tasks": tasks(work), "done": finished_today(work)}
    try:
        facts["look"] = shown(facts)
        facts["ok"] = True
    except (ValueError, RuntimeError, OSError) as exc:  # the facts still show
        facts["look"] = {"greeting": "Kosika Studio", "line": "", "cards": [], "glow": [],
                         "tabs": ["Today", "Chat", "Designs", "Coders"]}
        facts["ok"] = False
        facts["error"] = f"home.kos: {exc}"
    return facts
