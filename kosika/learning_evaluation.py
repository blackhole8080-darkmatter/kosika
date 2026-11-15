"""Cold/warm evaluations of accepted lesson reuse, judged by source fixtures."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .checker import check
from .lessons import Lessons, census
from .model import ModelResponse, ScriptedModelProvider
from .modules import load_program
from .runtime import replay, run
from .schema import schemas_equal
from .spec_benchmark import json_equal

TASKS_SCHEMA = "kosika.learning-tasks/v1"
REPORT_SCHEMA = "kosika.learning-evaluation/v1"
_MAX_TASKS = 64
_MAX_RESPONSES = 16
_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


class LearningEvaluationError(ValueError):
    """A learning task set or its bounded evaluation is invalid."""


def _read_tasks(path: str | Path) -> tuple[str, list[dict[str, Any]]]:
    try:
        if Path(path).stat().st_size > 1_000_000:
            raise LearningEvaluationError("Learning task file exceeds 1 MB")
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except LearningEvaluationError:
        raise
    except (OSError, ValueError) as exc:
        raise LearningEvaluationError(f"Cannot read learning tasks: {exc}") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema", "workflow", "tasks"}
        or payload.get("schema") != TASKS_SCHEMA
        or not isinstance(payload.get("workflow"), str)
        or not payload["workflow"]
    ):
        raise LearningEvaluationError(f"Tasks must use schema {TASKS_SCHEMA}")
    tasks = payload["tasks"]
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= _MAX_TASKS:
        raise LearningEvaluationError("Learning evaluations require 1..64 tasks")
    ids: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict) or set(task) != {
            "id",
            "inputs",
            "expected",
            "responses",
        }:
            raise LearningEvaluationError(
                "Each task needs exactly id, inputs, expected, and responses"
            )
        task_id = task["id"]
        if (
            not isinstance(task_id, str)
            or not _TASK_ID.fullmatch(task_id)
            or task_id in ids
        ):
            raise LearningEvaluationError("Learning task ids must be safe and unique")
        ids.add(task_id)
        if not isinstance(task["inputs"], dict) or not isinstance(
            task["expected"], dict
        ):
            raise LearningEvaluationError(
                f"Task {task_id!r} needs object inputs and expected output"
            )
        responses = task["responses"]
        if not isinstance(responses, dict) or not responses:
            raise LearningEvaluationError(
                f"Task {task_id!r} needs model response fixtures"
            )
        for name, values in responses.items():
            if (
                not isinstance(name, str)
                or not isinstance(values, list)
                or not 1 <= len(values) <= _MAX_RESPONSES
                or any(not isinstance(value, dict) for value in values)
            ):
                raise LearningEvaluationError(
                    f"Task {task_id!r} has invalid responses for model {name!r}"
                )
    return payload["workflow"], tasks


def _events(path: Path) -> list[dict[str, Any]]:
    try:
        items = [
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        ]
    except (OSError, ValueError) as exc:
        raise LearningEvaluationError(
            f"Cannot read generated trace {path.name}: {exc}"
        ) from exc
    if any(not isinstance(item, dict) for item in items):
        raise LearningEvaluationError(
            f"Generated trace {path.name} is not a JSON object stream"
        )
    return items


def _aggregate(stats: list[dict[str, Any]]) -> dict[str, Any]:
    steps = sum(item["model_steps"] for item in stats)
    lessons = sum(item["from_lessons"] for item in stats)
    how: Counter[str] = Counter()
    for item in stats:
        how.update(item["how"])
    return {
        "model_steps": steps,
        "from_lessons": lessons,
        "independence_ratio": round(lessons / steps, 3) if steps else None,
        "how": dict(sorted(how.items())),
    }


def evaluate_learning(
    source: str | Path, tasks_path: str | Path, output: str | Path
) -> dict[str, Any]:
    """Run tasks once with fixtures, retain accepted answers, then run offline.

    The output directory must not exist. Raw traces and the human-readable
    lesson store are preserved there for inspection and independent replay.
    """

    workflow, tasks = _read_tasks(tasks_path)
    program = load_program(source)
    check(program)
    if workflow not in {item.name for item in program.workflows}:
        raise LearningEvaluationError(f"Unknown workflow {workflow!r}")
    declared_models = {item.name for item in program.models}
    for task in tasks:
        unknown = set(task["responses"]) - declared_models
        if unknown:
            raise LearningEvaluationError(
                f"Task {task['id']!r} fixtures name undeclared model(s): {', '.join(sorted(unknown))}"
            )
    directory = Path(output)
    directory.mkdir(parents=True, exist_ok=False)
    memory = Lessons(directory / "memory")
    results: list[dict[str, Any]] = []
    cold_stats: list[dict[str, Any]] = []
    warm_stats: list[dict[str, Any]] = []

    for task in tasks:
        task_id = task["id"]
        providers = {
            name: ScriptedModelProvider(
                [
                    ModelResponse(content=json.dumps(value, allow_nan=False))
                    for value in values
                ]
            )
            for name, values in task["responses"].items()
        }
        cold_trace = directory / f"cold-{task_id}.jsonl"
        cold_result = run(
            program,
            workflow,
            cold_trace,
            inputs=task["inputs"],
            model_bindings=providers,
            lessons=memory,
        )
        cold_events = _events(cold_trace)
        cold_census = census(cold_events)
        if any(
            len(provider.requests) != len(task["responses"][name])
            for name, provider in providers.items()
        ):
            raise LearningEvaluationError(
                f"Task {task_id!r} did not consume exactly its cold-start model fixtures"
            )
        if cold_census["model_steps"] == 0 or cold_census["from_lessons"] != 0:
            raise LearningEvaluationError(
                f"Task {task_id!r} was not a cold model run; revise overlapping task questions"
            )
        if not json_equal(replay(cold_trace), cold_result):
            raise LearningEvaluationError(f"Cold trace replay mismatch for {task_id!r}")

        warm_trace = directory / f"warm-{task_id}.jsonl"
        warm_result = run(
            program,
            workflow,
            warm_trace,
            inputs=task["inputs"],
            model_bindings={},
            lessons=memory,
        )
        warm_events = _events(warm_trace)
        warm_census = census(warm_events)
        if not json_equal(replay(warm_trace), warm_result):
            raise LearningEvaluationError(f"Warm trace replay mismatch for {task_id!r}")
        cold_stats.append(cold_census)
        warm_stats.append(warm_census)
        results.append(
            {
                "id": task_id,
                "expected": task["expected"],
                "cold": {
                    "passed": schemas_equal(cold_result, task["expected"]),
                    "result": cold_result,
                    "trace": cold_trace.name,
                    "census": cold_census,
                },
                "warm": {
                    "passed": schemas_equal(warm_result, task["expected"]),
                    "result": warm_result,
                    "trace": warm_trace.name,
                    "census": warm_census,
                },
            }
        )

    cold = _aggregate(cold_stats)
    warm = _aggregate(warm_stats)
    report = {
        "schema": REPORT_SCHEMA,
        "workflow": workflow,
        "tasks": len(results),
        "cold_passed": sum(item["cold"]["passed"] for item in results),
        "warm_passed": sum(item["warm"]["passed"] for item in results),
        "cold": cold,
        "warm": warm,
        "results": results,
    }
    (directory / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return report


def report_to_text(report: dict[str, Any]) -> str:
    """The report as a few aligned lines a person can read."""

    independence = report["warm"]["independence_ratio"]
    return "\n".join(
        (
            f"{'tasks':<20}: {report['tasks']}",
            f"{'cold correct':<20}: {report['cold_passed']}/{report['tasks']}",
            f"{'warm correct':<20}: {report['warm_passed']}/{report['tasks']}",
            f"{'model independence':<20}: "
            f"{'none' if independence is None else independence}",
        )
    )
