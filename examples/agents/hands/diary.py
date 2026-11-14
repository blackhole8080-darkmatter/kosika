"""The hands' diary: read the runs' traces, and let diary.kos draw them.

    python diary.py TRACE.jsonl [TRACE.jsonl | FOLDER ...] [--out diary.html]

This only reads what the runs recorded: for each command given to hands.py
or browser.py, when it ran, in which job and step, the command, its exit
code, what it said and who approved it. diary.kos decides the rest.
Run jobs with `--trace traces/<name>.jsonl` to keep every run's diary.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
PROGRAMS = ("hands.py", "browser.py")
RUNNER = (
    "import json, sys\n"
    "from kosika.modules import load_program\n"
    "from kosika.runtime import run\n"
    "ask = json.loads(open(sys.argv[3], encoding='utf-8').read())\n"
    "print(json.dumps(run(load_program(sys.argv[1]), sys.argv[2], None, inputs=ask)))\n"
)


def local_time(stamp: str) -> str:
    try:
        return datetime.fromisoformat(stamp).astimezone().strftime("%H:%M:%S")
    except ValueError:
        return stamp


def entries(trace: Path, first_run: int = 0) -> list[dict]:
    """The hands' actions in one trace, in the order they were done."""
    found, job, step, waiting, run = [], "", "", None, first_run
    for line in trace.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get("event")
        if kind == "run_start":
            job, run = str(event.get("workflow", "")), run + 1
        elif kind == "step_start":
            step = str(event.get("step", ""))
        elif kind == "tool_call":
            args = (event.get("arguments") or {}).get("args")
            if isinstance(args, list) and args and str(args[0]) in PROGRAMS:
                approval = event.get("approval") or {}
                waiting = {"run": run, "at": local_time(str(event.get("timestamp", ""))), "job": job, "step": step,
                           "args": [str(a) for a in args], "exit_code": 0, "said": "",
                           "approved_by": str(approval.get("approved_by", ""))}
        elif kind == "tool_return" and waiting is not None:
            result = event.get("result") or {}
            waiting["exit_code"] = result.get("exit_code", 0)
            waiting["said"] = str(result.get("stdout", "")).strip() or str(result.get("stderr", "")).strip()
            found.append(waiting)
            waiting = None
    return found


def traces(paths: list[Path]) -> list[Path]:
    out = []
    for path in paths:
        out.extend(sorted(path.glob("*.jsonl"), key=lambda p: p.stat().st_mtime) if path.is_dir() else [path])
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, default=Path("diary.html"))
    parser.add_argument("--title", default="Hands diary")
    parser.add_argument("--lesson", type=int, metavar="RUN",
                        help="Write lesson.json from this run of the diary, for learn.kos to learn a skill from.")
    parser.add_argument("--name", default="", help="The learned skill's name, with --lesson.")
    parser.add_argument("--when", default="", help="When the learned skill fits, words split by commas, with --lesson.")
    args = parser.parse_args()
    actions: list[dict] = []
    for trace in traces(args.paths):
        actions += entries(trace, actions[-1]["run"] if actions else 0)
    if args.lesson is not None:
        lesson = {"l": {"name": args.name, "when": [w.strip() for w in args.when.split(",") if w.strip()],
                        "entries": [a for a in actions if a["run"] == args.lesson]}}
        Path("lesson.json").write_text(json.dumps(lesson, indent=2), encoding="utf-8")
        print(f"lesson.json holds run {args.lesson} ({len(lesson['l']['entries'])} actions). To learn from it:")
        print("kosika run learn.kos --workflow learn --input lesson.json --allow-shell --allow-folder . --approve")
        return 0
    with tempfile.TemporaryDirectory() as work:
        ask = Path(work) / "ask.json"
        ask.write_text(json.dumps({"d": {"title": args.title, "entries": actions}}), encoding="utf-8")
        ran = subprocess.run([sys.executable, "-c", RUNNER, str(HERE / "diary.kos"), "main", str(ask)], cwd=work,
                             capture_output=True, text=True, encoding="utf-8",
                             env={**os.environ, "PYTHONPATH": str(REPO)}, timeout=300)
    if ran.returncode != 0:
        print(ran.stderr.strip(), file=sys.stderr)
        return 2
    args.out.write_text(json.loads(ran.stdout.strip().splitlines()[-1])["html"], encoding="utf-8")
    print(f"{len(actions)} actions written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
