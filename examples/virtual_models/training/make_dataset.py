"""Kosika's training set for its own coder model, from Kosika's own history.

    python make_dataset.py                # writes kosika-coder.train.jsonl and .eval.jsonl
    python make_dataset.py --ref origin/studio --out DIR

Every commit to Kosika is a change someone checked before it went in: its
message says what was wanted, and its diff is what was done. Each becomes
one example in the exact shape the Kosika coder asks its model for an edit:
the request and the parts of each file around the change, then the answer
as FIND/REPLACE blocks. A model trained on these learns Kosika's language,
its two engines and its house style, and learns to answer in the format the
coder applies.

The checked changes the daily coder makes (kosika-daily/examples.jsonl) are
added the same way. The newest tenth of the commits is held out as the
evaluation set, so a trained model is judged on changes it never saw.

A commit is split the way the coder splits its own work: one example for
each file it changed, and a file's edits in steps whose answer stays under
about 6,000 characters, what a small model is asked for in one go, each
step applied on top of the ones before. Each example keeps the commit's
words as its request and says which file and which step it is.
"""

from __future__ import annotations

import argparse
import difflib
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
DAILY = Path.home() / "Aryan_Private" / "kosika-daily" / "examples.jsonl"
KEPT = (".kos", ".py", ".go", ".md")
SKIPPED = ("site/", "benchmarks/", "archive", "vendor/", "results/", "_probe/")
MOST_ANSWER = 6000
CONTEXT = 3
SYSTEM = (
    "You are the Kosika coder. You change Kosika's files with edits: each edit is "
    "<<<<<<< FIND, whole lines copied exactly from the file, =======, the lines to "
    "put in their place, >>>>>>> REPLACE. Answer with the edit blocks only."
)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def show(ref: str, path: str) -> str | None:
    done = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=REPO, capture_output=True)
    if done.returncode != 0:
        return None
    try:
        return done.stdout.decode("utf-8").replace("\r\n", "\n")
    except UnicodeDecodeError:
        return None


def blocks(before: str, after: str) -> tuple[list[str], list[str]]:
    """The edit blocks that turn `before` into `after`, and the excerpt of
    `before` a model would be shown: each change with a few lines around it,
    enough that every FIND is found in exactly one place."""
    old, new = before.split("\n"), after.split("\n")
    edits: list[str] = []
    shown: list[str] = []
    for group in difflib.SequenceMatcher(None, old, new, autojunk=False).get_grouped_opcodes(CONTEXT):
        first, last = group[0], group[-1]
        find = old[first[1]:last[2]]
        put = new[first[3]:last[4]]
        # Grow the FIND until it is in one place only, as the coder requires.
        start, end = first[1], last[2]
        while "\n".join(find) and before.count("\n".join(find)) > 1 and (start > 0 or end < len(old)):
            start, end = max(0, start - 1), min(len(old), end + 1)
            find = old[start:end]
            put = old[start:first[1]] + new[first[3]:last[4]] + old[last[2]:end]
        if not "\n".join(find).strip():
            # A file that was empty, or a change at a blank place, has nothing
            # to find; it is not an edit the coder could apply.
            return [], []
        edits.append("<<<<<<< FIND\n" + "\n".join(find) + "\n=======\n" + "\n".join(put) + "\n>>>>>>> REPLACE")
        lo, hi = max(0, start - 10), min(len(old), end + 10)
        shown.append("\n".join(old[lo:hi]))
    return edits, shown


def example(request: str, files: list[tuple[str, str, str]]) -> dict | None:
    """One training example: what is asked and shown, and the answer."""
    asked, answer = [f"Task: {request.strip()}"], []
    for path, before, after in files:
        if before == after:
            continue
        if not before.strip():
            # A new file: asked for whole, as the coder does for a new file.
            asked.append(f"\n{path} does not exist yet: write it new.")
            answer.append(f"--- {path} (whole new file) ---\n{after}")
            continue
        edits, shown = blocks(before, after)
        if not edits:
            return None
        asked.append(f"\nThe parts of {path} the task is about; \"... (lines left out) ...\" stands for lines not shown:\n```\n"
                     + "\n... (lines left out) ...\n".join(shown) + "\n```")
        answer.append(f"--- {path} ---\n" + "\n".join(edits))
    text = "\n\n".join(answer)
    if not answer or len(text) > MOST_ANSWER:
        return None
    return {"messages": [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "\n".join(asked) + "\n\nChange the files with edits."},
        {"role": "assistant", "content": text},
    ]}


def from_history(ref: str) -> list[dict]:
    found = []
    for line in git("log", ref, "--no-merges", "--reverse", "--format=%H").split():
        message = git("log", "-1", "--format=%B", line).strip()
        paths = [p for p in git("diff-tree", "--no-commit-id", "--name-only", "-r", line).split("\n") if p]
        paths = [p for p in paths if p.endswith(KEPT) and not p.startswith(SKIPPED)]
        # The commit's own words, without the trailer lines.
        words = "\n".join(l for l in message.split("\n") if not l.startswith("Co-Authored-By")).strip()
        for path in paths:
            before = show(f"{line}^", path) or ""
            after = show(line, path)
            if after is None or before == after:
                continue
            for made in pieces(words, path, before, after):
                made["source"] = line[:12]
                found.append(made)
    return found


def pieces(words: str, path: str, before: str, after: str) -> list[dict]:
    """One file's change as examples: whole when it fits, else its edits in
    groups that each fit, every group applied on top of the ones before."""
    whole = example(f"{words}\n\nThis part of the change: {path}.", [(path, before, after)])
    if whole:
        return [whole]
    if not before.strip():
        return []
    old, new = before.split("\n"), after.split("\n")
    groups = list(difflib.SequenceMatcher(None, old, new, autojunk=False).get_grouped_opcodes(CONTEXT))
    made: list[dict] = []
    current = old[:]
    shift = 0
    for number, group in enumerate(groups, 1):
        first, last = group[0], group[-1]
        start, end = first[1] + shift, last[2] + shift
        step = current[:start] + new[first[3]:last[4]] + current[end:]
        one = example(f"{words}\n\nThis part of the change: {path}, step {number} of {len(groups)}.",
                      [(path, "\n".join(current), "\n".join(step))])
        if one:
            made.append(one)
        shift += (last[4] - first[3]) - (last[2] - first[1])
        current = step
    return made


def from_daily() -> list[dict]:
    found = []
    if not DAILY.exists():
        return found
    for line in DAILY.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        made = example(row["request"], [(f["path"], f["before"], f["after"]) for f in row["files"]])
        if made:
            made["source"] = "daily"
            found.append(made)
    return found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref", default="origin/studio")
    parser.add_argument("--out", default=str(Path.home() / "Aryan_Private" / "kosika-daily" / "training"))
    args = parser.parse_args()
    history = from_history(args.ref)
    held = max(1, len(history) // 10)
    train, evaluation = history[:-held] + from_daily(), history[-held:]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in (("kosika-coder.train.jsonl", train), ("kosika-coder.eval.jsonl", evaluation)):
        (out / name).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        size = sum(len(json.dumps(row)) for row in rows)
        print(f"{name}: {len(rows)} examples, {size // 1000} kB")


if __name__ == "__main__":
    main()
