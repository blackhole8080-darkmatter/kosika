"""Let the virtual model improve its own routing, behind a gate.

    python improve.py --log ~/.kosika/virtual-router.jsonl            # propose only
    python improve.py --log ... --gate hard.json --keep-if-passes 4  # propose, test, keep or roll back

It reads what really happened (router.py's log) and proposes ONE change to
router.json at a time:

- a model that is mostly rate-limited moves later in every route
- if the quick model's replies fail the checks often, fewer requests start
  on it (a smaller max_chars); if they almost never fail, more do

A proposal is only a proposal. With --gate it is tried: a second router is
started with the proposed config, the benchmark's held-out task file is
run through it, and the change is kept only if at least --keep-if-passes
tasks pass, which should be what the current config passes. Otherwise the
config is left exactly as it was. Every decision, kept or not, is written
to improvements.jsonl beside the config, with the numbers it rested on.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent.parent / "benchmarks" / "coder"


def read(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def propose(config: dict, lines: list[dict]) -> tuple[dict, str] | None:
    limited, asked = Counter(), Counter()
    quick_bad = quick_all = 0
    for line in lines:
        for attempt in line["tried"]:
            if "status" in attempt or "error" in attempt:
                asked[attempt["model"]] += 1
            if attempt.get("status") == 429:
                limited[attempt["model"]] += 1
            if attempt["model"] == "quick" and attempt.get("status") == 200:
                quick_all += 1
                quick_bad += attempt.get("problem") is not None
    for name, count in limited.most_common():
        if asked[name] >= 10 and count / asked[name] > 0.5:
            new = copy.deepcopy(config)
            for route in new["routes"].values():
                if name in route and route[-1] != name:
                    route.remove(name)
                    route.append(name)
            if new != config:
                return new, f"{name} was rate-limited on {count} of {asked[name]} asks; it moves to the end of each route"
    rule = config["quick_when"]["max_chars"]
    if quick_all >= 10 and quick_bad / quick_all > 0.2 and rule > 500:
        new = copy.deepcopy(config)
        new["quick_when"]["max_chars"] = rule // 2
        return new, f"the quick model failed the checks on {quick_bad} of {quick_all} replies; max_chars {rule} -> {rule // 2}"
    if quick_all >= 20 and quick_bad / quick_all < 0.05 and rule < 20000:
        new = copy.deepcopy(config)
        new["quick_when"]["max_chars"] = rule * 2
        return new, f"the quick model failed the checks on only {quick_bad} of {quick_all} replies; max_chars {rule} -> {rule * 2}"
    return None


def gate(config: dict, tasks: str, port: int) -> int:
    """Pass count of the held-out tasks through a router running `config`."""
    trial = HERE / "router.trial.json"
    trial.write_text(json.dumps(config, indent=2), encoding="utf-8")
    server = subprocess.Popen([sys.executable, str(HERE / "router.py"), "--config", str(trial), "--port", str(port),
                               "--log", str(HERE / "router.trial.jsonl")])
    try:
        time.sleep(2)
        env = dict(os.environ, KOSIKA_MODEL_API_KEY="local")
        run = subprocess.run([sys.executable, "run.py", "--tasks", tasks, "--model", "kosika-virtual",
                              "--url", f"http://127.0.0.1:{port}/v1", "--protocol", "openai"],
                             cwd=BENCH, env=env, capture_output=True, text=True)
        found = re.search(r"(\d+) of (\d+) passed", run.stdout)
        return int(found.group(1)) if found else 0
    finally:
        server.terminate()
        trial.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", default=str(Path.home() / ".kosika" / "virtual-router.jsonl"))
    parser.add_argument("--config", default=str(HERE / "router.json"))
    parser.add_argument("--gate", help="Held-out task file in benchmarks/coder, such as hard.json.")
    parser.add_argument("--keep-if-passes", type=int, help="Tasks the current config passes; the change must match it.")
    parser.add_argument("--port", type=int, default=8792)
    args = parser.parse_args()
    path = Path(args.config)
    config = json.loads(path.read_text(encoding="utf-8"))
    found = propose(config, read(Path(args.log)))
    if found is None:
        print("Nothing to change: the log gives no reason to.")
        return
    new, why = found
    print("Proposed:", why)
    record = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "why": why, "kept": False}
    if args.gate:
        if args.keep_if_passes is None:
            raise SystemExit("--gate needs --keep-if-passes: what the current config passes.")
        passed = gate(new, args.gate, args.port)
        record.update({"gate": args.gate, "passed": passed, "needed": args.keep_if_passes})
        if passed >= args.keep_if_passes:
            path.write_text(json.dumps(new, indent=2) + "\n", encoding="utf-8")
            record["kept"] = True
            print(f"Kept: {passed} passed, {args.keep_if_passes} needed.")
        else:
            print(f"Rolled back: {passed} passed, {args.keep_if_passes} needed. router.json is unchanged.")
    with (path.parent / "improvements.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps(record) + "\n")


if __name__ == "__main__":
    main()
