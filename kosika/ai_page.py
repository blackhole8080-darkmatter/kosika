"""Studio's Kosika AI page: ask anything, and see the plan, each step and its
check, the pictures and the reply; then Keep or Drop the run.

Kosika AI is agents/ai/main.kos in kosika-agents. A run can take minutes on
free models, so it runs in the background: POST /api/ai starts one and
gives its id, GET /api/ai lists the runs, newest first, with each one's
state ("running", "done" or "failed") and, when done, the report the agent
printed. POST /api/ai/mark keeps or drops a run (agents/ai/mark.kos), which
is what Kosika AI learns from.

The models are reached through Kosika's own router (kosika-agents/models,
port 8791): free cloud models first, then the ones on this computer. When
nothing answers on that port, the router is started from kosika-agents, as
`python models/learning_router.py`, and left running.

Runs are kept in ~/.kosika/ai/runs (one JSON file each); what Kosika AI has
learned is in ~/.kosika/ai/lessons (its memory folder).

Kosika AI runs from its own copy of kosika-agents' main branch, kept in
~/.kosika/ai/agents and fetched again when it is more than a few hours old,
so it always has the merged agent while the kosika-agents folder beside
Studio stays on whatever branch its owner (such as the coder swarm) keeps it
on. KOSIKA_AI_AGENTS_HOME points at another copy instead, and is never
fetched over. When no copy can be fetched, the folder beside Studio is used
if it has the agent.
"""

from __future__ import annotations

import ast
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Any

from .agents_home import FETCH, agents_home

ROUTER_PORT = int(os.environ.get("KOSIKA_ROUTER_PORT", "8791"))
MODEL = os.environ.get("KOSIKA_AI_MODEL", "kosika-virtual:soft")
HOME = Path(os.environ.get("KOSIKA_AI_HOME", str(Path.home() / ".kosika" / "ai")))
MOST_RUNS = 50
#: kosika-agents' main branch as one zip, the same way Studio updates itself.
AGENTS_ZIP = "https://codeload.github.com/blackhole8080-darkmatter/kosika-agents/zip/refs/heads/main"
#: How old the copy may get before it is fetched again.
AGENTS_FRESH_SECONDS = 6 * 3600
_lock = threading.Lock()
# One run at a time: runs share the lessons file, and free models their allowance.
_one = threading.Semaphore(1)


class AIError(Exception):
    """Kosika AI could not start, in words a person can act on."""


def runs_folder() -> Path:
    folder = HOME / "runs"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def lessons_folder() -> Path:
    folder = HOME / "lessons"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def own_agents() -> Path:
    """Where Kosika AI keeps its own copy of kosika-agents."""
    told = os.environ.get("KOSIKA_AI_AGENTS_HOME")
    return Path(told).expanduser().resolve() if told else HOME / "agents"


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as answer:
        return answer.read()


def fetch_agents(force: bool = False) -> str:
    """Bring Kosika AI's copy of kosika-agents main up to date; "" when it is.

    A copy named by KOSIKA_AI_AGENTS_HOME is someone's own and is left alone.
    The new copy is unpacked beside the old one and only then put in its
    place, so a failed download never leaves Kosika AI without an agent.
    """
    if os.environ.get("KOSIKA_AI_AGENTS_HOME"):
        return ""
    home = own_agents()
    stamp = home / ".fetched"
    have = (home / "agents" / "ai" / "main.kos").is_file()
    if have and not force and stamp.is_file() and time.time() - stamp.stat().st_mtime < AGENTS_FRESH_SECONDS:
        return ""
    try:
        packed = zipfile.ZipFile(io.BytesIO(_download(AGENTS_ZIP)))
        fresh = home.with_name(home.name + ".new")
        shutil.rmtree(fresh, ignore_errors=True)
        packed.extractall(fresh)
        tops = [path for path in fresh.iterdir() if path.is_dir()]
        if len(tops) != 1 or not (tops[0] / "agents" / "ai" / "main.kos").is_file():
            shutil.rmtree(fresh, ignore_errors=True)
            return "The kosika-agents download has no agents/ai/main.kos."
        old = home.with_name(home.name + ".old")
        shutil.rmtree(old, ignore_errors=True)
        if home.exists():
            home.rename(old)
        tops[0].rename(home)
        shutil.rmtree(fresh, ignore_errors=True)
        shutil.rmtree(old, ignore_errors=True)
        (home / ".fetched").write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
        return ""
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        return f"Kosika AI could not fetch kosika-agents main: {exc}"


def ai_agents() -> Path:
    """The kosika-agents folder Kosika AI runs from: its own copy, else the one beside Studio."""
    mine = own_agents()
    if (mine / "agents" / "ai" / "main.kos").is_file():
        return mine
    return agents_home()


def ai_program(name: str = "main") -> Path:
    return ai_agents() / "agents" / "ai" / f"{name}.kos"


def _engine() -> list[str]:
    from .designs import _engine as engine

    return engine()


