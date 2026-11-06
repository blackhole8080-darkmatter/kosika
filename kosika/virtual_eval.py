"""Qualifying a virtual model against recorded ground truth.

A virtual model here is not a set of weights. It is three things bound
together: a profile (instructions, sources, verified examples), the model the
host chose to answer for it, and the evidence that this combination answered a
fixed question set well enough to be trusted. Without the third, "a virtual
model" is a configuration file and a hope.

This module supplies the third. It reads a recorded benchmark run, counts what
the run's own judge already decided (right, wrong shown, missed, failed),
derives an identity from the combination, and answers one question plainly:
may this combination be used?

Two deliberate limits:

* It re-reads the judge's recorded verdicts; it does not re-judge the answers.
  Re-judging belongs to the benchmark, and a qualification that quietly used a
  different judge than the run would not be evidence of anything.
* An identity excludes the human label and the timestamp, because those
  describe a particular run rather than the combination being qualified. It
  includes the profile digest, so editing the profile invalidates an earlier
  pass instead of inheriting it.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

EVAL_SCHEMA = "kosika.virtual-model-eval/v1"
LEDGER_SCHEMA = "kosika.virtual-model-ledger/v1"

#: What the benchmark's judge can decide about one answer.
CLASSES = ("right", "wrong shown", "missed", "failed")

MAX_RESULTS_BYTES = 8_000_000
MAX_QUESTIONS = 2_000
MAX_OUTCOMES = 16
MAX_LEDGER_ENTRIES = 512


class VirtualEvalError(ValueError):
    """A recorded run, a ledger, or an evaluation request cannot be used."""


@dataclass(frozen=True)
class RecordedRun:
    """One benchmark run, as it was recorded."""

    source: str
    model: str
    embed_model: str
    label: str
    question_set: str
    when: str
    asked: int
    counts: Mapping[str, int]


@dataclass(frozen=True)
class Score:
    """What a recorded run says about one virtual-model combination."""

    identity: str
    run: RecordedRun

    @property
    def right(self) -> int:
        return self.run.counts["right"]

    @property
    def wrong(self) -> int:
        return self.run.counts["wrong shown"]

    @property
    def missed(self) -> int:
        return self.run.counts["missed"]

    @property
    def failed(self) -> int:
        return self.run.counts["failed"]

    @property
    def asked(self) -> int:
        return self.run.asked

    @property
    def accuracy(self) -> float:
        return self.right / self.asked if self.asked else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": EVAL_SCHEMA,
            "identity": self.identity,
            "source": self.run.source,
            "model": self.run.model,
            "embed_model": self.run.embed_model,
            "label": self.run.label,
            "set": self.run.question_set,
            "when": self.run.when,
            "asked": self.asked,
            "counts": dict(self.run.counts),
            "accuracy": round(self.accuracy, 4),
            "honest": self.wrong == 0,
        }


def _object(payload: Any, where: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise VirtualEvalError(f"{where} must be a JSON object")
    return payload


def _text(payload: Mapping[str, Any], key: str, where: str, *, default: str = "") -> str:
    value = payload.get(key, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise VirtualEvalError(f"{where} field {key!r} must be text")
    if len(value) > 200:
        raise VirtualEvalError(f"{where} field {key!r} is longer than 200 characters")
    return value


def load_recorded(path: str | Path) -> RecordedRun:
    """Read a recorded benchmark run and count its judge's decisions.

    The counts are taken from each answer's recorded outcome rather than
    recomputed from the saved text, so a run with several attempts per
    question counts every attempt.
    """

    source = Path(path)
    try:
        if source.stat().st_size > MAX_RESULTS_BYTES:
            raise VirtualEvalError("Recorded run exceeds 8 MB")
        payload = json.loads(source.read_text(encoding="utf-8"))
    except VirtualEvalError:
        raise
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise VirtualEvalError(f"Cannot read recorded run: {exc}") from exc
    record = _object(payload, "A recorded run")
    model = _text(record, "model", "A recorded run")
    if not model.strip():
        raise VirtualEvalError("A recorded run must name the model it used")
    questions = record.get("questions")
    if not isinstance(questions, list) or not questions:
        raise VirtualEvalError("A recorded run must hold at least one question")
    if len(questions) > MAX_QUESTIONS:
        raise VirtualEvalError(f"A recorded run may hold at most {MAX_QUESTIONS} questions")
    counts = dict.fromkeys(CLASSES, 0)
    asked = 0
    for index, question in enumerate(questions):
        where = f"Question {index + 1}"
        item = _object(question, where)
        outcomes = item.get("outcomes")
        if not isinstance(outcomes, list) or not outcomes:
            raise VirtualEvalError(f"{where} has no recorded outcome")
        if len(outcomes) > MAX_OUTCOMES:
            raise VirtualEvalError(
                f"{where} has more than {MAX_OUTCOMES} recorded outcomes"
            )
        for outcome in outcomes:
            if outcome not in counts:
                raise VirtualEvalError(
                    f"{where} records {outcome!r}, which is not one of "
                    + ", ".join(CLASSES)
                )
            counts[outcome] += 1
            asked += 1
    return RecordedRun(
        source=source.as_posix(),
        model=model.strip(),
        embed_model=_text(record, "embed_model", "A recorded run"),
        label=_text(record, "label", "A recorded run"),
        question_set=_text(record, "set", "A recorded run", default="first"),
        when=_text(record, "when", "A recorded run"),
        asked=asked,
        counts=counts,
    )


def file_digest(path: str | Path) -> str:
    """The SHA-256 of a profile file's bytes.

    This is the same digest :class:`kosika.virtual_model.VirtualModelProvider`
    reports, so a qualification and a served profile agree on identity.
    """

    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise VirtualEvalError(f"Cannot read profile: {exc}") from exc
    if len(raw) > 256_000:
        raise VirtualEvalError("Profile exceeds 256 KB")
    return hashlib.sha256(raw).hexdigest()


def identity_of(run: RecordedRun, *, profile: str | None = None) -> str:
    """The combination being qualified: profile, model, and question set."""

    payload = {
        "schema": EVAL_SCHEMA,
        "profile": profile or "",
        "model": run.model,
        "embed_model": run.embed_model,
        "set": run.question_set,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def score(
    path: str | Path, *, profile: str | Path | None = None
) -> Score:
    """Read one recorded run and bind it to the combination it evidences."""

    run = load_recorded(path)
    digest = file_digest(profile) if profile is not None else None
    return Score(identity=identity_of(run, profile=digest), run=run)


def qualify(
    score: Score,
    *,
    min_right: float | None = None,
    require_no_wrong: bool = True,
    require_no_failure: bool = True,
) -> tuple[bool, list[str]]:
    """Whether this combination may be used, and every reason it may not.

    A wrong answer shown to a user is the failure the whole arrangement exists
    to prevent, so it is refused by default. A failed run is refused because an
    unfinished run says nothing about the questions it did not reach.
    """

    reasons: list[str] = []
    if score.asked == 0:
        reasons.append("no answers were recorded, so nothing is evidenced")
    if require_no_wrong and score.wrong:
        reasons.append(f"{score.wrong} wrong answer(s) were shown to a user")
    if require_no_failure and score.failed:
        reasons.append(f"{score.failed} answer(s) failed to run")
    if min_right is not None and score.accuracy < min_right:
        reasons.append(
            f"right answers are {score.accuracy:.3f} of {score.asked}, "
            f"below the floor {min_right:.3f}"
        )
    return (not reasons), reasons


def render_text(score: Score, *, reasons: Sequence[str] = ()) -> str:
    """A short human summary: what was asked, what it got, what it refused."""

    run = score.run
    lines = [
        f"virtual model : {run.model}"
        + (f" + {run.embed_model}" if run.embed_model else ""),
        f"questions     : {run.question_set} ({run.asked} answers"
        + (f", label {run.label}" if run.label else "")
        + ")",
        f"identity      : {score.identity[:16]}",
        "",
        f"  right        {score.right:>4}",
        f"  wrong shown  {score.wrong:>4}",
        f"  missed       {score.missed:>4}",
        f"  failed       {score.failed:>4}",
        f"  accuracy     {score.accuracy:>7.3f}",
    ]
    if reasons:
        lines.append("")
        lines.extend(f"  refused: {reason}" for reason in reasons)
    return "\n".join(lines)


def read_ledger(state: str | Path) -> list[dict[str, Any]]:
    """Read the recorded evaluations, if any."""

    path = Path(state)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise VirtualEvalError(f"Cannot read the evaluation ledger: {exc}") from exc
    record = _object(payload, "The evaluation ledger")
    if record.get("schema") != LEDGER_SCHEMA:
        raise VirtualEvalError(f"The ledger must use schema {LEDGER_SCHEMA}")
    entries = record.get("evaluations")
    if not isinstance(entries, list) or not all(
        isinstance(item, dict) for item in entries
    ):
        raise VirtualEvalError("The ledger evaluations must be a list of objects")
    return entries


def record_eval(state: str | Path, score: Score) -> dict[str, Any] | None:
    """Keep this evaluation, returning the previous one for the same identity.

    The write is atomic and the file is private to the person running it, so a
    half-written ledger can never be read as evidence.
    """

    path = Path(state)
    entries = read_ledger(path)
    previous = next(
        (item for item in reversed(entries) if item.get("identity") == score.identity),
        None,
    )
    entries.append(score.as_dict())
    if len(entries) > MAX_LEDGER_ENTRIES:
        entries = entries[-MAX_LEDGER_ENTRIES:]
    payload = {"schema": LEDGER_SCHEMA, "evaluations": entries}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise VirtualEvalError(f"Could not record the evaluation: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return previous


def compare(previous: Mapping[str, Any], score: Score) -> list[str]:
    """What changed against the last recorded evaluation of this identity."""

    changes: list[str] = []
    before = previous.get("counts")
    after = dict(score.run.counts)
    if isinstance(before, dict):
        for name in CLASSES:
            old, new = before.get(name), after.get(name)
            if isinstance(old, int) and old != new:
                changes.append(f"{name} {old} -> {new}")
    return changes


__all__ = [
    "CLASSES",
    "EVAL_SCHEMA",
    "LEDGER_SCHEMA",
    "RecordedRun",
    "Score",
    "VirtualEvalError",
    "compare",
    "file_digest",
    "identity_of",
    "load_recorded",
    "qualify",
    "read_ledger",
    "record_eval",
    "render_text",
    "score",
]
