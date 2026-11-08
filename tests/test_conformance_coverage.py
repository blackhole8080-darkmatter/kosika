"""The refused programs must reach the rules the checker refuses from.

`tests/test_conformance.py` holds the two engines to the suite. This holds the
suite to the checker: a rule that no program can reach is a promise nobody has
kept, and the count is only meaningful if it cannot quietly fall.
"""

from __future__ import annotations

from kosika.coverage import REFUSED, measure, to_text

#: Measured on this tree. Raise it when a refused program is added; never
#: lower it. A fall means a rule lost the program that reached it, or a case
#: stopped being refused, both of which are regressions in the suite.
FLOOR = 53


def test_no_program_under_refused_is_accepted() -> None:
    report = measure()
    assert not report.accepted, (
        f"{len(report.accepted)} program(s) under conformance/refused load "
        f"fine, so they no longer prove a refusal: {list(report.accepted)[:5]}"
    )


def test_the_number_of_rules_reached_does_not_fall() -> None:
    report = measure()
    assert len(report.covered) >= FLOOR, (
        f"a refused program reaches {report.ratio} checker rules; it reached "
        f"{FLOOR}.\n{to_text(report)}"
    )


def test_each_reached_rule_is_credited_to_one_program() -> None:
    report = measure()
    credited = list(report.covered.values())
    assert len(set(credited)) == len(credited), (
        "one program is credited with two rules, which one refusal cannot do"
    )


def test_every_refused_case_says_it_was_refused() -> None:
    for expected in sorted(REFUSED.glob("*.expected.txt")):
        text = expected.read_text(encoding="utf-8")
        assert text.startswith("Check failed: "), (
            f"{expected.name} does not begin with 'Check failed: '"
        )
