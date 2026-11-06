"""What a program has learned: model answers it kept, so it can answer
again without the model.

A thinking step marked `learn` keeps every answer its model gave and the
program accepted, with what it was asked:

    reply = use_model voice(prompt.text) learn by msg.text otherwise ...
    kind  = classify t.text into ["bill", "bug"] learn

Approximate recall finds candidates among the answers kept:

- the same question (the same words, ignoring case and punctuation) is
  answered with the answer kept for it;
- a question that differs by a word or two from a kept one, when nearly
  all its words are the same, is answered like it;
- a question that means what a kept one means, written in other words, is
  answered like it when an embedding model is granted (`--embed-model`):
  the two are compared as vectors, and nothing is asked of a model at
  run time that the store has not already been told;
- a `classify` or `decide` step, once it has kept enough examples, answers
  by itself when the examples most like the question agree on one answer.

Runtime reuse requires an exact full-request identity, unless the step
is written `learn near`: then an answer kept for the same inputs may also
be given to a question that reads differently, never to one carrying other
figures (`+15` for `-15`) or other words in capitals (`US` for `us`), and
never to one asked about other inputs. Otherwise the model is asked and its
answer is kept.
A step with no connected model runs its `otherwise` for an unseen request.

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
#: How near two questions must be in meaning, as a cosine of their
#: vectors, for one's answer to be given to the other. Set to favour
#: precision over recall until a task set calibrates it; a wrong answer
#: given confidently is worse than a model asked.
SEMANTIC_ENOUGH = 0.8

_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def words_of(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _likeness(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


_NUMBER = re.compile(r"[+-]?\d+(?:[.,]\d+)?")


def _figures(text: str) -> tuple[str, ...]:
    """The figures a question carries, signs and decimal marks kept.

    Words may be said another way; a figure may not: `+15` and `-15` are
    different questions, and so are `150` and `15`.
    """

    return tuple(number.replace(",", ".") for number in _NUMBER.findall(text))


def _caps(text: str) -> tuple[tuple[int, str], ...]:
    """Where a question writes words in capitals, such as `US`, and which.

    `US` and `us` are different words, and case is how a reader tells them
    apart, so a near question may not move that case onto another word:
    `compare US and us` and `compare us and US` keep different capitals.
    """

    return tuple(
        (position, word.lower())
        for position, word in enumerate(_WORD.findall(text))
        if len(word) > 1 and word.isupper()
    )


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class Lessons:
    """The lessons of every learning step of one program, kept in `folder`."""

    def __init__(self, folder: str | Path, meaning: Any | None = None):
        self.folder = Path(folder)
        #: The grant that turns questions into vectors, or None. Without it
        #: a lesson is matched by its words alone, as before.
        self.meaning = meaning
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

    def recall_exact(self, step: str, request: str) -> dict[str, Any] | None:
        digest = hashlib.sha256(request.encode("utf-8")).hexdigest()
        with self._lock:
            book = self._book(step)
            for item in reversed(book):
                if item.get("request_hash") == digest:
                    return {"answer": item["answer"], "how": "same", "likeness": 1.0, "lessons": len(book)}
        return None

    def recall(
        self,
        step: str,
        question: str,
        *,
        voting: bool,
        context: str | None = None,
        same_figures: bool = False,
    ) -> dict[str, Any] | None:
        """The answer learned for `question`, with how it was found, or None
        when the model must be asked.

        `context` holds a recall to the lessons kept for those inputs, so a
        caller that offers approximate matching can never cross from one set
        of inputs to another. Without it every lesson is a candidate, which is
        what the tool and Studio readers have always done.

        `same_figures` admits only lessons whose question carries the same
        numbers, signs and words in capitals: what `learn near` may reword,
        it may not renumber.
        """

        asked = words_of(question)
        plain = " ".join(asked)
        figures = _figures(question) if same_figures else None
        caps = _caps(question) if same_figures else None
        with self._lock:
            book = [
                item
                for item in self._book(step)
                if (context is None or item.get("context") == context)
                and (
                    figures is None
                    or (
                        _figures(item.get("question", "")) == figures
                        and _caps(item.get("question", "")) == caps
                    )
                )
            ]
        if not book:
            return None
        for item in reversed(book):
            if item.get("plain") == plain:
                return {"answer": item["answer"], "how": "same", "likeness": 1.0, "lessons": len(book)}
        by_meaning = self._by_meaning(step, question, book)
        if by_meaning is not None:
            return by_meaning
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
        votes: Counter[str] = Counter()  # type: ignore[assignment]
        answers: dict[str, Any] = {}
        weight = 0.0
        for score, _, item in ranked[:VOTE_NEIGHBOURS]:
            if score <= 0:
                continue
            answer = item["answer"]
            key = _canonical(_without_confidence(answer))
            votes[key] += score  # type: ignore[assignment]
            answers[key] = answer
            weight += score
        if not votes or weight == 0:
            return None
        winner, score = votes.most_common(1)[0]
        agreement = score / weight  # type: ignore[assignment]
        if agreement < VOTE_AGREEMENT:
            return None
        answer = dict(answers[winner]) if isinstance(answers[winner], dict) else answers[winner]
        if isinstance(answer, dict) and "confidence" in answer:
            answer["confidence"] = round(agreement, 3)
        return {"answer": answer, "how": "examples", "likeness": round(likeness, 3), "lessons": len(book)}

    def _by_meaning(
        self, step: str, question: str, book: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """The kept question nearest in meaning, or None when none is near.

        Vectors are made by the granted embedding model and kept in its own
        store, so a question already asked costs no model call; one never
        asked before is embedded once. A model that cannot be reached is
        not a failure: the words are compared instead.
        """

        if self.meaning is None or not book:
            return None
        try:
            scored = self.meaning.nearest(
                question, [item.get("question", "") for item in book]
            )
        except (RuntimeError, OSError, ValueError):
            return None
        if not scored:
            return None
        likeness, position = scored[0]
        if likeness < SEMANTIC_ENOUGH or not 0 <= position < len(book):
            return None
        item = book[position]
        return {
            "answer": item["answer"],
            "how": "meaning",
            "likeness": round(float(likeness), 3),
            "lessons": len(book),
        }

    def keep(
        self,
        step: str,
        question: str,
        answer: Any,
        *,
        request: str | None = None,
        context: str | None = None,
    ) -> None:
        """Keep a model's accepted answer to `question`.

        `context` is what the answer was computed for, apart from the
        question: a lesson is reused only for the same context, however alike
        two questions read.
        """

        plain = " ".join(words_of(question))
        if not plain and request is None:
            return
        key = question if request is None else request
        if request is None and context is not None:
            # The same question asked with different inputs is a different
            # lesson, so one does not overwrite the other.
            key = json.dumps([question, context], ensure_ascii=False)
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        with self._lock:
            book = self._book(step)
            book[:] = [item for item in book if item.get("request_hash") != digest]
            book.append({
                "question": question[:2000], "plain": plain[:2000],
                "context": context,
                "request_hash": digest, "answer": answer, "at": round(time.time()),
            })
            del book[:-MAX_LESSONS]
            self._save(step, book)

    def _save(self, step: str, book: list[dict[str, Any]]) -> None:
        path = self._path(step)
        path.parent.mkdir(parents=True, exist_ok=True)
        spare = path.with_suffix(".tmp")
        spare.write_text(json.dumps(book, ensure_ascii=False, indent=1), encoding="utf-8")
        spare.replace(path)

    def correct(
        self, step: str, question: str, answer: Any, *, request: str | None = None
    ) -> bool:
        """Replace the answer kept for `question`, or keep it when none is.

        A lesson is found the way `recall` would find it: by the words of its
        question, or by an exact request when one is given. True says a kept
        answer was replaced; False says the answer was new and is now kept.
        """

        plain = " ".join(words_of(question))
        digest = hashlib.sha256(
            (question if request is None else request).encode("utf-8")
        ).hexdigest()
        with self._lock:
            book = self._book(step)
            for item in reversed(book):
                matched = (
                    item.get("request_hash") == digest
                    if request is not None
                    else item.get("plain") == plain
                )
                if matched:
                    item["answer"] = answer
                    item["at"] = round(time.time())
                    self._save(step, book)
                    return True
        self.keep(step, question, answer, request=request)
        return False

    def forget(
        self, step: str, question: str, *, request: str | None = None
    ) -> int:
        """Drop every lesson kept for `question`; says how many went.

        Nothing is written when there was nothing to drop, and a forgotten
        question is asked of the model again rather than answered from a
        lesson nobody trusts.
        """

        plain = " ".join(words_of(question))
        digest = hashlib.sha256(
            (question if request is None else request).encode("utf-8")
        ).hexdigest()
        with self._lock:
            book = self._book(step)
            kept = [
                item
                for item in book
                if not (
                    item.get("request_hash") == digest
                    if request is not None
                    else item.get("plain") == plain
                )
            ]
            dropped = len(book) - len(kept)
            if dropped:
                book[:] = kept
                self._save(step, book)
            return dropped


def census(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Count the model answers in a trace by where each one came from.

    A lesson answer is recorded as a model answer with `learned`, so the
    trace alone says how many model steps a run had and how many of them
    the lessons answered, and in which way.
    """

    steps = 0
    from_lessons = 0
    how: Counter[str] = Counter()
    for event in events:
        if not isinstance(event, dict) or event.get("event") != "model_return":
            continue
        steps += 1
        learned = event.get("learned")
        if isinstance(learned, dict):
            from_lessons += 1
            how[str(learned.get("how") or "unknown")] += 1
    return {
        "model_steps": steps,
        "from_lessons": from_lessons,
        "live_model": steps - from_lessons,
        "lesson_ratio": round(from_lessons / steps, 4) if steps else None,
        "how": dict(sorted(how.items())),
    }


def _without_confidence(answer: Any) -> Any:
    if isinstance(answer, dict):
        return {key: value for key, value in answer.items() if key != "confidence"}
    return answer
