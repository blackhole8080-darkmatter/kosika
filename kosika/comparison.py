"""Compare Kosika with Python on the same tasks.

Each task under `benchmarks/compare` has one Kosika solution and two Python
ones:

* `plain.py`   : the short Python most people would write first.
* `checked.py` : Python that also rejects wrongly shaped inputs and model
  answers, which is what Kosika does without being asked. It may use the
  shared `pyhelpers.py`, whose size is reported once, separately.

All three must return the same result for the task's inputs and scripted model
answers. Each task also lists bad cases: wrongly shaped inputs or model
answers. A solution "catches" a bad case when it refuses to return a result.

The comparison measures source size and caught bad cases. It says nothing
about speed, about how good a real model's answers are, or about libraries a
Python team might add. Size is counted as non-blank, non-comment lines and as
tokens (names, numbers, strings, and symbols).
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

from .model import ModelResponse, ScriptedModelProvider
from .modules import load_program
from .runtime import run
from .spec_benchmark import json_equal

COMPARISON_SCHEMA = "kosika.python-comparison/v1"
VARIANTS = ("kosika", "plain", "checked")
_TOKEN = re.compile(
    r"[A-Za-z_]\w*|\d+(?:\.\d+)?|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|\S"
)


class ComparisonError(ValueError):
    """The comparison tasks are missing or a reference solution is wrong."""


def measure(source: str, comment: str) -> dict[str, int]:
    """Count code lines and tokens, ignoring blank lines and whole-line comments."""

    lines = [
        line
        for line in source.splitlines()
        if line.strip() and not line.strip().startswith(comment)
    ]
    return {"lines": len(lines), "tokens": len(_TOKEN.findall("\n".join(lines)))}


def _run_kosika(path: Path, inputs: Any, answers: list[Any]) -> Any:
    program = load_program(path)
    provider = ScriptedModelProvider(
        [ModelResponse(content=json.dumps(answer)) for answer in answers]
    )
    declared = [model.name for model in program.models if model.provider is None]
    return run(
        program,
        "main",
        None,
        inputs=inputs,
        model_bindings=dict.fromkeys(declared, provider),
    )


def _load_python(path: Path) -> Callable[..., Any]:
    helpers = str(path.parents[1])
    if helpers not in sys.path:
        sys.path.insert(0, helpers)
    spec = importlib.util.spec_from_file_location(
        f"kosika_compare_{path.parent.name}_{path.stem}", path
    )
    if spec is None or spec.loader is None:
        raise ComparisonError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main


def _run_python(path: Path, inputs: Any, answers: list[Any]) -> Any:
    remaining = [json.dumps(answer) for answer in answers]

    def complete(_instruction: str, _payload: str) -> str:
        return remaining.pop(0)

    return _load_python(path)(inputs, complete=complete)


def _attempt(
    variant: str, folder: Path, inputs: Any, answers: list[Any]
) -> tuple[bool, Any]:
    """Run one solution; (True, result) or (False, the refusal)."""

    try:
        if variant == "kosika":
            return True, _run_kosika(folder / "solution.kos", inputs, answers)
        return True, _run_python(folder / f"{variant}.py", inputs, answers)
    except Exception as exc:  # any refusal counts; the kind is reported
        return False, f"{type(exc).__name__}: {exc}"


def compare_task(folder: Path) -> dict[str, Any]:
    try:
        task = json.loads((folder / "task.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ComparisonError(f"Cannot read task {folder.name}: {exc}") from exc
    sizes = {
        "kosika": measure((folder / "solution.kos").read_text(encoding="utf-8"), "//"),
        "plain": measure((folder / "plain.py").read_text(encoding="utf-8"), "#"),
        "checked": measure((folder / "checked.py").read_text(encoding="utf-8"), "#"),
    }
    for variant in VARIANTS:
        ok, result = _attempt(variant, folder, task["inputs"], task["model_answers"])
        if not ok or not json_equal(result, task["expected"]):
            raise ComparisonError(
                f"{folder.name}: the {variant} solution returned {result!r}, "
                f"expected {task['expected']!r}"
            )
    bad_cases = []
    for case in task["bad"]:
        outcome: dict[str, Any] = {"label": case["label"]}
        for variant in VARIANTS:
            ok, result = _attempt(
                variant,
                folder,
                case.get("inputs", task["inputs"]),
                case.get("model_answers", task["model_answers"]),
            )
            outcome[variant] = "caught" if not ok else "accepted"
            outcome[f"{variant}_detail"] = result
        bad_cases.append(outcome)
    return {
        "task": folder.name,
        "about": task["about"],
        "uses_model": bool(task["model_answers"]),
        "sizes": sizes,
        "bad_cases": bad_cases,
        "caught": {
            variant: sum(case[variant] == "caught" for case in bad_cases)
            for variant in VARIANTS
        },
    }


def compare(root: str | Path) -> dict[str, Any]:
    base = Path(root)
    if not base.is_dir():
        raise ComparisonError(f"No comparison tasks found in {base}")
    folders = sorted(path for path in base.iterdir() if (path / "task.json").is_file())
    if not folders:
        raise ComparisonError(f"No comparison tasks found in {base}")
    tasks = [compare_task(folder) for folder in folders]
    totals = {
        variant: {
            "lines": sum(task["sizes"][variant]["lines"] for task in tasks),
            "tokens": sum(task["sizes"][variant]["tokens"] for task in tasks),
            "caught": sum(task["caught"][variant] for task in tasks),
        }
        for variant in VARIANTS
    }
    helpers = base / "pyhelpers.py"
    return {
        "schema": COMPARISON_SCHEMA,
        "tasks": tasks,
        "totals": totals,
        "bad_case_count": sum(len(task["bad_cases"]) for task in tasks),
        "python_helpers": measure(helpers.read_text(encoding="utf-8"), "#")
        if helpers.is_file()
        else {"lines": 0, "tokens": 0},
    }


def to_text(report: Mapping[str, Any]) -> str:
    bad = report["bad_case_count"]
    lines = [
        f"{'task':<22}{'Kosika':>14}{'Python plain':>16}{'Python checked':>18}",
        f"{'':<22}{'lines  caught':>14}{'lines  caught':>16}{'lines  caught':>18}",
    ]
    for task in report["tasks"]:
        cases = len(task["bad_cases"])
        cells = [
            f"{task['sizes'][variant]['lines']:>3}   {task['caught'][variant]}/{cases}"
            for variant in VARIANTS
        ]
        lines.append(f"{task['task']:<22}{cells[0]:>14}{cells[1]:>16}{cells[2]:>18}")
    totals = report["totals"]
    cells = [f"{totals[v]['lines']:>3}  {totals[v]['caught']}/{bad}" for v in VARIANTS]
    lines.append(f"{'total':<22}{cells[0]:>14}{cells[1]:>16}{cells[2]:>18}")
    lines.append(
        "tokens: "
        + ", ".join(f"{variant} {totals[variant]['tokens']}" for variant in VARIANTS)
    )
    helpers = report["python_helpers"]
    lines.append(
        f"Python checked also needs shared helpers: {helpers['lines']} lines, "
        f"{helpers['tokens']} tokens (counted once, not above)."
    )
    missed = [
        f"  {task['task']}: {case['label']}"
        for task in report["tasks"]
        for case in task["bad_cases"]
        if case["plain"] == "accepted"
    ]
    if missed:
        lines.append("Bad cases plain Python accepted without complaint:")
        lines += missed
    return "\n".join(lines)
