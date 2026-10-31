"""Give the coder agent real tasks, with no outside agent, and score them.

    python run.py --model qwen2.5:7b
    python run.py --model qwen2.5-coder:7b --only website
    python run.py --model NAME --url https://... --protocol openai
    python run.py --model qwen2.5:7b --best-of 16
    python run.py --model qwen2.5:7b --best-of 4 --repair
    python run.py --compare results/a.json results/b.json

Each task starts from a fresh copy of a small project in `project/`. The
coder runs as Kosika Studio runs it: its own model, its own tools, the
granted folder and the right to start programs. Every change it asks for
is allowed, as a person saying yes would. No outside agent is asked, so
what is scored is the work of the Kosika agent and the model it is given.

A task passes when every one of its checks succeeds afterwards in that
folder: the project's tests, and a line of code that tries the new thing
itself. The coder's own word that it is done counts for nothing.

`--best-of N` gives each task up to N independent attempts, each from its
own fresh copy of the project and of the agent. A task stops at its first
attempt that passes, and the checks are the only judge. Nothing said in a
failed attempt is shown to the next one, so this measures what resampling
and checking buy, not what repair buys. The summary reports the pass rate
and the wall clock for the first 1, 4 and 16 attempts (whichever are at
most N): the clock is what was really spent, because a task stops at its
first pass, and the cost of the last attempt is what decides whether it
was worth buying. A run has no stop of its own, so an attempt still
working when its time is up ends the task: a second attempt is never
started beside a call that is still in flight.

`--repair` changes what a second attempt is: instead of a fresh copy of the
project, it works in the folder the attempt before it left, and it is told
the end of what each failing check said. That measures fixing a real
failure rather than sampling again, and it is the fair comparison for
best-of-N: same attempts, same checks, only the information differs.

`--compare` reads saved result files and prints their attempt ladders
beside each other, so the two modes can be put side by side without a
model being asked anything.

Every task names the language it asks for, and a run reports pass and count
for each one. The Kosika tasks are in the ordinary set on purpose: one run
then says whether the agent writes Kosika programs as readily as Python
ones, instead of that question being asked of a single hard task.

With `--url` the model is asked over the network; its key is read from
the environment variable KOSIKA_MODEL_API_KEY and never written anywhere.

Results are saved in `results/`, one file per run.
"""

from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))

from kosika.app import App  # noqa: E402
from kosika.model import OpenAICompatibleProvider  # noqa: E402

AGENTS = REPO / "examples" / "agents"


def binder_for(args: argparse.Namespace) -> Any:
    url = args.url or ("http://localhost:11434" if args.protocol == "ollama" else "http://localhost:11434/v1")

    def binder(program: Any) -> dict[str, Any]:
        return {
            model.name: OpenAICompatibleProvider(
                url,
                args.model,
                api_key=os.environ.get("KOSIKA_MODEL_API_KEY"),
                protocol=args.protocol,
                timeout=args.timeout,
            )
            for model in program.models
            if model.provider is None
        }

    return binder


#: The attempt counts the summary reports, when N allows them.
LADDER = (1, 4, 16)


