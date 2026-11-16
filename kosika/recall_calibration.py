"""Measuring how far a question may be from a kept one and still be answered.

`Lessons.recall` answers without a model only when the question looks enough
like one already answered. "Enough" is a threshold: `CLOSE_ENOUGH` for the
share of words two questions have in common, `SEMANTIC_ENOUGH` for the cosine
of their vectors. A threshold chosen by taste is a guess; this measures it.

A pairs file holds questions written to mean what a reference question means,
and questions that only look close. For each threshold the sweep counts what
would have been answered rightly and what wrongly, so the report shows the
trade: reaching more paraphrases also reaches more near misses.

The report also says where the measure itself gives out: how far apart the
scores of questions that mean it and questions that do not actually sit, what
each kind of pair survives, and how much can be reached at a given precision.
Four lexical measures are compared on the same pairs, so a poor showing can be
read as a property of words rather than of the one Kosika happens to ship.

Recall is what asking the model costs; precision is what a wrong answer costs.
Kosika prefers precision, because a confidently wrong answer is worse than a
model asked, so ties in the report break towards precision.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

from .lessons import CLOSE_ENOUGH, SEMANTIC_ENOUGH, _likeness, words_of

SCHEMA = "kosika.paraphrase-pairs/v1"
REPORT_SCHEMA = "kosika.recall-calibration/v1"
MAX_FAMILIES = 40
MAX_PAIRS_PER_FAMILY = 20
MAX_PAIRS = 400
#: Two ways of asking the same thing, and two ways of only looking like it.
#: `reorder` keeps the words and moves them, which is the ceiling for a measure
#: that counts shared words; `far_miss` shares none, which is its floor.
SAME_KINDS = ("reword", "reorder")
OTHER_KINDS = ("near_miss", "far_miss")
KINDS = SAME_KINDS + OTHER_KINDS
#: Precision bars the capability section reports the best reach at.
BARS = (0.99, 0.95, 0.9, 0.8, 0.5)
#: Thresholds swept, in hundredths. Words are compared by the share of words
#: two questions have in common; meaning by the cosine of their vectors.
WORD_STEPS = tuple(step / 100 for step in range(30, 101))
MEANING_STEPS = tuple(step / 100 for step in range(50, 100))


class CalibrationError(ValueError):
    """A pairs file, or the request to measure one, is invalid."""


@dataclass(frozen=True)
class Pair:
    """One question put to a reference question, and whether it means it."""

    id: str
    family: str
    reference: str
    question: str
    same: bool
    kind: str = "reword"


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CalibrationError(f"{where} must be a non-empty string")
    return value


def load_pairs(path: str | Path) -> list[Pair]:
    """The pairs a file holds, checked before anything is measured."""

    try:
        if Path(path).stat().st_size > 1_000_000:
            raise CalibrationError("Pairs file exceeds 1 MB")
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except CalibrationError:
        raise
    except (OSError, ValueError) as exc:
        raise CalibrationError(f"Cannot read pairs: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != {"schema", "families"}:
        raise CalibrationError(
            f"Pairs must be an object with exactly schema and families, "
            f"using {SCHEMA}"
        )
    if payload.get("schema") != SCHEMA:
        raise CalibrationError(f"Pairs must use schema {SCHEMA}")
    families = payload["families"]
    if not isinstance(families, list) or not 1 <= len(families) <= MAX_FAMILIES:
        raise CalibrationError(f"Pairs require 1..{MAX_FAMILIES} families")
    pairs: list[Pair] = []
    seen: set[str] = set()
    for index, family in enumerate(families):
        if not isinstance(family, dict) or set(family) != {
            "id",
            "reference",
            "pairs",
        }:
            raise CalibrationError(
                f"Family {index} needs exactly id, reference, and pairs"
            )
        family_id = _text(family["id"], f"Family {index} id")
        reference = _text(family["reference"], f"Family {family_id!r} reference")
        items = family["pairs"]
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_PAIRS_PER_FAMILY:
            raise CalibrationError(
                f"Family {family_id!r} needs 1..{MAX_PAIRS_PER_FAMILY} pairs"
            )
        for item in items:
            if not isinstance(item, dict) or not {"id", "question", "same"} <= set(
                item
            ):
                raise CalibrationError(
                    f"A pair of {family_id!r} needs id, question, and same"
                )
            if set(item) - {"id", "question", "same", "kind"}:
                raise CalibrationError(
                    f"A pair of {family_id!r} may hold only id, question, same "
                    "and kind"
                )
            pair_id = _text(item["id"], f"A pair id of {family_id!r}")
            if pair_id in seen:
                raise CalibrationError(f"Pair id {pair_id!r} is used twice")
            seen.add(pair_id)
            if not isinstance(item["same"], bool):
                raise CalibrationError(f"Pair {pair_id!r} needs same true or false")
            same = item["same"]
            kind = item.get("kind") or ("reword" if same else "near_miss")
            if kind not in KINDS:
                raise CalibrationError(
                    f"Pair {pair_id!r} kind must be one of {', '.join(KINDS)}"
                )
            if (kind in SAME_KINDS) != same:
                raise CalibrationError(
                    f"Pair {pair_id!r} kind {kind!r} disagrees with same={same}"
                )
            pairs.append(
                Pair(
                    pair_id,
                    family_id,
                    reference,
                    _text(item["question"], f"Pair {pair_id!r} question"),
                    same,
                    kind,
                )
            )
    if len(pairs) > MAX_PAIRS:
        raise CalibrationError(f"Pairs require at most {MAX_PAIRS} pairs")
    if not any(pair.same for pair in pairs) or not any(
        not pair.same for pair in pairs
    ):
        raise CalibrationError(
            "Pairs need both questions that mean the reference and questions "
            "that do not"
        )
    return pairs


def word_score(reference: str, question: str) -> float:
    """How near two questions are by the share of words they have in common.

    This is the measure `Lessons.recall` uses before a model is asked, so the
    number it reports is the behaviour that ships.
    """

    return _likeness(set(words_of(reference)), set(words_of(question)))


def containment(reference: str, question: str) -> float:
    """The share of the asked question's words the reference also holds."""

    asked = set(words_of(question))
    if not asked:
        return 0.0
    return len(set(words_of(reference)) & asked) / len(asked)


