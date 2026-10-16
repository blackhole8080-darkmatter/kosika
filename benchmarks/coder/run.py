"""Give the coder agent real tasks, with no outside agent, and score them.

    python run.py --model qwen2.5:7b
    python run.py --model qwen2.5-coder:7b --only website
    python run.py --model NAME --url https://... --protocol openai

Each task starts from a fresh copy of a small project in `project/`. The
coder runs as Kosika Studio runs it: its own model, its own tools, the
granted folder and the right to start programs. Every change it asks for
is allowed, as a person saying yes would. No outside agent is asked, so
what is scored is the work of the Kosika agent and the model it is given.

A task passes when every one of its checks succeeds afterwards in that
folder: the project's tests, and a line of code that tries the new thing
itself. The coder's own word that it is done counts for nothing.

With `--url` the model is asked over the network; its key is read from
the environment variable KOSIKA_MODEL_API_KEY and never written anywhere.

Results are saved in `results/`, one file per run.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
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


def checked(folder: Path, checks: list[list[str]]) -> list[str]:
    """What each check said that failed; nothing when all passed."""

    failed = []
    for command in checks:
        program = sys.executable if command[0] == "python" else shutil.which(command[0])
        if program is None:
            failed.append(f"{command[0]} was not found")
            continue
        ran = subprocess.run(
            [program, *command[1:]], cwd=folder, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
        )
        if ran.returncode != 0:
            said = (ran.stdout + ran.stderr).strip().splitlines()
            failed.append(" ".join(command[:3]) + ": " + (said[-1] if said else f"exit {ran.returncode}"))
    return failed


def run_task(args: argparse.Namespace, task: dict[str, Any]) -> dict[str, Any]:
    # An outside agent may only be shown a folder inside the one root the
    # owner named, so with one allowed the work happens there.
    scratch = Path(tempfile.mkdtemp(prefix="kosika-coder-bench-", dir=args.within if args.outside else None))
    folder = scratch / task["folder"]
    source = HERE / "project" / task["folder"]
    if source.is_dir():
        shutil.copytree(source, folder)
    else:
        folder.mkdir()
    # A fresh copy of the agent each time: nothing it learned or remembered
    # in one task, or with another model, may help it in the next.
    agents = scratch / "_kosika"
    shutil.copytree(AGENTS / "coder", agents / "coder", ignore=shutil.ignore_patterns("memory", "runs"))
    app = App(agents, bind_models=binder_for(args), model_label=args.model,
              allow_folder=folder, allow_shell=True,
              outside=tuple(args.outside), outside_within=Path(args.within) if args.outside else None)
    agent = next(key for key in app.agents() if key.startswith("coder/"))
    started = time.monotonic()
    job = app.start(agent, {"text": task["task"]})
    asked = 0
    while job.state in ("running", "waiting") and time.monotonic() - started < args.limit_seconds:
        if job.state == "waiting":
            asked += 1
            app.decide(job.id, True)
        time.sleep(0.5)
    seconds = round(time.monotonic() - started, 1)
    failures = checked(folder, task["checks"])
    written = sorted(
        str(path.relative_to(folder)) for path in folder.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and ".pytest_cache" not in path.parts
    )
    result = {
        "id": task["id"],
        "passed": not failures and job.state == "done",
        "state": job.state if job.state in ("done", "failed", "refused") else "out of time",
        "seconds": seconds,
        "approvals": asked,
        "failures": failures,
        "error": job.error,
        "said": (getattr(job, "text", "") or "")[:800],
        "files": written,
    }
    if args.keep:
        result["folder"] = str(folder)
    else:
        shutil.rmtree(scratch, ignore_errors=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--model", required=True)
    parser.add_argument("--url", help="A model server other than the local Ollama.")
    parser.add_argument("--protocol", choices=("ollama", "openai"), default="ollama")
    parser.add_argument("--timeout", type=float, default=900.0, help="Seconds a model may take to answer once.")
    parser.add_argument("--limit-seconds", type=float, default=3600.0, help="Seconds a task may take.")
    parser.add_argument("--only", help="Run only the task with this id.")
    parser.add_argument("--keep", action="store_true", help="Keep each task's folder to look at.")
    parser.add_argument("--outside", action="append", default=[], help="An outside agent the coder may ask first, such as cline. May be repeated.")
    parser.add_argument("--within", default=str(Path.home() / "Aryan_Private" / "kosika-bench"), help="The only folder outside agents may be shown; the tasks run inside it.")
    parser.add_argument("--tasks", default="tasks.json", help="The task file in this folder: tasks.json, or hard.json.")
    args = parser.parse_args()
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
        print(f"  {'PASS' if result['passed'] else 'FAIL'}  {result['state']}, {result['seconds']} s"
              + (f"  {result['failures'][0]}" if result["failures"] else ""), flush=True)
    passed = sum(1 for result in results if result["passed"])
    print(f"{passed} of {len(results)} passed with {args.model}")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = HERE / "results" / f"{stamp}-{args.model.replace(':', '_').replace('/', '_')}.json"
    out.write_text(json.dumps({"model": args.model, "url": args.url, "results": results}, indent=2), encoding="utf-8")
    print(f"Saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