def by_language(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Pass and count for each language the tasks asked for.

    A run mixes tasks, so its pass rate alone does not say what the agent
    managed to write. This does, and it is the number that answers whether
    Kosika programs come out of the same agent as Python ones.
    """

    tally: dict[str, dict[str, Any]] = {}
    for result in results:
        row = tally.setdefault(str(result.get("language") or "unknown"), {"passed": 0, "tasks": 0})
        row["passed"] += 1 if result["passed"] else 0
        row["tasks"] += 1
    return dict(sorted(tally.items()))


def ladder(best_of: int) -> list[int]:
    """The attempt counts to report for `best_of`: 1, 4, 16, and N itself."""

    steps = [k for k in LADDER if k < best_of]
    return [*steps, best_of]


def summarise(attempts: list[dict[str, Any]], best_of: int) -> dict[str, Any]:
    """Pass, attempts spent and seconds spent, for the first k attempts.

    A run stops at its first passing attempt, so the cost of k attempts is
    what was actually bought: every attempt up to the pass, or k of them when
    none passed. Counting the attempts a run never made would price work that
    did not happen.
    """

    summary: dict[str, Any] = {}
    for k in ladder(best_of):
        counted = attempts[:k]
        summary[str(k)] = {
            "passed": any(item["passed"] for item in counted),
            "attempts": len(counted),
            "seconds": round(sum(item["seconds"] for item in counted), 1),
        }
    return summary


#: A line of a Python traceback that names the file it came from.
FRAME = re.compile(r'^\s*File "(?P<path>[^"]*)"')


def relative(line: str, folder: Path) -> str:
    """A line with the task's folder and this benchmark's named as relative.

    The parser names the file it read in full, and the coder's tools take
    only paths relative to its folder: shown an absolute one, a repair
    attempt stopped before changing a line.
    """

    for root in (str(folder), str(REPO)):
        for sep in ("\\", "/"):
            line = line.replace(root + sep, "")
        line = line.replace(root, ".")
    return line


def told(said: list[str], folder: Path) -> list[str]:
    """What a check said, with no file the coder may not open named in it.

    A check is this benchmark's own program, so its traceback names files
    outside the task's folder. Passing those words on made every repair
    attempt stop at once, asking to read a file it may not have: `path ...
    must be relative to the granted folder`. The frame, its source line and
    its caret go; the error stays, because that is what a repair acts on.
    """

    folder = folder.resolve()
    kept: list[str] = []
    dropping = 0
    for line in said:
        if dropping:
            dropping -= 1
            continue
        frame = FRAME.match(line)
        if frame and Path(frame["path"]).is_absolute():
            try:
                inside = Path(frame["path"]).resolve().is_relative_to(folder)
            except OSError:
                inside = False
            if not inside:
                # The source line under a frame, then its row of carets.
                dropping = 2
                continue
        kept.append(relative(line, folder))
    return kept


def checked(folder: Path, checks: list[list[str]]) -> tuple[list[str], list[str]]:
    """One line per failing check, and the end of what each one said.

    The first list goes into the result. The second is what a repair attempt
    is shown, because a model can only fix what it is told about, and "1
    failed" is not something a next attempt can act on.
    """

    failed: list[str] = []
    why: list[str] = []
    for command in checks:
        # {repo} is where this benchmark lives, so a check that must import
        # Kosika itself works on any computer.
        command = [part.replace("{repo}", str(REPO)) for part in command]
        program = sys.executable if command[0] == "python" else shutil.which(command[0])
        if program is None:
            failed.append(f"{command[0]} was not found")
            continue
        try:
            ran = subprocess.run(
                [program, *command[1:]], cwd=folder, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=120,
            )
        except subprocess.TimeoutExpired:
            # A check that never returns is a failing check, not the end of the
            # run: one bad attempt must not take the measurement down with it.
            failed.append(" ".join(command[:3]) + ": did not finish in 120 s")
            why.append(" ".join(command[:2]) + " said:\n  it did not finish in 120 s")
            continue
        if ran.returncode != 0:
            said = [line.rstrip() for line in (ran.stdout + ran.stderr).splitlines() if line.strip()]
            failed.append(" ".join(command[:3]) + ": " + (said[-1] if said else f"exit {ran.returncode}"))
            tail = told(said, folder)[-6:] or [f"exit {ran.returncode}"]
            why.append(" ".join(command[:2]) + " said:\n" + "\n".join("  " + line[:200] for line in tail))
    return failed, why


def copy_committed(paths: list[str], folder: Path) -> None:
    """Kosika's own files at its last commit, put into the task's folder.

    A task in kosika_dev.json works on Kosika itself, so its project is
    these files as they were committed: work in progress in the checkout
    never reaches a measurement, and two runs a week apart start alike.
    """

    archive = subprocess.run(
        ["git", "archive", "--format=tar", "HEAD", *paths],
        cwd=REPO, capture_output=True, check=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(folder, filter="data")


def prepare(args: argparse.Namespace, task: dict[str, Any]) -> tuple[Path, Path, App]:
    """A fresh copy of the project and of the agent, ready to work in."""

    # An outside agent may only be shown a folder inside the one root the
    # owner named, so with one allowed the work happens there.
    scratch = Path(tempfile.mkdtemp(prefix="kosika-coder-bench-", dir=args.within if args.outside else None))
    folder = scratch / task["folder"]
    source = HERE / "project" / task["folder"]
    if source.is_dir():
        shutil.copytree(source, folder)
    else:
        folder.mkdir()
    if task.get("copy"):
        copy_committed(task["copy"], folder)
    # A fresh copy of the agent each time: nothing it learned or remembered
    # in one task, or with another model, may help it in the next.
    agents = scratch / "_kosika"
    shutil.copytree(AGENTS / "coder", agents / "coder", ignore=shutil.ignore_patterns("memory", "runs"))
    app = App(agents, bind_models=binder_for(args), model_label=args.model,
              allow_folder=folder, allow_shell=True,
              outside=tuple(args.outside), outside_within=Path(args.within) if args.outside else None)
    return scratch, folder, app


def drop(scratch: Path, keep: bool) -> None:
    """Throw an attempt's folder away, unless someone asked to look at it."""

    if not keep:
        shutil.rmtree(scratch, ignore_errors=True)


def run_attempt(
    args: argparse.Namespace,
    task: dict[str, Any],
    number: int,
    folder: Path,
    app: App,
    asked: str,
) -> dict[str, Any]:
    """One attempt at a task, in the folder given and from the words given."""

    agent = next(key for key in app.agents() if key.startswith("coder/"))
    started = time.monotonic()
    job = app.start(agent, {"text": asked})
    approved = 0
    while job.state in ("running", "waiting") and time.monotonic() - started < args.limit_seconds:
        if job.state == "waiting":
            approved += 1
            app.decide(job.id, True)
        time.sleep(0.5)
    # A run has no stop of its own, and a second attempt would otherwise start
    # beside a call that is still in flight. Wait for it to end, up to a
    # bounded while, and count that wait: what it costs is real.
    drain = time.monotonic() + 60
    while job.state in ("running", "waiting") and time.monotonic() < drain:
        time.sleep(0.5)
    abandoned = job.state in ("running", "waiting")
    seconds = round(time.monotonic() - started, 1)
    failures, excerpts = checked(folder, task["checks"])
    written = sorted(
        str(path.relative_to(folder)) for path in folder.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and ".pytest_cache" not in path.parts
    )
    result = {
        "id": task["id"],
        "attempt": number,
        "asked": asked[:1000],
        "passed": not failures and job.state == "done",
        "state": job.state if job.state in ("done", "failed", "refused") else "out of time",
        "abandoned": abandoned,
        "seconds": seconds,
        "approvals": approved,
        "failures": failures,
        "excerpts": excerpts,
        "error": job.error,
        "said": (getattr(job, "text", "") or "")[:800],
        "files": written,
    }
    if args.keep:
        result["folder"] = str(folder)
    return result


def repair_text(task_text: str, excerpts: list[str], number: int) -> str:
    """The task again, with what the checks said about the attempt before."""

    return (
        task_text
        + "\n\nThis is attempt " + str(number) + " at the same task. The attempt before it did "
        "not pass its checks. This is what they said:\n\n"
        + "\n\n".join(excerpts)
        + "\n\nThe folder still holds that attempt's files. Make the checks pass, and keep "
        "whatever already works."
    )


def run_task(args: argparse.Namespace, task: dict[str, Any]) -> dict[str, Any]:
    """Attempts at one task, stopping at the first pass.

    Best-of-N gives every attempt a fresh copy, so the attempts are
    independent samples. Repair keeps the folder and shows each attempt what
    the checks said about the one before, so it measures fixing, not resampling.
    """

    attempts: list[dict[str, Any]] = []
    scratch, folder, app = prepare(args, task)
    asked = task["task"]
    for number in range(1, args.best_of + 1):
        if number > 1 and not args.repair:
            drop(scratch, args.keep)
            scratch, folder, app = prepare(args, task)
        attempt = run_attempt(args, task, number, folder, app, asked)
        attempts.append(attempt)
        # An attempt that was given up on is still working: a retry would run
        # beside it and neither clock would mean anything.
        if attempt["passed"] or attempt.get("abandoned"):
            break
        if args.repair and attempt["excerpts"]:
            asked = repair_text(task["task"], attempt["excerpts"], number + 1)
    drop(scratch, args.keep)
    winner = next(
        (item for item in attempts if item["passed"]), attempts[-1]
    )
    result = {
        "id": task["id"],
        "language": task.get("language") or "unknown",
        "passed": any(item["passed"] for item in attempts),
        "state": winner["state"],
        "seconds": round(sum(item["seconds"] for item in attempts), 1),
        "approvals": sum(item["approvals"] for item in attempts),
        "failures": winner["failures"],
        "error": winner["error"],
        "said": winner["said"],
        "files": winner["files"],
        "attempts": attempts,
        "summary": summarise(attempts, args.best_of),
    }
    if "folder" in winner:
        result["folder"] = winner["folder"]
    return result


def describe_report(path: Path) -> dict[str, Any]:
    """What a saved run says, in the shape a comparison needs."""

    report = json.loads(path.read_text(encoding="utf-8"))
    return {
        "file": path.name,
        "model": str(report.get("model") or "?"),
        "mode": str(report.get("mode") or "best-of"),
        "best_of": int(report.get("best_of") or 1),
        "summary": report.get("summary") or {},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--model", help="The model the agent is given. Not needed with --compare.")
    parser.add_argument("--url", help="A model server other than the local Ollama.")
    parser.add_argument("--protocol", choices=("ollama", "openai"), default="ollama")
    parser.add_argument("--timeout", type=float, default=900.0, help="Seconds a model may take to answer once.")
    parser.add_argument("--limit-seconds", type=float, default=3600.0, help="Seconds a task may take.")
    parser.add_argument("--only", help="Run only the task with this id.")
    parser.add_argument("--keep", action="store_true", help="Keep each task's folder to look at.")
    parser.add_argument("--outside", action="append", default=[], help="An outside agent the coder may ask first, such as cline. May be repeated.")
    parser.add_argument("--within", default=str(Path.home() / "Aryan_Private" / "kosika-bench"), help="The only folder outside agents may be shown; the tasks run inside it.")
    parser.add_argument("--tasks", default="tasks.json", help="The task file in this folder: tasks.json, hard.json, hard2.json, or kosika_dev.json.")
    parser.add_argument("--best-of", type=int, default=1, metavar="N",
                        help="Give each task up to N independent attempts, stopping at the first that passes.")
    parser.add_argument("--repair", action="store_true",
                        help="Keep the folder and tell each attempt what the failing checks said, instead of starting fresh.")
    parser.add_argument("--compare", nargs="+", default=[], metavar="FILE",
                        help="Print the attempt ladders of these saved result files.")
    args = parser.parse_args()
    if args.compare:
        try:
            reports = [describe_report(Path(name)) for name in args.compare]
        except (OSError, ValueError) as exc:
            parser.error(f"--compare could not read a result file: {exc}")
        print(f"{'file':34} {'model':20} {'mode':8} {'attempts':>8}  passed   wall clock")
        for report in reports:
            for k, row in report["summary"].items():
                print(f"{report['file'][:33]:34} {report['model'][:19]:20} {report['mode']:8} {k:>8}  "
                      f"{row.get('passed', 0)}/{row.get('tasks', 0)}     {row.get('seconds', 0):.0f} s")
        return 0
    if not args.model:
        parser.error("--model is needed unless --compare names saved runs")
    if args.best_of < 1:
        parser.error("--best-of must be 1 or more")
    if args.repair and args.best_of < 2:
        parser.error("--repair needs --best-of 2 or more: one attempt has nothing to repair from")
    if args.url and args.protocol == "ollama":
        args.protocol = "openai"
    if args.outside:
        Path(args.within).mkdir(parents=True, exist_ok=True)
    tasks = json.loads((HERE / args.tasks).read_text(encoding="utf-8"))
    if args.only:
        tasks = [task for task in tasks if task["id"] == args.only]
    results = []
    for task in tasks:
        print(f"{task['id']} ...", flush=True)
        result = run_task(args, task)
        results.append(result)
        used = len(result["attempts"])
        print(f"  {'PASS' if result['passed'] else 'FAIL'}  {result['state']}, {result['seconds']} s"
              + (f", {used} attempts" if args.best_of > 1 else "")
              + (f"  {result['failures'][0]}" if result["failures"] else ""), flush=True)
    passed = sum(1 for result in results if result["passed"])
    mode = "repair" if args.repair else "best-of"
    print(f"{passed} of {len(results)} passed with {args.model}" + (" in repair mode" if args.repair else ""))
    summary = {}
    for k in ladder(args.best_of):
        rows = [result["summary"][str(k)] for result in results]
        summary[str(k)] = {
            "passed": sum(1 for row in rows if row["passed"]),
            "tasks": len(rows),
            "seconds": round(sum(row["seconds"] for row in rows), 1),
            "attempts": sum(row["attempts"] for row in rows),
        }
    if args.best_of > 1:
        print("")
        print("attempts   passed   wall clock")
        for k, row in summary.items():
            print(f"{k:>8}   {row['passed']}/{row['tasks']}     {row['seconds']:.0f} s")
        print("The clock counts only the attempts that were made: a task stops at its first pass.")
    languages = by_language(results)
    print("")
    print("language     passed   tasks")
    for name, row in languages.items():
        print(f"{name:12} {row['passed']}/{row['tasks']}")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = HERE / "results" / f"{stamp}-{args.model.replace(':', '_').replace('/', '_')}.json"
    out.write_text(
        json.dumps(
            {"model": args.model, "url": args.url, "mode": mode,
             "best_of": args.best_of, "summary": summary, "languages": languages,
             "results": results},
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
