"""The second training set for kosika-coder: Kosika's history, plus how the
coder fixed its own failures, plus more of the Go engine.

    python make_fixes.py          # writes kosika-coder.train2.jsonl beside the first set

The first set (make_dataset.py) teaches the edit format and Kosika's style
from its history. The 1.5B model trained on it wrote edits that apply, but
when it ran the coder alone its Go code did not compile and its fixes did
not fix. This set adds, to the same history:

- every question the coder asked a model, and the answer, in daily runs
  that passed their checks (kosika-daily/DATE-N/_agents/coder/runs), so
  the model sees the whole path to a passing change, fixes after a failed
  test included; the fixes are given FIX_WEIGHT times;
- each history example that changes a Go file once more, since that is
  where it failed.

The held-out set (kosika-coder.eval.jsonl, and trial15.jsonl inside it) is
not touched, so scores stay comparable with the first model's.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

from make_dataset import SYSTEM

DAILY = Path.home() / "Aryan_Private" / "kosika-daily"
TRAINING = DAILY / "training"
FIX_WEIGHT = 4
FAILED = re.compile(r"Traceback|FAILED|did not pass|failed|error:|cannot use|undefined:", re.I)


def model_pairs(trace: Path) -> list[tuple[str, str]]:
    """Each model call in a run's trace, with the answer it got, in order."""

    calls: list[dict] = []

    def walk(value) -> None:
        if isinstance(value, dict):
            if value.get("event") in ("model_call", "model_return"):
                calls.append(value)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for line in trace.read_text(encoding="utf-8").splitlines():
        if line.strip():
            walk(json.loads(line))
    pairs, asked = [], None
    for event in calls:
        if event["event"] == "model_call":
            asked = (event.get("arguments") or {}).get("text")
        elif asked:
            answer = event.get("content") or ""
            if isinstance(answer, str) and answer.strip():
                pairs.append((asked, answer.strip()))
            asked = None
    return pairs


def passed(run: Path) -> bool:
    report = run / "report.md"
    return report.exists() and "passed its checks" in report.read_text(encoding="utf-8")


def chat(asked: str, answer: str) -> dict:
    return {"messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": asked},
                         {"role": "assistant", "content": answer}]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(TRAINING))
    parser.add_argument("--base", default="kosika-coder", help="the history set to add to: BASE.train.jsonl")
    parser.add_argument("--name", default="kosika-coder.train2", help="what to call the result: NAME.jsonl")
    args = parser.parse_args()
    out = Path(args.out)
    history = [json.loads(line) for line in (out / f"{args.base}.train.jsonl").read_text(encoding="utf-8").splitlines() if line]
    go = [row for row in history if ".go" in row["messages"][1]["content"].split("This part of the change: ", 1)[-1][:120]]
    fixes, firsts = [], []
    for run in sorted(DAILY.glob("2026-*")):
        if not passed(run):
            continue
        for trace in sorted(run.glob("_agents/coder/runs/*.jsonl")):
            for asked, answer in model_pairs(trace):
                (fixes if FAILED.search(asked) else firsts).append(chat(asked, answer))
    # Answers the router kept because the change they were for passed
    # (smarter.Kept): from the cloud models above all, the teacher's
    # answers kosika-coder should learn to give itself.
    taught = []
    kept_file = Path.home() / ".kosika" / "kept-answers.jsonl"
    if kept_file.exists():
        for line in kept_file.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            asked = "\n".join(str(m.get("content") or "") for m in row["asked"]["messages"] if m.get("role") == "user")
            answer = (row["reply"]["choices"][0]["message"].get("content") or "").strip()
            if asked and answer and row.get("model") not in ("coder", "small", "kept"):
                taught.append(chat(asked, answer))
    rows = history + go + firsts + fixes * FIX_WEIGHT + taught * FIX_WEIGHT
    random.Random(7).shuffle(rows)
    target = out / f"{args.name}.jsonl"
    target.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    print(f"{len(history)} from history, {len(go)} Go once more, {len(firsts)} first answers and "
          f"{len(fixes)} fixes (x{FIX_WEIGHT}) from passing coder runs, {len(taught)} kept cloud answers (x{FIX_WEIGHT}): "
          f"{len(rows)} in {target}")


if __name__ == "__main__":
    main()