def dice(reference: str, question: str) -> float:
    """Twice the shared words over the words of both questions."""

    left, right = set(words_of(reference)), set(words_of(question))
    total = len(left) + len(right)
    return 2 * len(left & right) / total if total else 0.0


def _trigrams(text: str) -> set[str]:
    letters = "".join(char if char.isalnum() else " " for char in text.lower())
    packed = f" {' '.join(letters.split())} "
    return {packed[index : index + 3] for index in range(len(packed) - 2)}


def trigram_score(reference: str, question: str) -> float:
    """The cosine of the character trigrams two questions share.

    A letter-level measure sees "card" and "cards" as nearly one word, so it is
    the strongest lexical competition the shipped measure has.
    """

    left, right = _trigrams(reference), _trigrams(question)
    if not left or not right:
        return 0.0
    return len(left & right) / ((len(left) * len(right)) ** 0.5)


#: Lexical measures compared on the same pairs, shipped one first.
MEASURES: dict[str, Callable[[str, str], float]] = {
    "words (jaccard)": word_score,
    "containment": containment,
    "dice": dice,
    "trigram cosine": trigram_score,
}


def meaning_score(meaning: Any) -> Callable[[str, str], float]:
    """How near two questions are by the cosine of the vectors granted."""

    def score(reference: str, question: str) -> float:
        found = meaning.nearest(question, [reference])
        return float(found[0][0]) if found else 0.0

    return score


def scored(
    pairs: Sequence[Pair], score: Callable[[str, str], float]
) -> list[dict[str, Any]]:
    """Every pair with the number the measure gives it."""

    return [
        {
            "id": pair.id,
            "family": pair.family,
            "kind": pair.kind,
            "same": pair.same,
            "question": pair.question,
            "score": round(score(pair.reference, pair.question), 3),
        }
        for pair in pairs
    ]


def evaluate_rows(
    rows: Sequence[dict[str, Any]], threshold: float
) -> dict[str, Any]:
    """What answering scored rows at `threshold` would have got right and wrong."""

    true_positive = false_positive = false_negative = true_negative = 0
    for row in rows:
        reached = row["score"] >= threshold
        if row["same"] and reached:
            true_positive += 1
        elif row["same"]:
            false_negative += 1
        elif reached:
            false_positive += 1
        else:
            true_negative += 1
    reached_total = true_positive + false_positive
    same_total = true_positive + false_negative
    precision = true_positive / reached_total if reached_total else 0.0
    recall = true_positive / same_total if same_total else 0.0
    both = precision + recall
    return {
        "threshold": round(threshold, 2),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "true_negative": true_negative,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(2 * precision * recall / both, 3) if both else 0.0,
    }


