"""Interference: what independent agreement does to a set of candidates."""

from __future__ import annotations

from kosika.interference import (
    MEASURE_FLOOR,
    THIN_CAP,
    Candidate,
    Evidence,
    measure,
    record,
    score,
)


def two_sources(first: str = "kg:a", second: str = "web:b") -> tuple[Evidence, ...]:
    return (
        Evidence(first, "kg", 1.0),
        Evidence(second, "web", 1.0),
    )


def test_one_source_speaking_twice_is_one_voice() -> None:
    twice = Candidate(
        "twice", "an answer", (Evidence("kg:a", "kg", 1.0), Evidence("kg:a", "kg", 1.0))
    )
    once = Candidate("once", "an answer", (Evidence("kg:a", "kg", 1.0),))

    assert score(twice).support == 1.0
    assert score(twice).support == score(once).support
    assert score(twice).sources == ("kg:a",)


def test_corroboration_against_two_sources_outranks_two_voices_of_one() -> None:
    twice = Candidate(
        "twice", "an answer", (Evidence("kg:a", "kg", 1.0), Evidence("kg:a", "kg", 1.0))
    )
    two = Candidate("two", "an answer", two_sources())

    assert score(two).support > score(twice).support
    assert score(two).confidence > score(twice).confidence


def test_one_source_cannot_buy_full_confidence() -> None:
    alone = score(Candidate("alone", "an answer", (Evidence("kg:a", "kg", 1.0),)))

    assert alone.capped is True
    assert alone.confidence == THIN_CAP
    # Two sources of the same weight are allowed past the cap.
    assert score(Candidate("pair", "an answer", two_sources())).capped is False
    assert score(Candidate("pair", "an answer", two_sources())).confidence > THIN_CAP


def test_a_contradiction_takes_confidence_away_and_is_kept() -> None:
    answered = Candidate(
        "c",
        "an answer",
        (Evidence("kg:a", "kg", 1.0), Evidence("web:b", "web", -1.0)),
    )
    judged = score(answered)

    assert judged.support == 1.0
    assert judged.against == 1.0
    assert judged.confidence == 0.0
    assert judged.contradicted_by == ("web:b",)


def test_a_source_that_both_supports_and_contradicts_counts_as_against() -> None:
    judged = score(
        Candidate(
            "c",
            "an answer",
            (Evidence("kg:a", "kg", 1.0), Evidence("kg:a", "kg", -1.0)),
        )
    )

    assert judged.support == 0.0
    assert judged.against == 1.0
    assert judged.sources == ("kg:a",)


def test_nothing_is_measured_when_every_candidate_is_weak() -> None:
    weak = (
        Candidate("a", "one", (Evidence("kg:a", "kg", 0.05),)),
        Candidate("b", "two", (Evidence("web:b", "web", 0.05),)),
    )
    decision = measure(weak)

    assert decision.measured is False
    assert decision.winner is None
    assert "too weak to stand" in decision.reason


def test_two_equals_are_not_measured() -> None:
    equal = (
        Candidate(
            "a", "one", (Evidence("kg:a", "kg", 1.0), Evidence("web:b", "web", 1.0))
        ),
        Candidate(
            "b", "two", (Evidence("kg:c", "kg", 1.0), Evidence("web:d", "web", 1.0))
        ),
    )
    decision = measure(equal)

    assert decision.measured is False
    assert decision.winner is None
    assert "indistinguishable" in decision.reason


def test_the_corroborated_candidate_is_measured_out() -> None:
    candidates = (
        Candidate("thin", "a guess", (Evidence("model:x", "model", 1.0),)),
        Candidate(
            "solid",
            "the answer",
            two_sources() + (Evidence("memory:y", "memory", 1.0),),
        ),
    )
    decision = measure(candidates)

    assert decision.measured is True
    assert decision.winner is not None and decision.winner.id == "solid"
    assert decision.confidence > THIN_CAP
    assert "memory:y" in decision.scores[0].agreeing


def test_the_order_of_candidates_does_not_change_the_measurement() -> None:
    first = Candidate("first", "one", two_sources())
    second = Candidate("second", "two", (Evidence("kg:z", "kg", 1.0),))

    forwards = measure((first, second))
    backwards = measure((second, first))

    assert forwards.winner is not None and backwards.winner is not None
    assert forwards.winner.id == backwards.winner.id == "first"


def test_a_record_is_plain_data_for_a_trace() -> None:
    decision = measure((Candidate("only", "an answer", two_sources()),))
    written = record(decision)

    assert written["winner"] == "only"
    assert written["measured"] is True
    assert written["scores"][0]["agreeing"] == ["kg:a", "web:b"]
    assert written["confidence"] >= MEASURE_FLOOR
