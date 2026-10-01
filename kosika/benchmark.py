"""Score a solver against the repair corpus.

The purpose of this module is to make the question "can a model use Kosika?"
answerable, and to answer it before any model is involved. A benchmark that
cannot distinguish a solver that does nothing from a solver that knows the
answer measures nothing, so two reference solvers are always reported:

``identity``
    Returns the broken program unchanged. This is the floor: a solver that
    cannot beat it is not solving anything.

``oracle``
    Applies the case's stored reference patch. This is the ceiling, and it
    proves the harness, the patches, and the checker all agree -- a benchmark
    whose ceiling is below 100% is broken, not the solver.

Reporting both is what makes a real solver's score interpretable. A single
number with no floor and no ceiling cannot be read.

A solver is any callable from a case to proposed source. Model-backed solvers
plug in at that seam, so scoring is independent of which model produced the
candidate.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .corpus import CorpusCase, Edit, source_lines
from .diagnostics import analyze

BENCHMARK_SCHEMA = "kosika.benchmark/v1"

#: A solver maps a case to the source it believes is repaired.
Solver = Callable[[CorpusCase], str]


@dataclass
class CaseResult:
    """The outcome for one case."""

    case_id: str
    code: str | None
    mutation: str
    solved: bool
    raised: tuple[str, ...] = ()
    no_change: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.case_id,
            "code": self.code,
            "mutation": self.mutation,
            "solved": self.solved,
            "raised": list(self.raised),
            "no_change": self.no_change,
        }


@dataclass
class BenchmarkReport:
    """Aggregate scores for one solver over one case set."""

    name: str
    results: list[CaseResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def solved(self) -> int:
        return sum(1 for result in self.results if result.solved)

    @property
    def score(self) -> float:
        return (self.solved / self.total) if self.total else 0.0

    @property
    def unchanged(self) -> int:
        return sum(1 for result in self.results if result.no_change)

    def by_code(self) -> dict[str, dict[str, Any]]:
        """Per-diagnostic-code accuracy, which is where the real signal is."""

        groups: dict[str, list[CaseResult]] = {}
        for result in self.results:
            groups.setdefault(result.code or "<none>", []).append(result)
        return {
            code: {
                "total": len(items),
                "solved": sum(1 for item in items if item.solved),
                "rate": sum(1 for item in items if item.solved) / len(items),
            }
            for code, items in sorted(groups.items())
        }

    def by_mutation(self) -> dict[str, int]:
        counter: Counter[str] = Counter()
        for result in self.results:
            counter[result.mutation] += 1 if result.solved else 0
        return dict(sorted(counter.items()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": BENCHMARK_SCHEMA,
            "solver": self.name,
            "total": self.total,
            "solved": self.solved,
            "score": round(self.score, 4),
            "unchanged": self.unchanged,
            "by_code": {
                code: {
                    "total": value["total"],
                    "solved": value["solved"],
                    "rate": round(value["rate"], 4),
                }
                for code, value in self.by_code().items()
            },
        }


# ---------------------------------------------------------------------------
# Reference solvers
# ---------------------------------------------------------------------------


def identity_solver(case: CorpusCase) -> str:
    """Return the broken program unchanged. The floor."""

    return case.source


def oracle_solver(case: CorpusCase) -> str:
    """Apply the case's stored reference patch. The ceiling."""

    if case.fix_edit is None or case.fix_source is None:
        return case.source
    edit = Edit(
        start=case.fix_edit["start"],
        removed=case.fix_edit["removed"],
        inserted=tuple(case.fix_edit["inserted"]),
    )
    return "\n".join(edit.apply(source_lines(case.source)))


REFERENCE_SOLVERS: Mapping[str, Solver] = {
    "identity": identity_solver,
    "oracle": oracle_solver,
}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score(
    cases: Sequence[CorpusCase],
    solver: Solver,
    *,
    name: str = "solver",
    on_result: Callable[[CaseResult], None] | None = None,
) -> BenchmarkReport:
    """Run ``solver`` over ``cases`` and report how many it repairs.

    A case counts as solved when the candidate it returns analyses clean, which
    is the only success criterion that matters: a fix that still fails to check
    is not a fix, however plausible it looks.

    ``on_result`` is invoked per case so a long run can report progress; a
    model-backed run takes minutes per case on CPU, and a harness that prints
    nothing until the end is indistinguishable from one that has hung.
    """

    report = BenchmarkReport(name=name)
    for case in cases:
        if case.is_valid:
            # A valid program is already solved; only repair cases are scored.
            continue
        try:
            candidate = solver(case)
        except Exception as exc:  # a solver crash is a failure, not an error
            result = CaseResult(
                case_id=case.case_id,
                code=case.code,
                mutation=case.mutation,
                solved=False,
                raised=(f"solver raised {type(exc).__name__}: {exc}",),
            )
            report.results.append(result)
            if on_result is not None:
                on_result(result)
            continue
        remaining = analyze(candidate, path=case.origin)
        result = CaseResult(
            case_id=case.case_id,
            code=case.code,
            mutation=case.mutation,
            solved=not remaining,
            raised=tuple(d.code for d in remaining),
            no_change=candidate == case.source,
        )
        report.results.append(result)
        if on_result is not None:
            on_result(result)
    return report


def run_reference_baselines(
    cases: Sequence[CorpusCase],
) -> dict[str, BenchmarkReport]:
    """Score the identity and oracle solvers, establishing floor and ceiling."""

    return {
        name: score(cases, solver, name=name)
        for name, solver in REFERENCE_SOLVERS.items()
    }


def validate(
    cases: Sequence[CorpusCase],
) -> dict[str, Any]:
    """Check that the benchmark discriminates before any model is plugged in.

    A harness is trustworthy when the floor is at the bottom and the ceiling
    reaches the top. If the oracle cannot reach 1.0 the patches or the harness
    are wrong; if the identity solver scores above 0 some cases are trivially
    clean.
    """

    baselines = run_reference_baselines(cases)
    identity = baselines["identity"]
    oracle = baselines["oracle"]
    problems: list[str] = []
    if oracle.score < 1.0:
        problems.append(
            f"oracle solver scored {oracle.score:.3f}, expected 1.0: "
            f"{oracle.total - oracle.solved} case(s) its own patch did not fix"
        )
    if identity.score > 0.0:
        problems.append(
            f"identity solver scored {identity.score:.3f}, expected 0.0: "
            "some cases are already clean"
        )
    return {
        "schema": BENCHMARK_SCHEMA,
        "ok": not problems,
        "problems": problems,
        "identity_score": round(identity.score, 4),
        "oracle_score": round(oracle.score, 4),
        "cases": oracle.total,
    }


__all__ = [
    "BENCHMARK_SCHEMA",
    "BenchmarkReport",
    "CaseResult",
    "REFERENCE_SOLVERS",
    "Solver",
    "identity_solver",
    "oracle_solver",
    "run_reference_baselines",
    "score",
    "validate",
]
