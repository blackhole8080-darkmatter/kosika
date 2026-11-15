"""Watch a swarm of Kosika coders on a live board drawn by Kosika.

    python swarm_watch.py                 # today's coders; opens the board in the browser
    python swarm_watch.py --once          # draw it once and stop

Every few seconds it gathers what each coder of today's runs is doing (its
request from todo.md, how long it has worked, how it ended, from its run
folder in kosika-daily) and how many answers the model router has given
since the first of them began, and asks Kosika to draw the board
(examples/agents/hands/board.kos), each coder in its own pointer's colour.
The page reloads itself, so it stays live; it stops when every coder has
finished.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
BOARD = REPO / "examples" / "agents" / "hands" / "board.kos"
DAILY = Path.home() / "Aryan_Private" / "kosika-daily"
PAGE = DAILY / "board.html"
NATIVE = Path.home() / ".kosika" / "bin" / ("kosika-native.exe" if os.name == "nt" else "kosika-native")
ROUTER_LOGS = [Path.home() / ".kosika" / "virtual-router.jsonl"]


def todays_runs() -> list[Path]:
    today = datetime.date.today().isoformat()
    return sorted((p for p in DAILY.glob(f"{today}-*") if p.is_dir()), key=lambda p: int(p.name.rsplit("-", 1)[1]))


def request_of(run: Path) -> str:
    report = run / "report.md"
    if report.exists():
        return report.read_text(encoding="utf-8").split("\n", 1)[0].lstrip("# ").strip()
    saved = run / "request.txt"
    return saved.read_text(encoding="utf-8").strip() if saved.exists() else "(working on a request from todo.md)"


def answers_since(started: float, logs: list[Path]) -> tuple[int, str]:
    count, last = 0, ""
    since = datetime.datetime.fromtimestamp(started).strftime("%Y-%m-%dT%H:%M:%S")
    for log in logs:
        if not log.exists():
            continue
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines()[-2000:]:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("at", "") >= since and row.get("ok"):
                count += 1
                last = row.get("why", "")
    return count, last


def state(logs: list[Path]) -> dict:
    runs = todays_runs()
    coders = []
    for at, run in enumerate(runs, 1):
        report = run / "report.md"
        began = run.stat().st_ctime
        result = ""
        ended = time.time()
        if report.exists():
            third = report.read_text(encoding="utf-8").split("\n")[2] if report.read_text(encoding="utf-8").count("\n") >= 2 else ""
            result = "passed" if "passed its checks" in third else "did not pass"
            ended = report.stat().st_mtime
        coders.append({"name": f"coder-{at}", "request": request_of(run), "seconds": int(ended - began),
                       "calls": 0, "doing": "", "result": result})
    if runs:
        count, last = answers_since(min(r.stat().st_ctime for r in runs), logs)
        working = [c for c in coders if not c["result"]] or coders
        share = count // max(1, len(working))
        for c in working:
            c["calls"] = share
            c["doing"] = ("last model answer: " + last) if last and not c["result"] else ""
    return {"title": f"Kosika coders, {datetime.date.today().isoformat()}", "coders": coders}


def draw(swarm: dict) -> None:
    with tempfile.TemporaryDirectory() as work:
        given = Path(work) / "state.json"
        given.write_text(json.dumps({"s": swarm}), encoding="utf-8")
        engine = [str(NATIVE)] if NATIVE.exists() else [sys.executable, "-m", "kosika.cli"]
        ran = subprocess.run(engine + ["run", str(BOARD), "--input", str(given)], cwd=BOARD.parent, capture_output=True,
                             text=True, encoding="utf-8", env={**os.environ, "PYTHONPATH": str(REPO)}, timeout=120)
    first = ran.stdout.split("\n", 1)[0]
    try:
        import ast

        html = ast.literal_eval(first)["html"]
    except (ValueError, SyntaxError, KeyError):
        raise SystemExit("Kosika could not draw the board:\n" + ran.stdout[-500:] + ran.stderr[-500:])
    PAGE.write_text(html, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--log", action="append", help="a router log to count answers from (more than one may be given)")
    args = parser.parse_args()
    logs = [Path(p) for p in args.log] if args.log else ROUTER_LOGS
    draw(state(logs))
    if args.once:
        print(PAGE)
        return
    os.startfile(PAGE) if os.name == "nt" else print(PAGE)
    while True:
        time.sleep(5)
        swarm = state(logs)
        draw(swarm)
        if swarm["coders"] and all(c["result"] for c in swarm["coders"]):
            break


if __name__ == "__main__":
    main()