def evaluate(
    pairs: Sequence[Pair],
    score: Callable[[str, str], float],
    threshold: float,
) -> dict[str, Any]:
    """What answering at `threshold` would have got right and wrong."""

    return evaluate_rows(scored(pairs, score), threshold)


def separation(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """How far apart the scores of questions that mean it and that do not sit."""

    same = [row["score"] for row in rows if row["same"]]
    other = [row["score"] for row in rows if not row["same"]]
    weakest = min(same)
    return {
        "same_median": round(median(same), 3),
        "same_lowest": round(weakest, 3),
        "same_zero": sum(1 for value in same if value == 0.0),
        "other_median": round(median(other), 3),
        "other_highest": round(max(other), 3),
        "crossed": sum(1 for value in other if value >= weakest),
    }


def breakdown(
    rows: Sequence[dict[str, Any]], threshold: float
) -> list[dict[str, Any]]:
    """What each kind of pair survives at one threshold."""

    table: list[dict[str, Any]] = []
    for kind in KINDS:
        of_kind = [row for row in rows if row["kind"] == kind]
        if not of_kind:
            continue
        reached = [row for row in of_kind if row["score"] >= threshold]
        table.append(
            {
                "kind": kind,
                "pairs": len(of_kind),
                "reached": len(reached),
                "right": sum(1 for row in reached if row["same"]),
                "wrong": sum(1 for row in reached if not row["same"]),
            }
        )
    return table


def reach(curve: Sequence[dict[str, Any]], bar: float) -> dict[str, Any]:
    """The most paraphrases reached at no worse than `bar` precision."""

    allowed = [row for row in curve if row["precision"] >= bar]
    if not allowed:
        return {"bar": bar, "threshold": None, "right": 0, "precision": 0.0}
    row = max(allowed, key=lambda held: (held["recall"], held["threshold"]))
    return {
        "bar": bar,
        "threshold": row["threshold"],
        "right": row["true_positive"],
        "precision": row["precision"],
        "recall": row["recall"],
    }


def closest(
    rows: Sequence[dict[str, Any]], limit: int = 3
) -> dict[str, list[dict[str, Any]]]:
    """The hits the measure nearly missed and the misses it nearly took."""

    hits = sorted(
        (row for row in rows if row["same"]),
        key=lambda row: (row["score"], row["id"]),
    )[:limit]
    misses = sorted(
        (row for row in rows if not row["same"]),
        key=lambda row: (-row["score"], row["id"]),
    )[:limit]
    return {"hits": hits, "misses": misses}


def compare(pairs: Sequence[Pair], bar: float = 0.95) -> list[dict[str, Any]]:
    """Every lexical measure on the same pairs, each at its own best threshold."""

    table: list[dict[str, Any]] = []
    for name, measure in MEASURES.items():
        rows = scored(pairs, measure)
        curve = [evaluate_rows(rows, step) for step in WORD_STEPS]
        best = max(
            curve, key=lambda row: (row["f1"], row["precision"], row["threshold"])
        )
        top = reach(curve, bar)
        table.append(
            {
                "measure": name,
                "f1": best["f1"],
                "threshold": best["threshold"],
                "right": best["true_positive"],
                "precision": best["precision"],
                "right_at_bar": top["right"],
                "threshold_at_bar": top["threshold"],
            }
        )
    return table


def calibrate(pairs_path: str | Path, meaning: Any | None = None) -> dict[str, Any]:
    """Measure every threshold and report the shipped one beside the best.

    Without a granted embedding model the words are measured, which is what
    `Lessons.recall` does with no grant; with one, the cosine it uses.
    """

    pairs = load_pairs(pairs_path)
    score: Callable[[str, str], float]
    if meaning is None:
        scorer = "words"
        score = word_score
        steps: Sequence[float] = WORD_STEPS
        shipped = CLOSE_ENOUGH
    else:
        scorer = "meaning"
        score = meaning_score(meaning)
        steps = MEANING_STEPS
        shipped = SEMANTIC_ENOUGH
    rows = scored(pairs, score)
    curve = [evaluate_rows(rows, step) for step in steps]
    best = max(
        curve, key=lambda row: (row["f1"], row["precision"], row["threshold"])
    )
    return {
        "schema": REPORT_SCHEMA,
        "scorer": scorer,
        "pairs": len(pairs),
        "same": sum(1 for pair in pairs if pair.same),
        "different": sum(1 for pair in pairs if not pair.same),
        "families": len({pair.family for pair in pairs}),
        "kinds": {
            kind: sum(1 for pair in pairs if pair.kind == kind) for kind in KINDS
        },
        "shipped_threshold": shipped,
        "shipped": evaluate_rows(rows, shipped),
        "best": best,
        "separation": separation(rows),
        "by_kind": breakdown(rows, shipped),
        "reach": [reach(curve, bar) for bar in BARS],
        "measures": compare(pairs) if meaning is None else [],
        "closest": closest(rows),
        "curve": curve,
    }


def _line(row: dict[str, Any]) -> str:
    answered = row["true_positive"] + row["false_negative"]
    return (
        f"{row['threshold']:.2f}  precision {row['precision']:.2f}  "
        f"recall {row['recall']:.2f}  f1 {row['f1']:.2f}  "
        f"(answered {row['true_positive']} of {answered})"
    )


def _sep_lines(report: dict[str, Any]) -> list[str]:
    held = report["separation"]
    return [
        f"separation   : hits median {held['same_median']:.2f}, weakest "
        f"{held['same_lowest']:.2f}, {held['same_zero']} sharing no word at all",
        f"             : misses median {held['other_median']:.2f}, nearest "
        f"{held['other_highest']:.2f}; {held['crossed']} misses score at least "
        "as high as the weakest hit",
    ]


def _reach_lines(report: dict[str, Any]) -> list[str]:
    lines = ["", "best reach at a precision bar"]
    for row in report["reach"]:
        if row["threshold"] is None:
            lines.append(f"  >= {row['bar']:.2f}  nothing reached at any threshold")
            continue
        lines.append(
            f"  >= {row['bar']:.2f}  {row['right']} of {report['same']} "
            f"paraphrases at {row['threshold']:.2f} "
            f"(precision {row['precision']:.2f})"
        )
    return lines


def _kind_lines(report: dict[str, Any]) -> list[str]:
    lines = ["", "kind          pairs  reached  right  wrong"]
    for row in report["by_kind"]:
        lines.append(
            f"{row['kind']:<13}{row['pairs']:>6}{row['reached']:>9}"
            f"{row['right']:>7}{row['wrong']:>7}"
        )
    return lines


def _measure_lines(report: dict[str, Any]) -> list[str]:
    table = report.get("measures") or []
    if not table:
        return []
    lines = ["", "lexical measures, same pairs, each at its own best f1"]
    lines.append("  measure           best f1  at     right  precise  right at .95")
    for row in table:
        lines.append(
            f"  {row['measure']:<17}{row['f1']:>7.2f}{row['threshold']:>7.2f}"
            f"{row['right']:>8}{row['precision']:>9.2f}{row['right_at_bar']:>13}"
        )
    return lines


def _closest_lines(report: dict[str, Any]) -> list[str]:
    lines = ["", "nearest calls"]
    for row in report["closest"]["hits"]:
        lines.append(f"  hit  {row['score']:.2f}  {row['question']}")
    for row in report["closest"]["misses"]:
        lines.append(f"  miss {row['score']:.2f}  {row['question']}")
    return lines


def report_to_text(report: dict[str, Any]) -> str:
    """A report a person can read."""

    kinds = ", ".join(
        f"{kind} {count}" for kind, count in report["kinds"].items() if count
    )
    lines = [
        f"pairs        : {report['pairs']} from {report['families']} families "
        f"({report['same']} meaning it, {report['different']} not)",
        f"kinds        : {kinds}",
        f"scored by    : {report['scorer']}",
        *_sep_lines(report),
        f"shipped      : {_line(report['shipped'])}",
        f"best measured: {_line(report['best'])}",
        *_reach_lines(report),
        *_kind_lines(report),
        *_measure_lines(report),
    ]
    if report["scorer"] == "words":
        lines.append("")
        lines.append(
            "words alone cannot reach a paraphrase that shares no word with "
            "the reference, whatever the threshold; a meaning grant can"
        )
    lines.extend(_closest_lines(report))
    lines.append("")
    lines.append("threshold  right  wrong  precision  recall  f1")
    marked = {report["shipped"]["threshold"], report["best"]["threshold"]}
    for row in report["curve"]:
        if int(round(row["threshold"] * 100)) % 5 == 0 or row["threshold"] in marked:
            lines.append(
                f"{row['threshold']:>9.2f}  {row['true_positive']:>5}  "
                f"{row['false_positive']:>5}  {row['precision']:>9.2f}  "
                f"{row['recall']:>6.2f}  {row['f1']:>4.2f}"
            )
    return "\n".join(lines)
