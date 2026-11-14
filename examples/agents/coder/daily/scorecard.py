"""The coder swarm's scorecard: every request in scorecard.md, run on whatever
model the router serves, and the number whose checks passed.

    python scorecard.py --label kosika-coder-v2 --swarm 4

Each request runs in a fresh copy of Kosika, as a daily run does, from a copy
of scorecard.md kept in the run's folder, so the list itself is never marked.
The score is added to scorecard-history.jsonl in the kosika-daily folder, one
line per run, so model versions can be compared on the same tasks.
"""

from __future__ import annotations

import argparse
import datetime
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CARD = HERE / "scorecard.md"
WORK = Path.home() / "Aryan_Private" / "kosika-daily"
HISTORY = WORK / "scorecard-history.jsonl"


def tally(lines: list[str]) -> dict[str, list[str]]:
    marks = {"passed": "- [x] ", "failed": "- [!] ", "open": "- [ ] ", "taken": "- [~] "}
    return {name: [line[6:].split(" (20")[0].strip() for line in lines if line.startswith(mark)] for name, mark in marks.items()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True, help="the model version being scored, as it should appear in the history")
    parser.add_argument("--swarm", type=int, default=4, help="how many coders run at once")
    args = parser.parse_args()

    stamp = datetime.datetime.now().strftime("%Y-%m-%d-%H%M")
    copy = WORK / f"scorecard-{args.label}-{stamp}.md"
    WORK.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(CARD, copy)
    started = time.monotonic()
    while tally(copy.read_text(encoding="utf-8").splitlines())["open"]:
        subprocess.run([sys.executable, str(HERE / "daily.py"), "--todo", str(copy), "--swarm", str(args.swarm)], check=False)
    counted = tally(copy.read_text(encoding="utf-8").splitlines())
    total = sum(len(v) for v in counted.values())
    line = {
        "label": args.label,
        "when": stamp,
        "passed": len(counted["passed"]),
        "of": total,
        "seconds": round(time.monotonic() - started),
        "passed_names": [t.split("method ")[-1].split("(")[0] for t in counted["passed"]],
        "card": str(copy),
    }
    with HISTORY.open("a", encoding="utf-8") as out:
        out.write(json.dumps(line) + "\n")
    print(f"{args.label}: {line['passed']} of {total} passed in {line['seconds']} s")


if __name__ == "__main__":
    main()
