"""Hard test: the OS builder builds MayaOS stage by stage, and stops at the
first stage it cannot pass.

    python run.py --model qwen/qwen3.8-27b --url https://api.groq.com/openai/v1
    python run.py ... --stages 1-4 --limit-seconds 900

The builder (examples/agents/osbuilder) runs as Kosika Studio runs it with
sessions on: in a walled copy of the workspace inside WSL, where make, nasm,
gcc and QEMU run with no network. Every step it asks for is allowed, as a
person saying yes would, but only inside that copy.

A stage passes only if this script, not the agent, boots the image the
agent left and finds what the stage's lesson file says the screen must
show. The agent's own word counts for nothing.

Limits, so that nothing runs for ever:
* the agent stops itself after three tries or its budget (6 model calls,
  1500 seconds a stage);
* this script stops waiting for a stage after --limit-seconds and counts it
  failed;
* it stops at the first failed stage, since each stage builds on the last.

The workspace is copied from Aryan_Private/kosika-bench/osbuild (lessons,
tutorial, tools, and toolchain: nasm and QEMU unpacked from Ubuntu's
packages; see workspace/README.txt), and each run's files and results are
kept in results/. The model key is read from KOSIKA_MODEL_API_KEY and never written.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))

from kosika.app import App  # noqa: E402
from kosika.model import OpenAICompatibleProvider  # noqa: E402

AGENT = REPO / "examples" / "agents" / "osbuilder"
WORKSPACE = Path(os.environ.get("OSBUILD_WORKSPACE", r"C:\Users\Aryan\Aryan_Private\kosika-bench\osbuild"))


def binder_for(args: argparse.Namespace) -> Any:
    def binder(program: Any) -> dict[str, Any]:
        return {
            # The teacher may be another model: one that finds causes well
            # need not be the one that writes assembly well.
            model.name: OpenAICompatibleProvider(
                args.url, args.teacher_model if model.name == "teacher" and args.teacher_model else args.model,
                api_key=os.environ.get("KOSIKA_MODEL_API_KEY"),
                protocol="openai", timeout=args.timeout,
            )
            for model in program.models
            if model.provider is None
        }

    return binder


def spec_of(text: str) -> dict[str, Any]:
    spec: dict[str, Any] = {"expect": [], "absent": [], "rising": [], "keys": "", "snapshots": "1", "title": ""}
    for line in text.splitlines():
        if line.strip() == "---":
            break
        key, _, value = line.partition(":")
        value = value.strip()
        if key == "Expect":
            spec["expect"].append(value)
        elif key == "Absent":
            spec["absent"].append(value)
        elif key == "Rising":
            spec["rising"].append(value + ":" if not value.endswith(":") else value)
        elif key in ("Keys", "Snapshots", "Title"):
            spec[key.lower()] = value
    return spec


def number_after(screen: str, label: str) -> float | None:
    for line in screen.splitlines():
        if label in line:
            rest = line.split(label, 1)[1].split()
            try:
                return float(rest[0].strip(".,;")) if rest else None
            except ValueError:
                return None
    return None


def screens_of(stdout: str) -> list[str]:
    found, lines, open_ = [], [], False
    for line in stdout.splitlines():
        if line.startswith("=== screen "):
            if open_:
                found.append("\n".join(lines))
            lines, open_ = [], True
        elif open_:
            lines.append(line)
    if open_:
        found.append("\n".join(lines))
    return found


def verify(session: Any, stage: int, spec: dict[str, Any]) -> tuple[list[str], str]:
    """Boot the stage's image ourselves and check its screen."""

    image = f"stages/{stage}/os-image.bin"
    if not (session.work / image).is_file():
        return [f"No {image} was left behind"], ""
    args = ["tools/screen.py", image, "--snapshots", spec["snapshots"] or "1"]
    if spec["keys"]:
        args += ["--keys", spec["keys"]]
    ran = session.run("python3", args, timeout=60)
    shown = screens_of(str(ran.get("stdout", "")))
    if not shown:
        return ["The screen could not be read: " + str(ran.get("stdout", ""))[-300:] + str(ran.get("stderr", ""))[-300:]], ""
    last = shown[-1]
    failed = [f"Missing: {e}" for e in spec["expect"] if e not in last]
    failed += [f"Must not show: {a}" for a in spec["absent"] if a in last]
    for label in spec["rising"]:
        first, second = number_after(shown[0], label), number_after(last, label)
        if first is None or second is None or second <= first:
            failed.append(f"{label} did not grow: {first} then {second}")
    return failed, "\n--- next reading ---\n".join(shown)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--stages", default="1-8")
    parser.add_argument("--limit-seconds", type=float, default=1600)
    parser.add_argument("--teacher-model", help="The model that names the cause of each failure (default: --model).")
    parser.add_argument("--without-rules", action="store_true",
                        help="Run each stage as if nothing had been learned, and learn nothing.")
    parser.add_argument("--rules-file", type=Path,
                        help="Rules learned in earlier runs: read before, and written back after.")
    args = parser.parse_args()
    first, _, last = args.stages.partition("-")
    stages = list(range(int(first), int(last or first) + 1))

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = HERE / "results" / stamp
    folder = run_dir / "workspace"
    for part in ("lessons", "tutorial", "examples", "tools", "toolchain"):
        shutil.copytree(WORKSPACE / part, folder / part)
    agents = run_dir / "agents"
    shutil.copytree(AGENT, agents / "osbuilder", ignore=shutil.ignore_patterns("memory", "runs"))
    # The rules the builder taught itself, carried from run to run when asked.
    kept_rules = agents / "osbuilder" / "memory" / "rules.json"
    if args.rules_file and args.rules_file.is_file() and not args.without_rules:
        kept_rules.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.rules_file, kept_rules)
    app = App(agents, bind_models=binder_for(args), model_label=args.model, allow_folder=folder,
              allow_shell=True, in_session=True, session_home=run_dir / "sessions")
    agent = next(key for key in app.agents() if key.startswith("osbuilder/"))

    results: list[dict[str, Any]] = []
    for stage in stages:
        spec = spec_of((folder / "lessons" / f"{stage}.md").read_text(encoding="utf-8"))
        print(f"Stage {stage}: {spec['title']} ...", flush=True)
        started = time.monotonic()
        job = app.start(agent, {"text": f"stage {stage}" + (" without rules" if args.without_rules else "")})
        allowed = 0
        while job.state in ("running", "waiting") and time.monotonic() - started < args.limit_seconds:
            if job.state == "waiting":
                allowed += 1
                app.decide(job.id, True)
            time.sleep(0.5)
        seconds = round(time.monotonic() - started, 1)
        session = next(iter(app._sessions.values()), None)
        failed, screen = verify(session, stage, spec) if session is not None else (["No session was made"], "")
        state = job.state if job.state in ("done", "failed", "refused") else "out of time"
        result = {
            "stage": stage, "title": spec["title"], "passed": not failed and state == "done",
            "state": state, "seconds": seconds, "allowed": allowed, "failed": failed,
            "error": job.error, "agent_said": (getattr(job, "text", "") or "")[:1500], "screen": screen[:3000],
        }
        results.append(result)
        print(f"  {'PASSED' if result['passed'] else 'FAILED'} in {seconds}s" + ("" if result["passed"] else f": {failed or job.error}"), flush=True)
        if session is not None:
            # Keep what the agent built, outside the session, for reading.
            target = run_dir / "built" / str(stage)
            source = session.work / "stages" / str(stage)
            if source.is_dir():
                shutil.copytree(source, target, dirs_exist_ok=True)
        if not result["passed"]:
            print("Stopping: each stage builds on the one before.", flush=True)
            break

    rules: list[str] = []
    if kept_rules.is_file():
        try:
            stored = json.loads(kept_rules.read_text(encoding="utf-8"))
            rules = next(iter(stored.values()), {}).get("items", []) if isinstance(stored, dict) else []
        except (ValueError, AttributeError):
            rules = []
        if args.rules_file and not args.without_rules:
            args.rules_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(kept_rules, args.rules_file)
    summary = {
        "rules": "off" if args.without_rules else "on", "teacher": args.teacher_model or args.model, "learned": rules,
        "model": args.model, "when": stamp, "stages": args.stages,
        "passed": sum(r["passed"] for r in results), "tried": len(results), "results": results,
    }
    (run_dir / "results.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"{summary['passed']} of {len(stages)} stages passed. Results: {run_dir / 'results.json'}")
    return 0 if summary["passed"] == len(stages) else 1


if __name__ == "__main__":
    raise SystemExit(main())
