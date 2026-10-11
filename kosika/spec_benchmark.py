"""Measure whether a model can write Kosika from a plain-language spec.

The repair corpus (`kosika benchmark`) asks a model to fix one
mutated line. This benchmark asks the harder question an AI author faces:
given only a short language guide and a spec, write the whole program.

Each task names its records, declarations, and workflow exactly, and carries
the inputs, the model and tool fixtures, and the expected result. A candidate
program is judged by Kosika itself, never by a model:

* it must pass the checker;
* any structural requirement must hold in the checked program;
* unless the task is check-only, running it with the fixtures must return the
  expected value exactly.

A solver gets ``max_attempts`` tries. After a failed try it is shown what
Kosika reported and asked for a corrected program. Nothing a solver writes is
saved outside a temporary directory, and a candidate runs only with the task's
fixtures, so it has no model, tool, file, or network authority.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .ast import Program, ToolDecl
from .development import check_project
from .execution import ExecutionLimits
from .model import (
    ChatMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ScriptedModelProvider,
)
from .modules import load_program
from .runtime import run

SPEC_TASKS_SCHEMA = "kosika.spec-tasks/v1"
SPEC_REPORT_SCHEMA = "kosika.spec-benchmark/v1"
MAX_SOURCE_BYTES = 64 * 1024
_FENCE = re.compile(r"```[A-Za-z]*[ \t]*\r?\n(.*?)```", re.DOTALL)
_CATEGORIES = (
    "records",
    "tools",
    "agents",
    "swarms",
    "workflows",
    "functions",
    "models",
)


class SpecBenchmarkError(ValueError):
    """The task file or a solver setup is invalid."""


@dataclass(frozen=True)
class SpecTask:
    id: str
    level: str
    spec: str
    inputs: dict[str, Any] = field(default_factory=dict)
    model_fixture: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    tool_fixture: dict[str, Any] = field(default_factory=dict)
    expected: Any = None
    check_only: bool = False
    structure: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class Verdict:
    """What Kosika found when it judged one candidate program."""

    checked: bool
    passed: bool
    stage: str
    codes: tuple[str, ...] = ()
    feedback: str = ""


@dataclass
class Attempt:
    index: int
    verdict: Verdict
    seconds: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    source: str = ""


@dataclass
class TaskResult:
    id: str
    level: str
    attempts: list[Attempt]

    @property
    def passed(self) -> bool:
        return bool(self.attempts) and self.attempts[-1].verdict.passed

    @property
    def first_try(self) -> bool:
        return bool(self.attempts) and self.attempts[0].verdict.passed


Solver = Callable[[SpecTask, Sequence[tuple[str, Verdict]]], tuple[str, dict[str, int]]]


def load_tasks(path: str | Path) -> list[SpecTask]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SpecBenchmarkError(f"Cannot read spec tasks: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != SPEC_TASKS_SCHEMA:
        raise SpecBenchmarkError(f"Spec tasks must use schema {SPEC_TASKS_SCHEMA}")
    raw_tasks = payload.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise SpecBenchmarkError("Spec tasks must contain a nonempty task list")
    allowed = set(SpecTask.__dataclass_fields__)
    tasks: list[SpecTask] = []
    for raw in raw_tasks:
        if not isinstance(raw, dict) or set(raw) - allowed:
            raise SpecBenchmarkError("A spec task has unknown fields")
        if not all(
            isinstance(raw.get(key), str) and raw[key]
            for key in ("id", "level", "spec")
        ):
            raise SpecBenchmarkError("A spec task needs id, level, and spec text")
        if not raw.get("check_only") and "expected" not in raw:
            raise SpecBenchmarkError(f"Task {raw['id']!r} needs an expected result")
        tasks.append(SpecTask(**{**raw, "structure": tuple(raw.get("structure", ()))}))
    if len({task.id for task in tasks}) != len(tasks):
        raise SpecBenchmarkError("Spec task ids must be unique")
    return tasks


def json_equal(left: Any, right: Any) -> bool:
    """Structural JSON equality: 1 equals 1.0, but true never equals 1."""

    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            json_equal(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            json_equal(a, b) for a, b in zip(left, right)
        )
    return type(left) is type(right) and left == right


def _lookup(declaration: Mapping[str, Any], dotted: str) -> Any:
    value: Any = declaration
    for part in dotted.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return _MISSING
        value = value[part]
    return value


_MISSING = object()


def structure_failures(
    program: Program, source: str, requirements: Sequence[Mapping[str, Any]]
) -> list[str]:
    """Check required declarations against the checked program."""

    failures: list[str] = []
    for requirement in requirements:
        pattern = requirement.get("source_matches")
        if pattern is not None:
            if not re.search(pattern, source):
                failures.append(f"the program must contain text matching {pattern}")
            continue
        category, name = requirement.get("in"), requirement.get("name")
        if category not in _CATEGORIES or not isinstance(name, str):
            raise SpecBenchmarkError(f"Invalid structure requirement: {requirement}")
        found = [item for item in getattr(program, category) if item.name == name]
        singular = category[:-1]
        if not found:
            failures.append(f"missing {singular} {name!r}")
            continue
        declaration = asdict(found[0])
        for key, wanted in requirement.get("has", {}).items():
            actual = _lookup(declaration, key)
            if actual is _MISSING or not json_equal(actual, wanted):
                shown = "nothing" if actual is _MISSING else json.dumps(actual)
                failures.append(
                    f"{singular} {name!r} must have {key} = {json.dumps(wanted)}, "
                    f"found {shown}"
                )
        for key, wanted in requirement.get("contains", {}).items():
            actual = declaration.get(key, ())
            missing = [item for item in wanted if item not in actual]
            if missing:
                failures.append(
                    f"{singular} {name!r} must list {', '.join(missing)} in {key}"
                )
    return failures


def _diagnostic_lines(report: Mapping[str, Any]) -> tuple[tuple[str, ...], str]:
    codes: list[str] = []
    lines: list[str] = []
    for item in report.get("diagnostics", ()):
        codes.append(str(item.get("code")))
        where = ""
        position = item.get("range")
        if isinstance(position, Mapping):
            start = position.get("start", {})
            where = f" (line {int(start.get('line', 0)) + 1})"
        lines.append(f"{item.get('code')}{where}: {item.get('message')}")
    return tuple(codes), "\n".join(lines)


def judge(task: SpecTask, source: str) -> Verdict:
    """Check, inspect, and run one candidate program with the task's fixtures."""

    if not source.strip():
        return Verdict(False, False, "empty", ("spec/empty",), "No program was given.")
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        return Verdict(False, False, "empty", ("spec/too-large",), "Program too large.")
    with tempfile.TemporaryDirectory(prefix="kosika-spec-") as directory:
        path = Path(directory) / "main.kos"
        path.write_text(source, encoding="utf-8")
        report = check_project(path)["diagnostics"]
        if not report["ok"]:
            codes, text = _diagnostic_lines(report)
            return Verdict(False, False, "check", codes, text)
        program = load_program(path)

    failures = structure_failures(program, source, task.structure)
    if failures:
        return Verdict(
            True, False, "structure", ("spec/structure",), "\n".join(failures)
        )
    if task.check_only:
        return Verdict(True, True, "passed")

    fixture = {
        name: list(items)
        for name, items in task.tool_fixture.items()
        if isinstance(items, list)
    }

    def fixture_tool(declaration: ToolDecl, _arguments: dict[str, Any]) -> Any:
        value = task.tool_fixture[declaration.name]
        if isinstance(value, list):
            return fixture[declaration.name].pop(0)
        return value

    declared_tools = {tool.name for tool in program.tools}
    declared_models = {model.name for model in program.models}
    try:
        result = run(
            program,
            "main",
            None,
            inputs=dict(task.inputs),
            model_bindings={
                name: ScriptedModelProvider(
                    [ModelResponse(content=json.dumps(item)) for item in items]
                )
                for name, items in task.model_fixture.items()
                if name in declared_models
            },
            tool_bindings={
                name: fixture_tool
                for name in task.tool_fixture
                if name in declared_tools
            },
            limits=ExecutionLimits(
                max_function_calls=2000,
                max_steps=64,
                max_model_calls=8,
                max_tool_calls=8,
                deadline_seconds=10,
            ),
        )
    except Exception as exc:  # a candidate may fail in any way; all are a miss
        return Verdict(
            True, False, "run", (f"run/{type(exc).__name__}",), f"Running failed: {exc}"
        )
    if not json_equal(result, task.expected):
        return Verdict(
            True,
            False,
            "result",
            ("spec/wrong-result",),
            f"For inputs {json.dumps(task.inputs)} the program returned "
            f"{json.dumps(result)} but the spec requires {json.dumps(task.expected)}.",
        )
    return Verdict(True, True, "passed")


