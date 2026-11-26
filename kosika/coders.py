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


def _agent_of(folder: Path) -> str:
    """The coder named in the run's agent.txt, when the swarm wrote one."""
    try:
        name = (folder / "agent.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return name if name in SWARM_NAMES or name in ("Kosika", "scout") else ""


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
        # The coder that did it, as the swarm wrote it down; a run from before
        # that is named by its place in the day, as it always was.
        "agent": _agent_of(folder) or SWARM_NAMES[slot % len(SWARM_NAMES)],
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
        "waiting": len(waiting(work)),
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


def run_detail(name: str, work: Path | None = None) -> dict[str, Any]:
    """One run in full: what it was asked, its report and its change."""
    work = work or work_folder()
    if not RUN_NAME.match(name):
        raise ValueError("No such run")
    folder = work / name
    if not folder.is_dir():
        raise FileNotFoundError(f"No run {name} in {work}")

    def text(file: str) -> str:
        path = folder / file
        return (
            path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        )

    report = text("report.md")
    result, why = _ended(report) if report else ("", "")
    kept = text("kept.txt").strip()
    return {
        "run": name,
        "request": text("request.txt").strip(),
        "report": report,
        "patch": text("change.patch"),
        "result": result,
        "why": why,
        "kept": kept,
    }


def _git(args: list[str], cwd: Path) -> str:
    done = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if done.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args[:2])} failed: {(done.stderr or done.stdout).strip()[-600:]}"
        )
    return done.stdout


#: Where Kosika is on GitHub, for a Studio that is not run from a checkout
#: (the installed app runs from its own folder, which is not a git repository).
KOSIKA_REPO_URL = "https://github.com/blackhole8080-darkmatter/kosika"


def origin_of(home: Path) -> str:
    """Where a kept change is pushed: the checkout's own origin, or, when
    Studio runs from a folder that is not a git checkout, KOSIKA_REPO_URL
    (or Kosika's repository on GitHub)."""
    try:
        return _git(["remote", "get-url", "origin"], home).strip()
    except (RuntimeError, OSError):
        return os.environ.get("KOSIKA_REPO_URL", "").strip() or KOSIKA_REPO_URL


