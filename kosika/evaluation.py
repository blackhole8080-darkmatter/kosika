"""Fresh-execution evaluations; assertions are data, never executable code."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from .ast import Program
from .model import ModelProvider
from .runtime import run
from .schema import schemas_equal


@dataclass(frozen=True)
class EvaluationCase:
    name: str
    inputs: dict[str, Any]
    expected: dict[str, Any] | None = None
    contains: dict[str, str] = field(default_factory=dict)


def evaluate(
    program: Program,
    workflow: str,
    cases: list[EvaluationCase],
    output: str | Path,
    providers: Callable[[EvaluationCase], Mapping[str, ModelProvider]],
) -> dict[str, Any]:
    if not cases or len(cases) > 64 or len({case.name for case in cases}) != len(cases):
        raise ValueError("Evaluations require 1..64 unique cases")
    for case in cases:
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", case.name) is None or (
            case.expected is None and not case.contains
        ):
            raise ValueError("Evaluation needs a safe name and at least one assertion")
        if (
            not isinstance(case.inputs, dict)
            or not isinstance(case.contains, dict)
            or any(
                not isinstance(k, str) or not isinstance(v, str)
                for k, v in case.contains.items()
            )
        ):
            raise ValueError("Invalid evaluation input or assertion")
    directory = Path(output)
    directory.mkdir(parents=True, exist_ok=False)
    results = []
    for case in cases:
        try:
            result = run(
                program,
                workflow,
                directory / f"{case.name}.jsonl",
                inputs=case.inputs,
                model_bindings=providers(case),
            )
            failures = []
            if case.expected is not None and not schemas_equal(result, case.expected):
                failures.append("exact output mismatch")
            for key, text in case.contains.items():
                if not isinstance(result.get(key), str) or text not in result[key]:
                    failures.append(f"{key}: required text absent")
            results.append(
                {"name": case.name, "passed": not failures, "failures": failures}
            )
        except (ValueError, RuntimeError, OSError) as exc:
            results.append(
                {
                    "name": case.name,
                    "passed": False,
                    "error": type(exc).__name__,
                    "message": str(exc),
                }
            )
    report = {"passed": all(item["passed"] for item in results), "cases": results}
    (directory / "report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    return report