def router_up(port: int = ROUTER_PORT) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def start_router() -> str:
    """Start Kosika's model router when nothing answers on its port; "" when it is up."""
    if router_up():
        return ""
    script = ai_agents() / "models" / "learning_router.py"
    if not script.is_file():
        return f"Kosika's model router is not at {script}. {FETCH}"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen([sys.executable, str(script)], cwd=str(script.parent.parent), creationflags=flags,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        if router_up():
            return ""
        time.sleep(0.25)
    return "Kosika's model router did not start on port %d." % ROUTER_PORT


def report_of(printed: str) -> dict[str, Any] | None:
    """The report the engine printed: JSON or the Python-shaped record."""
    text = "\n".join(line for line in printed.splitlines() if not line.startswith("Trace: ")).strip()
    start = text.find("{")
    if start < 0:
        return None
    for read in (json.loads, ast.literal_eval):
        try:
            value = read(text[start:])
            if isinstance(value, dict):
                return value
        except (ValueError, SyntaxError):
            pass
    return None


def _save(run: dict[str, Any]) -> None:
    path = runs_folder() / (run["id"] + ".json")
    path.write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8")


def _work(run: dict[str, Any]) -> None:
    with _one:
        run["started"] = time.time()
        _run(run)


def _run(run: dict[str, Any]) -> None:
    program = ai_program()
    with tempfile.TemporaryDirectory() as work:
        given = Path(work) / "ask.json"
        given.write_text(json.dumps({"ask": {"text": run["ask"], "most": 3}}), encoding="utf-8")
        command = _engine() + ["run", str(program), "--input", str(given),
                               "--memory", str(lessons_folder()), "--allow-site", "en.wikipedia.org",
                               "--model", MODEL, "--model-url", f"http://127.0.0.1:{ROUTER_PORT}/v1",
                               "--model-timeout", "120", "--max-output-tokens", "2000",
                               # Reading a formula or drawing a curve is many small steps in Kosika.
                               "--max-function-calls", "400000"]
        try:
            ran = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                 timeout=1800, cwd=work, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            report = report_of(ran.stdout)
            if report is None:
                said = (ran.stderr or ran.stdout).strip().splitlines()
                run.update(state="failed", error=(said[-1] if said else "Kosika AI printed no report.")[:600])
            else:
                run.update(state="done", report=report)
        except subprocess.TimeoutExpired:
            run.update(state="failed", error="Kosika AI took longer than 30 minutes and was stopped.")
        except OSError as exc:
            run.update(state="failed", error=f"The Kosika engine could not start: {exc}")
    run["seconds"] = round(time.time() - run["started"])
    with _lock:
        _save(run)


def ask(text: str) -> dict[str, Any]:
    """Start Kosika AI on one ask, in the background; gives the new run."""
    text = " ".join(str(text or "").split())
    if not text:
        raise AIError("Say what Kosika AI should work on.")
    if len(text) > 2000:
        raise AIError("Keep the ask under 2,000 characters.")
    fetched = fetch_agents()
    if not ai_program().is_file():
        raise AIError(fetched or f"Kosika AI is not at {ai_program()}. {FETCH}")
    problem = start_router()
    if problem:
        raise AIError(problem)
    run = {"id": time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6], "ask": text, "state": "running",
           "started": time.time(), "verdict": ""}
    with _lock:
        _save(run)
    threading.Thread(target=_work, args=(run,), daemon=True).start()
    return run


def runs() -> dict[str, Any]:
    """The runs kept on this computer, newest first."""
    found = []
    for path in sorted(runs_folder().glob("*.json"), reverse=True)[:MOST_RUNS]:
        try:
            found.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    for run in found:
        # A run still marked running after an hour was cut off (Studio closed).
        if run.get("state") == "running" and time.time() - run.get("started", 0) > 3600:
            run.update(state="failed", error="This run was cut off: Studio was closed while it worked.")
    return {"runs": found, "router": router_up(), "model": MODEL, "agents": str(ai_agents())}


def mark(body: dict[str, Any]) -> dict[str, Any]:
    """Keep or Drop a run: Kosika AI's lessons hold the verdict (agents/ai/mark.kos)."""
    run_id, verdict = str(body.get("id", "")), str(body.get("verdict", ""))
    if verdict not in ("keep", "drop"):
        raise AIError("A run is kept or dropped.")
    path = runs_folder() / (Path(run_id).name + ".json")
    if not path.is_file():
        raise AIError("There is no such run.")
    run = json.loads(path.read_text(encoding="utf-8"))
    program = ai_program("mark")
    with tempfile.TemporaryDirectory() as work:
        given = Path(work) / "mark.json"
        given.write_text(json.dumps({"m": {"ask": run["ask"], "verdict": verdict}}), encoding="utf-8")
        ran = subprocess.run(_engine() + ["run", str(program), "--input", str(given), "--memory", str(lessons_folder())],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, cwd=work,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    said = report_of(ran.stdout) or {}
    if not said.get("ok"):
        raise AIError(str(said.get("said") or (ran.stderr.strip().splitlines() or ["Kosika AI could not mark the run."])[-1]))
    run["verdict"] = verdict
    with _lock:
        _save(run)
    return run