def keep(
    name: str, work: Path | None = None, repo: Path | None = None
) -> dict[str, Any]:
    """Keep a run that passed: its change goes on a branch of its own,
    coder/NAME, made from studio in a fresh copy (the checkout you work in is
    never touched), is pushed, and a pull request into studio is opened for
    you to review. Nothing is merged."""
    from .agents_home import KOSIKA_HOME

    work = work or work_folder()
    detail = run_detail(name, work)
    if detail["result"] != "passed":
        raise ValueError("Only a run that passed its checks can be kept")
    if detail["kept"]:
        return {"run": name, "url": detail["kept"]}
    if not detail["patch"].strip():
        raise ValueError("This run left no change to keep")
    origin = origin_of(repo or KOSIKA_HOME)
    place = work / "branches" / name
    if place.exists():
        import shutil

        shutil.rmtree(place, ignore_errors=True)
    place.parent.mkdir(parents=True, exist_ok=True)
    _git(
        ["clone", "--quiet", "--depth", "1", "--branch", "studio", origin, str(place)],
        work,
    )
    branch = "coder/" + name
    _git(["checkout", "--quiet", "-b", branch], place)
    _git(["apply", "--whitespace=nowarn", str(work / name / "change.patch")], place)
    _git(["add", "-A"], place)
    request = detail["request"]
    title = (request[:1].upper() + request[1:])[:90]
    _git(
        [
            "commit",
            "--quiet",
            "-m",
            f"{title}\n\nWritten by the Kosika coder swarm ({name}) and kept from Studio: it passed its\nexample tests, go vet, the comparison of both engines, and develop.kos.",
        ],
        place,
    )
    _git(["push", "--quiet", "-u", "origin", branch], place)
    body = (
        f"Written by the Kosika coder swarm, run `{name}`, and kept from Studio's Coders page.\n\n"
        f"**Request:** {request}\n\nIt passed its example tests, go vet, the comparison of both engines and develop.kos."
    )
    made = subprocess.run(
        [
            "gh",
            "pr",
            "create",
            "--base",
            "studio",
            "--head",
            branch,
            "--title",
            title,
            "--body",
            body,
        ],
        cwd=str(place),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if made.returncode != 0:
        raise RuntimeError(
            "The branch was pushed, but the pull request was not made: "
            + (made.stderr or made.stdout).strip()[-400:]
        )
    url = made.stdout.strip().splitlines()[-1]
    (work / name / "kept.txt").write_text(url + "\n", encoding="utf-8")
    return {"run": name, "url": url}


def waiting(work: Path | None = None) -> list[dict[str, Any]]:
    """Every run, on any day, that passed its checks, left a change, and was
    not kept yet: what waits for a Keep."""
    work = work or work_folder()
    found = []
    for i, folder in enumerate(_runs(work)):
        if (folder / "kept.txt").exists() or not (folder / "change.patch").exists():
            continue
        report = folder / "report.md"
        if not report.exists():
            continue
        result, _ = _ended(report.read_text(encoding="utf-8"))
        if result != "passed":
            continue
        if not (folder / "change.patch").read_text(encoding="utf-8").strip():
            continue
        found.append(run_of(folder, i))
    return found


def keep_all(
    names: list[str] | None = None,
    work: Path | None = None,
    repo: Path | None = None,
) -> dict[str, Any]:
    """Keep many runs that passed at once: one branch, one commit per run,
    pushed, and ONE pull request into studio for you to review. A change
    that no longer applies on top of the others is left waiting and said.
    Nothing is merged."""
    import shutil
    import time as clock

    from .agents_home import KOSIKA_HOME

    work = work or work_folder()
    ready = waiting(work)
    if names is not None:
        wanted = set(names)
        ready = [run for run in ready if run["run"] in wanted]
    if not ready:
        raise ValueError("No run that passed is waiting to be kept")
    origin = origin_of(repo or KOSIKA_HOME)
    batch = "batch-" + clock.strftime("%Y%m%d-%H%M%S")
    place = work / "branches" / batch
    shutil.rmtree(place, ignore_errors=True)
    place.parent.mkdir(parents=True, exist_ok=True)
    _git(
        ["clone", "--quiet", "--depth", "1", "--branch", "studio", origin, str(place)],
        work,
    )
    branch = "coder/" + batch
    _git(["checkout", "--quiet", "-b", branch], place)
    kept: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for run in ready:
        patch = work / run["run"] / "change.patch"
        tried = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", str(patch)],
            cwd=str(place),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if tried.returncode != 0:
            skipped.append(
                {"run": run["run"], "why": (tried.stderr or "did not apply").strip()[-200:]}
            )
            continue
        _git(["add", "-A"], place)
        title = (run["request"][:1].upper() + run["request"][1:])[:90] or run["run"]
        _git(
            [
                "commit",
                "--quiet",
                "-m",
                f"{title}\n\nWritten by the Kosika coder swarm ({run['run']}) and kept from Studio: it passed its\nexample tests, go vet, the comparison of both engines, and develop.kos.",
            ],
            place,
        )
        kept.append(run)
    if not kept:
        raise ValueError(
            "None of the waiting changes applies to studio any more: "
            + "; ".join(item["run"] + ": " + item["why"] for item in skipped[:3])
        )
    _git(["push", "--quiet", "-u", "origin", branch], place)
    lines = "\n".join(f"- `{run['run']}`: {run['request']}" for run in kept)
    left = (
        "\n\n**Left waiting** (no longer applies on top of the others):\n"
        + "\n".join(f"- `{item['run']}`" for item in skipped)
        if skipped
        else ""
    )
    title = f"Coder swarm: {len(kept)} kept changes"
    body = (
        f"Written by the Kosika coder swarm and kept together from Studio's Coders page.\n\n"
        f"Each passed its example tests, go vet, the comparison of both engines and develop.kos.\n"
        f"One commit per run:\n\n{lines}{left}"
    )
    made = subprocess.run(
        ["gh", "pr", "create", "--base", "studio", "--head", branch, "--title", title, "--body", body],
        cwd=str(place),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if made.returncode != 0:
        raise RuntimeError(
            "The branch was pushed, but the pull request was not made: "
            + (made.stderr or made.stdout).strip()[-400:]
        )
    url = made.stdout.strip().splitlines()[-1]
    for run in kept:
        (work / run["run"] / "kept.txt").write_text(url + "\n", encoding="utf-8")
    return {"url": url, "kept": [run["run"] for run in kept], "skipped": skipped}