def extract_program(reply: str) -> str:
    """Take the last fenced block of a model reply, or the reply itself."""

    blocks = _FENCE.findall(reply or "")
    return (blocks[-1] if blocks else reply or "").strip() + "\n"


def reference_solver(directory: str | Path) -> Solver:
    """Answer every task with its stored reference program."""

    root = Path(directory)

    def solve(
        task: SpecTask, _history: Sequence[tuple[str, Verdict]]
    ) -> tuple[str, dict[str, int]]:
        return (root / f"{task.id}.kos").read_text(encoding="utf-8"), {}

    return solve


def build_messages(
    guide: str, task: SpecTask, history: Sequence[tuple[str, Verdict]]
) -> tuple[ChatMessage, ...]:
    """The whole conversation a model solver sees for one attempt."""

    messages = [
        ChatMessage(
            "system",
            "You write programs in the Kosika language. Follow the language "
            "guide exactly; Kosika is not Python, JavaScript, or Rust.\n\n" + guide,
        ),
        ChatMessage(
            "user",
            "Write one complete Kosika program for this spec. Use the exact "
            "names given. Reply with the program in a single ```kosika code "
            "block and nothing else.\n\nSpec: " + task.spec,
        ),
    ]
    for source, verdict in history:
        messages.append(ChatMessage("assistant", f"```kosika\n{source}```"))
        messages.append(
            ChatMessage(
                "user",
                "That program was rejected:\n"
                f"{verdict.feedback}\n\n"
                "Reply with the full corrected program in a single ```kosika "
                "code block and nothing else.",
            )
        )
    return tuple(messages)


