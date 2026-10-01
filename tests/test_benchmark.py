"""Tests for the repair benchmark.

The harness is only useful if it can tell a solver that did nothing from a
solver that knows the answer. These tests pin that property, because a
benchmark that quietly fails to discriminate is worse than none: it would let
a weak model look competent.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from kosika.benchmark import (
    BENCHMARK_SCHEMA,
    BenchmarkReport,
    identity_solver,
    oracle_solver,
    run_reference_baselines,
    score,
    validate,
)
from kosika.corpus import CorpusCase, build_corpus

REPO_ROOT = Path(__file__).resolve().parent.parent

_CACHE: list[CorpusCase] | None = None


def corpus() -> list[CorpusCase]:
    global _CACHE
    if _CACHE is None:
        _CACHE = build_corpus(REPO_ROOT)
    return _CACHE


def repairable() -> list[CorpusCase]:
    return [case for case in corpus() if case.is_repairable]


class DiscriminationTests(unittest.TestCase):
    def test_harness_is_valid(self) -> None:
        result = validate(corpus())
        self.assertTrue(result["ok"], result["problems"])

    def test_identity_solver_scores_zero(self) -> None:
        report = score(repairable(), identity_solver, name="identity")
        self.assertEqual(report.score, 0.0)
        self.assertEqual(report.unchanged, report.total)

    def test_oracle_solver_scores_one(self) -> None:
        report = score(repairable(), oracle_solver, name="oracle")
        self.assertEqual(report.score, 1.0)
        self.assertEqual(report.unchanged, 0)

    def test_baselines_span_the_full_range(self) -> None:
        baselines = run_reference_baselines(corpus())
        self.assertEqual(baselines["identity"].score, 0.0)
        self.assertEqual(baselines["oracle"].score, 1.0)

    def test_benchmark_has_enough_cases(self) -> None:
        self.assertGreater(len(repairable()), 200)


class ScoringTests(unittest.TestCase):
    def test_valid_cases_are_not_scored(self) -> None:
        # A valid program is already correct; counting it would inflate a score.
        report = score(corpus(), oracle_solver, name="oracle")
        self.assertEqual(report.total, len(repairable()))

    def test_a_crash_is_a_failure_not_an_error(self) -> None:
        def exploding(case: CorpusCase) -> str:
            raise RuntimeError("solver exploded")

        report = score(repairable()[:5], exploding, name="exploding")
        self.assertEqual(report.solved, 0)
        self.assertEqual(report.total, 5)
        self.assertIn("solver exploded", report.results[0].raised[0])

    def test_empty_case_set_scores_zero_without_dividing_by_zero(self) -> None:
        report = score([], oracle_solver, name="oracle")
        self.assertEqual(report.score, 0.0)

    def test_a_weak_solver_sits_between_floor_and_ceiling(self) -> None:
        # A solver that fixes only the capability cases must score strictly
        # between the two baselines, which is what makes a real score readable.
        target = "check/capability-denied"

        def partial(case: CorpusCase) -> str:
            if case.code == target and case.fix_source is not None:
                return case.fix_source
            return case.source

        report = score(repairable(), partial, name="partial")
        self.assertGreater(report.score, 0.0)
        self.assertLess(report.score, 1.0)

    def test_by_code_breakdown(self) -> None:
        report = score(repairable(), identity_solver, name="identity")
        breakdown = report.by_code()
        self.assertIn("check/capability-denied", breakdown)
        value = breakdown["check/capability-denied"]
        self.assertEqual(value["solved"], 0)
        self.assertEqual(value["rate"], 0.0)

    def test_by_mutation_counts_only_solves(self) -> None:
        report = score(repairable(), oracle_solver, name="oracle")
        solved = report.by_mutation()
        self.assertTrue(all(count > 0 for count in solved.values()))


class SerializationTests(unittest.TestCase):
    def test_report_to_dict(self) -> None:
        payload = score(repairable()[:20], oracle_solver, name="oracle").to_dict()
        json.dumps(payload)
        self.assertEqual(payload["schema"], BENCHMARK_SCHEMA)
        self.assertEqual(payload["solver"], "oracle")
        self.assertEqual(payload["solved"], payload["total"])

    def test_validation_report_is_serialisable(self) -> None:
        json.dumps(validate(corpus()))

    def test_empty_report_serialises(self) -> None:
        json.dumps(BenchmarkReport(name="empty").to_dict())


class CommandLineTests(unittest.TestCase):
    def _run(self, *arguments: str) -> tuple[int, str]:
        import subprocess
        import sys

        process = subprocess.run(
            [sys.executable, "-m", "kosika.cli", *arguments],
            capture_output=True,
            cwd=str(REPO_ROOT),
            timeout=300,
        )
        return process.returncode, process.stdout.decode("utf-8")

    def test_validate_passes(self) -> None:
        code, out = self._run("benchmark", "--validate")
        self.assertEqual(code, 0, out)
        self.assertNotIn("PROBLEM", out)

    def test_baseline_scores(self) -> None:
        code, out = self._run("benchmark")
        self.assertEqual(code, 0)
        self.assertIn("identity", out)
        self.assertIn("oracle", out)

    def test_json_format(self) -> None:
        code, out = self._run("benchmark", "--format", "json")
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(payload["identity"]["score"], 0.0)
        self.assertEqual(payload["oracle"]["score"], 1.0)


if __name__ == "__main__":
    unittest.main()
