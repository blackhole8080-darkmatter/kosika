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
import re
import subprocess
from collections import defaultdict
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


# Lines of a commit message that say nothing about the change: tool
# signatures and trailers.
NOISE = re.compile(r"Generated with|Co-Authored-By|^\s*(?:🤖|👾)|claude\.com|letta\.com", re.I)
# A line that starts a definition, in any of the languages Kosika is written
# in, and the name it defines.
DEFINES = re.compile(
    r"^\s*(?:async\s+)?(?:def|class|func(?:\s*\([^)]*\))?|fn|record|workflow|tool|agent|model|test|type|shape|choice)\s+\"?([A-Za-z_][\w.$]*)"
    r"|^(#{1,4})\s+(.+)$")
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def plain_words(message: str) -> str:
    """A commit message without the lines that are not about the change."""
    return "\n".join(line for line in message.split("\n") if not NOISE.search(line)).strip()


def enclosing(lines: list[str], at: int) -> str | None:
    """The name of the definition (or Markdown heading) line `at` is in."""
    for line in reversed(lines[: at + 1]):
        found = DEFINES.match(line)
        if found:
            return found.group(1) or found.group(3).strip()
    return None


def focus(before: str, after: str) -> str:
    """What a person asking for this one edit would say about where and
    what: the definitions it is in, and the new names it brings in. Real
    requests to the coder name these; a commit's message often does not."""
    old, new = before.split("\n"), after.split("\n")
    places: list[str] = []
    added: list[str] = []
    known = set(WORD.findall(before))
    for tag, a1, a2, b1, b2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        place = enclosing(old, a1 if a1 < len(old) else len(old) - 1) if old else None
        if place and place not in places:
            places.append(place)
        for line in new[b1:b2]:
            defined = DEFINES.match(line)
            if defined and (defined.group(1) or "") and defined.group(1) not in places:
                places.append(defined.group(1))
            for word in WORD.findall(line):
                if word not in known and word not in added:
                    added.append(word)
    said = []
    if places:
        said.append("Where: " + ", ".join(f"`{p}`" for p in places[:4]) + ".")
    if added:
        said.append("New names: " + ", ".join(f"`{w}`" for w in added[:8]) + ".")
    return " ".join(said)


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


def from_history(ref: str, hints: bool = False, per_commit: int = 0) -> list[dict]:
    found = []
    for line in git("log", ref, "--no-merges", "--reverse", "--format=%H").split():
        message = git("log", "-1", "--format=%B", line).strip()
        paths = [p for p in git("diff-tree", "--no-commit-id", "--name-only", "-r", line).split("\n") if p]
        paths = [p for p in paths if p.endswith(KEPT) and not p.startswith(SKIPPED)]
        # The commit's own words, without the trailer lines.
        words = "\n".join(l for l in message.split("\n") if not l.startswith("Co-Authored-By")).strip()
        if hints:
            words = plain_words(message)
        made_here: list[dict] = []
        for path in paths:
            before = show(f"{line}^", path) or ""
            after = show(line, path)
            if after is None or before == after:
                continue
            for made in pieces(words, path, before, after, hints):
                made["source"] = line[:12]
                made_here.append(made)
        if per_commit and len(made_here) > per_commit:
            # One large commit must not be most of what is learned: keep an
            # even spread of its pieces.
            stride = len(made_here) / per_commit
            made_here = [made_here[int(i * stride)] for i in range(per_commit)]
        found.extend(made_here)
    return found


def pieces(words: str, path: str, before: str, after: str, hints: bool = False) -> list[dict]:
    """One file's change as examples: whole when it fits, else its edits in
    groups that each fit, every group applied on top of the ones before.
    With hints, each says where the edit goes and the names it brings in."""
    def said(part: str, b: str, a: str) -> str:
        hint = focus(b, a) if hints else ""
        return f"{words}\n\nThis part of the change: {part}." + (f"\n{hint}" if hint else "")

    whole = example(said(path, before, after), [(path, before, after)])
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
        one = example(said(f"{path}, step {number} of {len(groups)}", "\n".join(current), "\n".join(step)),
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
    parser.add_argument("--hints", action="store_true",
                        help="say where each edit goes and the names it brings in, without tool trailers")
    parser.add_argument("--per-commit", type=int, default=0, help="keep at most this many pieces of one commit")
    parser.add_argument("--name", default="kosika-coder", help="the files' name: NAME.train.jsonl, NAME.eval.jsonl")
    args = parser.parse_args()
    history = from_history(args.ref, args.hints, args.per_commit)
    held = max(1, len(history) // 10)
    train, evaluation = history[:-held] + from_daily(), history[-held:]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in ((f"{args.name}.train.jsonl", train), (f"{args.name}.eval.jsonl", evaluation)):
        (out / name).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        size = sum(len(json.dumps(row)) for row in rows)
        print(f"{name}: {len(rows)} examples, {size // 1000} kB")


if __name__ == "__main__":
    main()