def model_solver(
    provider: ModelProvider,
    guide: str,
    *,
    model: str | None = None,
    max_output_tokens: int = 1200,
) -> Solver:
    """Ask a model for a whole program, showing it earlier rejections."""

    def solve(
        task: SpecTask, history: Sequence[tuple[str, Verdict]]
    ) -> tuple[str, dict[str, int]]:
        response = provider.complete(
            ModelRequest(
                messages=build_messages(guide, task, history),
                model=model,
                temperature=0.0,
                max_output_tokens=max_output_tokens,
            )
        )
        usage = response.usage
        return extract_program(response.content or ""), {
            "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        }

    return solve


def command_solver(
    command: Sequence[str],
    guide: str,
    *,
    timeout: float = 600.0,
) -> Solver:
    """Ask an external command-line model for a whole program.

    The conversation is written to the command's standard input as plain text
    and the reply is read from its standard output. The command runs without a
    shell, in an empty temporary directory, so an agentic tool started this way
    has no project files in front of it.
    """

    if not command:
        raise SpecBenchmarkError("A solver command cannot be empty")
    executable = shutil.which(command[0])
    if executable is None:
        raise SpecBenchmarkError(f"Solver command {command[0]!r} was not found")
    argv = [executable, *command[1:]]

    def solve(
        task: SpecTask, history: Sequence[tuple[str, Verdict]]
    ) -> tuple[str, dict[str, int]]:
        prompt = "\n\n".join(
            f"[{message.role}]\n{message.content}"
            for message in build_messages(guide, task, history)
        )
        with tempfile.TemporaryDirectory(prefix="kosika-solver-") as directory:
            completed = subprocess.run(
                argv,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=directory,
                check=False,
            )
        if completed.returncode != 0 and not completed.stdout.strip():
            raise RuntimeError(
                f"solver command exited {completed.returncode}: "
                f"{completed.stderr.strip()[-300:]}"
            )
        return extract_program(completed.stdout), {}

    return solve


def run_task(task: SpecTask, solver: Solver, *, max_attempts: int = 2) -> TaskResult:
    if not 1 <= max_attempts <= 8:
        raise SpecBenchmarkError("max_attempts must be from 1 through 8")
    history: list[tuple[str, Verdict]] = []
    attempts: list[Attempt] = []
    for index in range(1, max_attempts + 1):
        started = time.monotonic()
        try:
            source, usage = solver(task, tuple(history))
            verdict = judge(task, source)
        except SpecBenchmarkError:
            raise
        except Exception as exc:  # a provider failure ends this task, not the run
            source, usage = "", {}
            verdict = Verdict(
                False, False, "solver", (f"solver/{type(exc).__name__}",), str(exc)
            )
        attempts.append(
            Attempt(
                index,
                verdict,
                round(time.monotonic() - started, 3),
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
                source,
            )
        )
        if verdict.passed or verdict.stage == "solver":
            break
        history.append((source, verdict))
    return TaskResult(task.id, task.level, attempts)


def run_benchmark(
    tasks: Sequence[SpecTask],
    solver: Solver,
    *,
    max_attempts: int = 2,
    progress: Callable[[TaskResult], None] | None = None,
) -> dict[str, Any]:
    results = []
    for task in tasks:
        result = run_task(task, solver, max_attempts=max_attempts)
        results.append(result)
        if progress is not None:
            progress(result)
    return report(results, max_attempts=max_attempts)


def _rate(count: int, total: int) -> float:
    return round(count / total, 3) if total else 0.0


def report(results: Sequence[TaskResult], *, max_attempts: int) -> dict[str, Any]:
    total = len(results)
    levels: dict[str, dict[str, Any]] = {}
    codes: Counter[str] = Counter()
    stages: Counter[str] = Counter()
    for result in results:
        level = levels.setdefault(
            result.level, {"tasks": 0, "first_try": 0, "passed": 0}
        )
        level["tasks"] += 1
        level["first_try"] += int(result.first_try)
        level["passed"] += int(result.passed)
        for attempt in result.attempts:
            if not attempt.verdict.passed:
                stages[attempt.verdict.stage] += 1
                codes.update(attempt.verdict.codes)
    first_try = sum(result.first_try for result in results)
    passed = sum(result.passed for result in results)
    checked_first = sum(
        bool(result.attempts) and result.attempts[0].verdict.checked
        for result in results
    )
    attempts = [attempt for result in results for attempt in result.attempts]
    return {
        "schema": SPEC_REPORT_SCHEMA,
        "tasks": total,
        "max_attempts": max_attempts,
        "checked_first_try": checked_first,
        "passed_first_try": first_try,
        "passed": passed,
        "checked_first_try_rate": _rate(checked_first, total),
        "passed_first_try_rate": _rate(first_try, total),
        "passed_rate": _rate(passed, total),
        "levels": dict(sorted(levels.items())),
        "failure_stages": dict(stages.most_common()),
        "failure_codes": dict(codes.most_common()),
        "usage": {
            "attempts": len(attempts),
            "prompt_tokens": sum(item.prompt_tokens for item in attempts),
            "completion_tokens": sum(item.completion_tokens for item in attempts),
            "seconds": round(sum(item.seconds for item in attempts), 3),
        },
        "results": [
            {
                "id": result.id,
                "level": result.level,
                "passed": result.passed,
                "first_try": result.first_try,
                "attempts": [
                    {
                        "index": attempt.index,
                        "stage": attempt.verdict.stage,
                        "codes": list(attempt.verdict.codes),
                        "feedback": attempt.verdict.feedback,
                        "seconds": attempt.seconds,
                        "prompt_tokens": attempt.prompt_tokens,
                        "completion_tokens": attempt.completion_tokens,
                        "source": attempt.source,
                    }
                    for attempt in result.attempts
                ],
            }
            for result in results
        ],
    }


def to_text(payload: Mapping[str, Any]) -> str:
    lines = [
        f"tasks                : {payload['tasks']}",
        f"checked first try    : {payload['checked_first_try']} "
        f"({payload['checked_first_try_rate']:.3f})",
        f"passed first try     : {payload['passed_first_try']} "
        f"({payload['passed_first_try_rate']:.3f})",
        f"passed in {payload['max_attempts']} attempt(s): {payload['passed']} "
        f"({payload['passed_rate']:.3f})",
    ]
    for level, counts in payload["levels"].items():
        lines.append(
            f"  {level:<6} {counts['passed']}/{counts['tasks']} passed, "
            f"{counts['first_try']} first try"
        )
    if payload["failure_codes"]:
        lines.append("failed attempts by code:")
        lines += [
            f"  {count:>3}  {code}" for code, count in payload["failure_codes"].items()
        ]
    failed = [item["id"] for item in payload["results"] if not item["passed"]]
    if failed:
        lines.append("not passed: " + ", ".join(failed))
    usage = payload["usage"]
    lines.append(
        f"usage: {usage['attempts']} attempts, {usage['prompt_tokens']} prompt and "
        f"{usage['completion_tokens']} completion tokens, {usage['seconds']}s"
    )
    return "\n".join(lines)
