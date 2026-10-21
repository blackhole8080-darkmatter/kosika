"""What a program has learned: model answers it kept, so it can answer
again without the model.

A thinking step marked `learn` keeps every answer its model gave and the
program accepted, with what it was asked:

    reply = use_model voice(prompt.text) learn by msg.text otherwise ...
    kind  = classify t.text into ["bill", "bug"] learn

Before asking the model again, the step looks at what it has kept:

- the same question (the same words, ignoring case and punctuation) is
  answered with the answer kept for it;
- a question that differs by a word or two from a kept one, when nearly
  all its words are the same, is answered like it;
- a `classify` or `decide` step, once it has kept enough examples, answers
  by itself when the examples most like the question agree on one answer.

Only when none of these holds is the model asked, and its answer is kept
in turn. A step that learns may run with no model at all: it answers what
it has learned and, for anything else, fails the way a model that cannot
be reached fails, so its `otherwise` runs.

Lessons live in a folder the person chose (the memory folder), one file
for each step, as plain JSON they can read or delete. Without a folder a
step learns nothing and every question goes to the model, exactly as
without `learn`. What was learned is part of the trace: an answer given
from a lesson is recorded as the model's answer, with where it came from,
so a run replays the same with or without the lessons.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any

#: Lessons kept for one step; the oldest go first.
MAX_LESSONS = 500
#: How alike two questions must be, by the share of their words they have
#: in common, for one's answer to be given to the other.
CLOSE_ENOUGH = 0.88
#: A `classify` or `decide` step answers by itself only with at least this
#: many examples, when the examples nearest the question agree this much.
VOTE_EXAMPLES = 6
VOTE_NEIGHBOURS = 7
VOTE_AGREEMENT = 0.8
VOTE_NEAREST = 0.4

_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def words_of(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _likeness(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class Lessons:
    """The lessons of every learning step of one program, kept in `folder`."""

    def __init__(self, folder: str | Path):
        self.folder = Path(folder)
        self._lock = threading.Lock()
        self._loaded: dict[str, list[dict[str, Any]]] = {}

    def _path(self, step: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", step)[:80]
        digest = hashlib.sha256(step.encode("utf-8")).hexdigest()[:10]
        return self.folder / "lessons" / f"{safe}-{digest}.json"

    def _book(self, step: str) -> list[dict[str, Any]]:
        if step not in self._loaded:
            try:
                kept = json.loads(self._path(step).read_text(encoding="utf-8"))
                self._loaded[step] = [item for item in kept if isinstance(item, dict)]
            except (OSError, ValueError):
                self._loaded[step] = []
        return self._loaded[step]

    def adopt(self, earlier: str, step: str) -> None:
        """Move lessons kept under an earlier name of a step to its name now,
        when it has none yet."""

        if earlier == step:
            return
        with self._lock:
            old, new = self._path(earlier), self._path(step)
            if old.is_file() and not new.exists():
                try:
                    old.replace(new)
                except OSError:
                    return
                self._loaded.pop(step, None)
                self._loaded.pop(earlier, None)

    def count(self, step: str) -> int:
        with self._lock:
            return len(self._book(step))

    def recall(self, step: str, question: str, *, voting: bool) -> dict[str, Any] | None:
        """The answer learned for `question`, with how it was found, or None
        when the model must be asked."""

        asked = words_of(question)
        plain = " ".join(asked)
        with self._lock:
            book = list(self._book(step))
        if not book:
            return None
        for item in reversed(book):
            if item.get("plain") == plain:
                return {"answer": item["answer"], "how": "same", "likeness": 1.0, "lessons": len(book)}
        mine = set(asked)
        ranked = sorted(
            ((_likeness(mine, set(item.get("plain", "").split())), index, item) for index, item in enumerate(book)),
            key=lambda found: (found[0], found[1]),
            reverse=True,
        )
        likeness, _, nearest = ranked[0]
        if likeness >= CLOSE_ENOUGH and len(mine) >= 3:
            return {"answer": nearest["answer"], "how": "close", "likeness": round(likeness, 3), "lessons": len(book)}
        if not voting or len(book) < VOTE_EXAMPLES or likeness < VOTE_NEAREST:
            return None
        votes: Counter[str] = Counter()
        answers: dict[str, Any] = {}
        weight = 0.0
        for score, _, item in ranked[:VOTE_NEIGHBOURS]:
            if score <= 0:
                continue
            answer = item["answer"]
            key = _canonical(_without_confidence(answer))
            votes[key] += score
            answers[key] = answer
            weight += score
        if not votes or weight == 0:
            return None
        winner, score = votes.most_common(1)[0]
        agreement = score / weight
        if agreement < VOTE_AGREEMENT:
            return None
        answer = dict(answers[winner]) if isinstance(answers[winner], dict) else answers[winner]
        if isinstance(answer, dict) and "confidence" in answer:
            answer["confidence"] = round(agreement, 3)
        return {"answer": answer, "how": "examples", "likeness": round(likeness, 3), "lessons": len(book)}

    def keep(self, step: str, question: str, answer: Any) -> None:
        """Keep a model's accepted answer to `question`."""

        plain = " ".join(words_of(question))
        if not plain:
            return
        with self._lock:
            book = self._book(step)
            book[:] = [item for item in book if item.get("plain") != plain]
            book.append({"question": question[:2000], "plain": plain[:2000], "answer": answer, "at": round(time.time())})
            del book[:-MAX_LESSONS]
            path = self._path(step)
            path.parent.mkdir(parents=True, exist_ok=True)
            spare = path.with_suffix(".tmp")
            spare.write_text(json.dumps(book, ensure_ascii=False, indent=1), encoding="utf-8")
            spare.replace(path)


def _without_confidence(answer: Any) -> Any:
    if isinstance(answer, dict):
        return {key: value for key, value in answer.items() if key != "confidence"}
    return answer
