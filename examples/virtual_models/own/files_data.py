"""The examples Kosika's first own model learns from: Kosika's own history.

    python files_data.py      # writes files.json beside this file

Each commit is one example: its words, and the files it changed. The newest
tenth is held out for the test. Only reading git is done here; everything a
model is, learns and is judged by is in files.kos, in Kosika.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
KEPT = (".kos", ".py", ".go", ".md")
SKIPPED = ("site/", "benchmarks/", "archive", "vendor/", "results/", "_probe/", "examples/agents/coder/runs")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def main() -> None:
    commits = git("rev-list", "--all", "--no-merges", "--date-order", "--reverse").split()
    changes = []
    for commit in commits:
        words = git("log", "-1", "--format=%B", commit)
        words = " ".join(line for line in words.split("\n") if not line.startswith("Co-Authored-By")).strip()
        files = [p for p in git("diff-tree", "--no-commit-id", "--name-only", "-r", commit).split("\n")
                 if p.endswith(KEPT) and not p.startswith(SKIPPED)]
        if words and files:
            changes.append({"text": words[:2000], "files": files[:40]})
    held = max(1, len(changes) // 10)
    data = {"train": changes[:-held], "test": changes[-held:]}
    (HERE / "files.json").write_text(json.dumps({"data": data}), encoding="utf-8")
    print(f"{len(data['train'])} changes to learn from, {len(data['test'])} held out")


if __name__ == "__main__":
    main()
