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


def draw(program: str, workflow: str, ask: dict) -> str:
    """The page a workflow of this folder draws for one input."""
    with tempfile.TemporaryDirectory() as work:
        asked = Path(work) / "ask.json"
        asked.write_text(json.dumps(ask), encoding="utf-8")
        ran = subprocess.run([sys.executable, "-c", RUNNER, str(HERE / program), workflow, str(asked)], cwd=work,
                             capture_output=True, text=True, encoding="utf-8",
                             env={**os.environ, "PYTHONPATH": str(REPO)}, timeout=300)
    if ran.returncode != 0:
        raise RuntimeError(ran.stderr.strip())
    return json.loads(ran.stdout.strip().splitlines()[-1])["html"]


def asks_waiting(folder: Path | None) -> list[str]:
    """What each ask waiting in an --approve-in folder says."""
    if folder is None:
        return []
    sys.path.insert(0, str(REPO))
    from kosika.ask_folder import waiting
    return [str(ask.get("says", "")) for ask in waiting(folder)]


def kos(text: str) -> str:
    """A Kosika text literal that reads back as exactly this text."""
    return json.dumps(text, ensure_ascii=False)


def recorded_tests(actions: list[dict]) -> str:
    """Kosika tests from real runs: each run's recorded results, given to
    main.kos's `done`, must end the way the run really ended."""
    out = ["// Real runs of the hands, recorded by diary.py --tests and replayed as",
           "// tests: if a change to main.kos would judge one of them differently,",
           "// a test here fails. Typed words are left out of what is kept.",
           "", 'import "main.kos"', ""]
    runs: dict[int, list[dict]] = {}
    for action in actions:
        runs.setdefault(action["run"], []).append(action)
    for run, steps in runs.items():
        # The note job checks its read-back apart from `done`, so it is left out.
        steps = [a for a in steps if not (a["job"] == "note" and a["step"] == "read")]
        if not steps:
            continue
        first_bad = next((i for i, a in enumerate(steps) if a["exit_code"] != 0), None)
        stopped = first_bad is not None
        count = first_bad if stopped else len(steps)
        ends = f"stopped at step {first_bad + 1}" if stopped else f"all {count} steps done"
        out.append(f'test "recorded run {run} ({steps[0]["job"]}, {steps[0]["at"]}): {ends}" {{')
        out.append("  steps: Ran[] = [")
        rows = []
        for a in steps:
            said = a["said"].splitlines()[0] if a["said"] else ""
            rows.append(f'    {{exit_code: {int(a["exit_code"])}, stdout: {kos(said)}, stderr: ""}}')
        out.append(",\n".join(rows))
        out.append("  ]")
        out.append("  d = done(steps)")
        out.append(f"  expect d.stopped == {'true' if stopped else 'false'}")
        out.append(f"  expect d.steps == {count}")
        out.append("}")
        out.append("")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, default=Path("diary.html"))
    parser.add_argument("--title", default="Hands diary")
    parser.add_argument("--lesson", type=int, metavar="RUN",
                        help="Write lesson.json from this run of the diary, for learn.kos to learn a skill from.")
    parser.add_argument("--name", default="", help="The learned skill's name, with --lesson.")
    parser.add_argument("--when", default="", help="When the learned skill fits, words split by commas, with --lesson.")
    parser.add_argument("--tests", type=Path, metavar="FILE.kos",
                        help="Write each run as a Kosika test that replays it against main.kos.")
    parser.add_argument("--board", action="store_true", help="Draw the live board of all agents (team.kos) instead.")
    parser.add_argument("--asks", type=Path, help="The --approve-in folder, so the board shows who waits for a yes.")
    parser.add_argument("--watch", action="store_true", help="Draw again every few seconds, until stopped.")
    args = parser.parse_args()
    if args.watch:
        import time
        last = None
        while True:
            seen = ([(p, p.stat().st_mtime) for p in traces(args.paths)], asks_waiting(args.asks))
            if seen != last:
                last = seen
                once(args)
            time.sleep(2)
    return once(args)


def once(args: argparse.Namespace) -> int:
    actions: list[dict] = []
    for trace in traces(args.paths):
        actions += entries(trace, actions[-1]["run"] if actions else 0)
    if args.tests is not None:
        args.tests.write_text(recorded_tests(actions), encoding="utf-8")
        print(f"{len({a['run'] for a in actions})} runs written as tests to {args.tests}")
        return 0
    if args.lesson is not None:
        lesson = {"l": {"name": args.name, "when": [w.strip() for w in args.when.split(",") if w.strip()],
                        "entries": [a for a in actions if a["run"] == args.lesson]}}
        Path("lesson.json").write_text(json.dumps(lesson, indent=2), encoding="utf-8")
        print(f"lesson.json holds run {args.lesson} ({len(lesson['l']['entries'])} actions). To learn from it:")
        print("kosika run learn.kos --workflow learn --input lesson.json --allow-shell --allow-folder . --approve")
        return 0
    try:
        if args.board:
            out = args.out if args.out != Path("diary.html") else Path("board.html")
            html = draw("team.kos", "board", {"t": {"title": "Kosika agents", "entries": actions,
                                                     "waiting": asks_waiting(args.asks)}})
        else:
            out = args.out
            html = draw("diary.kos", "main", {"d": {"title": args.title, "entries": actions}})
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 2
    out.write_text(html, encoding="utf-8")
    print(f"{len(actions)} actions drawn in {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
