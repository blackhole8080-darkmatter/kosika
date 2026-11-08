"""Does the answer stand on the line it cites?

This is the smallest useful form of a "virtual model made of software": the
model still writes the words, but a rule written once decides whether those
words are allowed to reach a person. One rule, no training, no quota.

The rule, in full:

1. The answer must cite something: ``From: <file>, line <n>``.
2. That file must be in the notes folder and that line must exist.
3. If the answer quotes a line (``Quote: "..."``), that quote must be on the
   cited line. A quote from nowhere is a false answer no matter how the rest
   reads.
4. Every content word of the claim -- everything but "the", "is", "of" and
   friends -- must appear in the cited line. Two words count as the same when
   they are equal, or when one begins the other and the shorter is at least
   four letters, so "euro"/"euros" and "leave"/"leaves" match while "car" and
   "card" do not. Numbers must be equal: "900" is not "90".
5. Anything left over means the claim says more than the line says, and the
   answer is replaced by a refusal.

That last step is the whole idea: a false answer becomes an honest miss. In
this benchmark a miss costs nothing but an unused opportunity; a shown false
answer is the harmful kind.

It runs on a recorded benchmark result, so every answer below is what the
model really said, not a demonstration written to flatter the check:

    python examples/grounding/check.py
    python examples/grounding/check.py --set unseen --results <file.json>
    python examples/grounding/check.py -v          # every question, not just changes

The judging is the benchmark's own (benchmarks/researcher/run.py), loaded from
that file rather than copied, so "before" and "after" are measured the same way
the project already measures them.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BENCH = REPO / "benchmarks" / "researcher"
DEFAULT_RESULT = BENCH / "results" / "20261010-205359_qwen2.5-1.5b_fixed.json"

#: What an unsupported answer is replaced with. The benchmark counts a reply
#: beginning like this as a refusal, which is exactly what it is.
DECLINE = "I will not guess: the notes I was given do not say this."

#: Words that carry no claim of their own, so their absence proves nothing.
STOP = frozenset(
    "a an the is are was were be been being of to in on at for with and or but "
    "it its this that these those i my me you your we our they their he she his "
    "her from by as not no do does did have has had can could will would should "
    "may might must than then there here what when where which who whom whose "
    "how why so if about into over under more most also just very".split()
)

_WORD = re.compile(r"[a-z0-9]+")
_QUOTE = re.compile(r'Quote:\s*"(.+?)"', re.S)
#: A recorded quote is sometimes prefixed with where it came from, as in
#: "health.md:3: Dentist appointment ...". The prefix is not part of the line.
_QUOTE_PREFIX = re.compile(r"^[\w./\\-]+:\d+:\s*")
_CITED = re.compile(r"From:\s*([^,\n]+),\s*line\s*(\d+)")


@dataclass(frozen=True)
class Verdict:
    """What the rule decided, and why, in words a person can argue with."""

    grounded: bool
    reason: str
    claim: str
    cited: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "grounded": self.grounded,
            "reason": self.reason,
            "claim": self.claim,
            "cited": self.cited,
        }


def words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def content(text: str) -> list[str]:
    return [word for word in words(text) if word not in STOP and len(word) > 1]


def supports(word: str, other: str) -> bool:
    """Whether ``other`` may stand for ``word``: equal, or one begins the other."""

    if word.isdigit() or other.isdigit():
        return word == other
    if word == other:
        return True
    shorter, longer = sorted((word, other), key=len)
    return len(shorter) >= 4 and longer.startswith(shorter)


def parse_answer(said: str) -> tuple[str, str, str | None, int | None]:
    """Split an answer into what it claims, what it quotes, and what it cites."""

    claim = said.split("\nQuote:")[0].strip()
    quote = ""
    found = _QUOTE.search(said)
    if found is not None:
        quote = _QUOTE_PREFIX.sub("", found.group(1).strip())
    cited = _CITED.search(said)
    if cited is None:
        return claim, quote, None, None
    return claim, quote, cited.group(1).strip(), int(cited.group(2))


def appears(quote: str, cited: str) -> bool:
    """Whether the quote is a run of words on the cited line."""

    quoted, line = words(quote), words(cited)
    if not quoted or not line:
        return False
    span = len(quoted)
    return any(
        line[start : start + span] == quoted for start in range(len(line) - span + 1)
    )


def check(said: str, notes: Path) -> Verdict:
    """The rule above, applied to one answer."""

    claim, quote, cited_file, line = parse_answer(said)
    if cited_file is None or line is None:
        return Verdict(False, "cites nothing", claim)
    try:
        lines = (notes / cited_file).read_text(encoding="utf-8").split("\n")
    except OSError:
        return Verdict(False, f"cited file {cited_file} is not in the notes", claim)
    if not 1 <= line <= len(lines):
        return Verdict(False, f"line {line} is not in {cited_file}", claim)
    cited = lines[line - 1]
    if quote and not appears(quote, cited):
        return Verdict(False, "the quote is not on the cited line", claim, cited)
    pool = words(cited) + words(quote)
    missing = [
        word
        for word in content(claim)
        if not any(supports(word, other) for other in pool)
    ]
    if missing:
        unique = list(dict.fromkeys(missing))
        return Verdict(
            False, "not on the cited line: " + ", ".join(unique), claim, cited
        )
    return Verdict(True, "every word of the claim is on the cited line", claim, cited)


def load_judge():
    """The benchmark's own judge, from its file, so nothing is re-implemented."""

    spec = importlib.util.spec_from_file_location("researcher_run", BENCH / "run.py")
    if spec is None or spec.loader is None:
        raise SystemExit(f"Cannot load the benchmark's judge from {BENCH / 'run.py'}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.judge


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--set", choices=("first", "unseen"), default="first")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    judge = load_judge()
    payload = json.loads(args.results.read_text(encoding="utf-8"))
    notes = BENCH / "notes" if args.set == "first" else BENCH / "unseen" / "notes"
    before: Counter[str] = Counter()
    after: Counter[str] = Counter()
    changed = 0
    lost = 0

    print(f"model   : {payload.get('model', '?')}   label: {payload.get('label', '')}")
    resolved = args.results.resolve()
    try:
        shown = resolved.relative_to(REPO.resolve()).as_posix()
    except ValueError:
        shown = resolved.as_posix()
    print(f"result  : {shown}")
    print(f"rule    : {DECLINE}")
    print()

    for question in payload["questions"]:
        said = (question.get("said") or [""])[0]
        if not said:
            before["failed"] += 1
            after["failed"] += 1
            continue
        was = judge(question, said)
        verdict = check(said, notes)
        now = judge(question, said if verdict.grounded else DECLINE)
        before[was] += 1
        after[now] += 1
        if was != now:
            changed += 1
            if was == "right":
                lost += 1
        if not (args.verbose or was != now):
            continue
        mark = "kept   " if verdict.grounded else "DECLINE"
        print(f"[{question['kind']:<7}] {mark}  {question['question']}")
        print(f"          model said : {verdict.claim[:160]}")
        if verdict.cited:
            print(f"          cited line : {verdict.cited.strip()[:160]}")
        print(f"          rule says  : {verdict.reason}")
        print(f"          judged     : {was} -> {now}")
        print()

    def row(counts: Counter[str]) -> str:
        return (
            f"right {counts['right']:>2}   false shown {counts['wrong shown']:>2}   "
            f"declined {counts['missed']:>2}   failed {counts['failed']:>2}"
        )

    print(f"answered as recorded                  {row(before)}")
    print(f"with the grounding rule applied       {row(after)}")
    print()
    print(f"answers changed: {changed}   of those, right answers lost: {lost}")
    print(
        "false answers removed: "
        f"{before['wrong shown'] - after['wrong shown']}   "
        f"honest declines added: {after['missed'] - before['missed']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
