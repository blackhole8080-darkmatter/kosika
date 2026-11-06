"""Many candidates, measured against independent evidence.

A single-path loop commits to one answer and then defends it. This module keeps
the alternatives alive until the evidence has been read, and then measurement
commits to one of them:

* superposition -- every candidate is carried at once, and nothing is decided
  before the evidence is in;
* interference -- each *distinct* source that supports a candidate adds to it,
  each that contradicts takes away, so corroboration amplifies and
  contradiction cancels. A source counts once: two sources saying the same
  thing outrank one source saying it twice;
* measurement -- exactly one candidate is committed to, or none when the best
  of them is too weak to stand or two are indistinguishable.

Nothing here claims a speed-up. This is a classical search over a small
population; what it buys is a better answer at the same cost, and a record of
why that answer was chosen. The quantum vocabulary is the organising idea, not
a promise about complexity classes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: How many independent sources must agree before a candidate may be trusted
#: fully. One source is a claim, not a corroboration.
AGREEMENT_MIN = 2

#: The most confidence one source may buy, however sure it sounds.
THIN_CAP = 0.4

#: The least confidence worth measuring: below it, the honest answer is to
#: say nothing rather than to pick the least bad candidate.
MEASURE_FLOOR = 0.15


@dataclass(frozen=True)
class Evidence:
    """One source's Opinion about one candidate.

    `stance` is +1 for "this is the answer", -1 for "this is wrong", and
    anything between for a partial view; 0 is silence, and says nothing either
    way. `source` identifies the source itself, so the same source speaking
    twice is one voice rather than two.
    """

    source: str
    kind: str
    stance: float
    weight: float = 1.0
    fresh: bool = False

    @property
    def supporting(self) -> bool:
        return self.stance > 0


@dataclass(frozen=True)
class Candidate:
    """One way the question could be answered."""

    id: str
    text: str
    evidence: tuple[Evidence, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Scoring:
    """What interference did to one candidate."""

    id: str
    support: float
    against: float
    confidence: float
    sources: tuple[str, ...]
    agreeing: tuple[str, ...]
    contradicted_by: tuple[str, ...]
    capped: bool


@dataclass(frozen=True)
class Decision:
    """The measurement: one candidate, or a reason there is none."""

    winner: Candidate | None
    measured: bool
    reason: str
    scores: tuple[Scoring, ...]

    @property
    def confidence(self) -> float:
        """The winning confidence, or 0 when nothing was measured."""
        return self.scores[0].confidence if self.measured and self.scores else 0.0


def _strongest(evidence: tuple[Evidence, ...]) -> dict[str, Evidence]:
    """One voice per source: its strongest view, the negative when tied.

    Ties go to the negative on purpose: a source that both supports and
    contradicts a candidate is not evidence for it.
    """
    kept: dict[str, Evidence] = {}
    for item in evidence:
        seen = kept.get(item.source)
        if seen is None or abs(item.stance) > abs(seen.stance):
            kept[item.source] = item
        elif abs(item.stance) == abs(seen.stance) and item.stance < seen.stance:
            kept[item.source] = item
    return kept


def score(candidate: Candidate) -> Scoring:
    """How much independent evidence stands behind one candidate."""
    voices = _strongest(candidate.evidence)
    support = sum(voice.weight * max(voice.stance, 0.0) for voice in voices.values())
    against = sum(voice.weight * max(-voice.stance, 0.0) for voice in voices.values())
    agreeing = tuple(
        sorted(name for name, voice in voices.items() if voice.supporting)
    )
    contradicted = tuple(
        sorted(name for name, voice in voices.items() if voice.stance < 0)
    )
    # Bounded and saturating: corroboration never reaches certainty, and thin
    # evidence cannot look strong however loud one source is.
    raw = (support - against) / (1.0 + support + against)
    capped = len(agreeing) < AGREEMENT_MIN
    confidence = min(raw, THIN_CAP) if capped else raw
    return Scoring(
        id=candidate.id,
        support=support,
        against=against,
        confidence=confidence,
        sources=tuple(sorted(voices)),
        agreeing=agreeing,
        contradicted_by=contradicted,
        capped=capped,
    )


def measure(candidates: tuple[Candidate, ...], *, floor: float = MEASURE_FLOOR) -> Decision:
    """Read the evidence and commit to one candidate, or to none.

    Nothing is measured when the best candidate is weaker than `floor`, and
    nothing is measured when the leaders are indistinguishable: picking one of
    two equals is a coin toss dressed as a judgement.
    """
    if not candidates:
        return Decision(None, False, "no candidates were offered", ())
    scored = tuple(
        sorted((score(candidate) for candidate in candidates),
               key=lambda item: (-item.confidence, -item.support, item.id))
    )
    best = scored[0]
    if best.confidence < floor:
        return Decision(
            None, False,
            f"the best of {len(scored)} candidates was too weak to stand "
            f"({best.confidence:.2f} < {floor:.2f})",
            scored,
        )
    tied = tuple(
        item.id for item in scored
        if item.confidence == best.confidence and item.support == best.support
    )
    if len(tied) > 1:
        return Decision(
            None, False,
            "the candidates were indistinguishable: " + ", ".join(tied),
            scored,
        )
    winner = next(candidate for candidate in candidates if candidate.id == best.id)
    return Decision(
        winner, True,
        f"{best.id} stood on {len(best.agreeing)} source"
        f"{'' if len(best.agreeing) == 1 else 's'}"
        + (f", against {len(best.contradicted_by)}" if best.contradicted_by else ""),
        scored,
    )


def record(decision: Decision) -> dict:
    """A decision as plain data, for a trace or a ledger row."""
    return {
        "winner": decision.winner.id if decision.winner else "",
        "measured": decision.measured,
        "confidence": round(decision.confidence, 4),
        "reason": decision.reason,
        "scores": [
            {
                "id": item.id,
                "support": round(item.support, 4),
                "against": round(item.against, 4),
                "confidence": round(item.confidence, 4),
                "agreeing": list(item.agreeing),
                "contradicted_by": list(item.contradicted_by),
                "capped": item.capped,
            }
            for item in decision.scores
        ],
    }
