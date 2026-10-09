"""Tests written in Kosika.

A program may hold tests next to the code they exercise:

    test "a double charge goes to billing" {
      answers assistant: [{label: "billing", confidence: 0.9}]
      result = main({text: "My card was charged twice."})
      expect result.queue == "billing"
    }

A test body is a list of lines, run in order:

`answers NAME: [record, ...]`
    The answers a declared model, or a read-only tool, gives in the order it
    is asked. A test never calls a real model or a real tool. Anything asked
    with no answer left fails the test, and so do answers that were never
    used.
`name = expression` or `name: Type = expression`
    Binds a value. The expression may call a function, or run a workflow by
    calling it like a function.
`expect expression`
    The expression must be `true`.

Tests are not part of the program that runs: `program_payload` leaves them
out, so adding or changing a test never changes a program's hash, its traces,
or its approvals. A test can only reach what a run with scripted answers can
reach. A tool that changes the world (`access: execute`) cannot be scripted,
because running one needs a recorded human approval.

`check_tests` checks every test's names and types without running anything;
the module loader calls it, so `kosika check` reports a mistake in a test.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .ast import (
    BinaryExpr,
    FunctionCallExpr,
    Program,
    TestDecl,
    TestStep,
    ToolDecl,
    WorkflowDecl,
)
from .checker import CheckError, _Env, _expression_type
from .execution import ExecutionControl, ExecutionLimits
from .function_types import assignable
from .memory import MemoryStore, bind_memory_tools
from .model import ModelResponse, ScriptedModelProvider
from .runtime import _evaluate_expression, _FunctionRegistry, run
from .sugar import resolve_unnamed

_COMPARISONS = ("==", "!=", "<", "<=", ">", ">=")


@dataclass(frozen=True)
class TestResult:
    name: str
    passed: bool
    message: str = ""
    line: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "message": self.message,
            "line": self.line,
        }


class _Failure(Exception):
    def __init__(self, message: str, line: int):
        super().__init__(message)
        self.line = line


def _show(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _workflow_run(
    program: Program, step: TestStep
) -> tuple[WorkflowDecl, dict[str, Any]] | None:
    """The workflow a `name = call(...)` line runs, with its named arguments."""

    call = step.value
    if not isinstance(call, FunctionCallExpr):
        return None
    workflow = next(
        (item for item in program.workflows if item.name == call.function_name), None
    )
    if workflow is None:
        return None
    parameters = [parameter.name for parameter in workflow.parameters]
    arguments = dict(resolve_unnamed(call.arguments, parameters))
    if sorted(arguments) != sorted(parameters):
        wanted = ", ".join(parameters) or "nothing"
        raise _Failure(f"workflow {workflow.name!r} takes {wanted}", step.line)
    return workflow, arguments


def _check_one(program: Program, test: TestDecl) -> None:
    """Check a test's names and types. Runs nothing."""

    where = f"test {test.name}"
    models = {model.name for model in program.models if model.provider is None}
    tools = {tool.name: tool for tool in program.tools}
    seen: set[str] = set()
    for name, _ in test.answers:
        if name in seen:
            raise _Failure(f"answers names {name!r} more than once", test.line)
        seen.add(name)
        if name in models:
            continue
        tool = tools.get(name)
        if tool is None:
            known = ", ".join(sorted(models | set(tools))) or "none"
            raise _Failure(
                f"answers names {name!r}, which is not a declared model or "
                f"tool; declared: {known}",
                test.line,
            )
        if tool.access != "observe":
            raise _Failure(
                f"answers cannot script tool {name!r}: it changes the world, "
                "and running it needs a recorded approval",
                test.line,
            )
    types = _Env()
    for step in test.steps:
        try:
            if step.kind == "expect":
                kind = _expression_type(step.value, types, program, step_name=where)
                if kind != "Bool":
                    raise _Failure(
                        f"expect needs a Bool, got {kind!r}: {step.text}", step.line
                    )
                continue
            if step.name in types:
                raise _Failure(f"{step.name!r} is already bound", step.line)
            target = _workflow_run(program, step)
            if target is None:
                found = _expression_type(
                    step.value,
                    types,
                    program,
                    step_name=where,
                    expected_type=step.type_name,
                )
                if step.type_name is not None and not assignable(found, step.type_name):
                    raise _Failure(
                        f"{step.name!r} expects {step.type_name!r}, got {found!r}",
                        step.line,
                    )
                types[step.name] = step.type_name or found
                continue
            workflow, arguments = target
            parameters = {p.name: p.type_name for p in workflow.parameters}
            for name, argument in arguments.items():
                given = _expression_type(
                    argument,
                    types,
                    program,
                    step_name=where,
                    expected_type=parameters[name],
                )
                if not assignable(given, parameters[name]):
                    raise _Failure(
                        f"workflow {workflow.name!r} argument {name!r} expects "
                        f"{parameters[name]!r}, got {given!r}",
                        step.line,
                    )
            types[step.name] = workflow.output_type
        except CheckError as exc:
            raise _Failure(str(exc), step.line) from exc


def check_tests(program: Program) -> None:
    """Raise `CheckError` for the first test that is not well formed."""

    for test in program.tests:
        try:
            _check_one(program, test)
        except _Failure as failure:
            raise CheckError(
                f"Test {test.name!r} line {failure.line}: {failure}"
            ) from failure


class _ScriptedTool:
    """A read-only tool that gives its scripted answers in order."""

    ordered = True

    def __init__(self, name: str, answers: list[Any]):
        self.name, self.answers, self.asked = name, list(answers), 0

    def __call__(self, tool: ToolDecl, arguments: dict[str, Any]) -> Any:
        if self.asked >= len(self.answers):
            raise RuntimeError(f"Scripted tool {self.name!r} has no answer left")
        self.asked += 1
        return self.answers[self.asked - 1]


class _Counted:
    """A real model, with a count of how often it was asked."""

    def __init__(self, provider: Any):
        self.provider, self.calls = provider, 0

    def complete(self, request: Any) -> Any:
        self.calls += 1
        return self.provider.complete(request)


def _run_one(
    program: Program,
    test: TestDecl,
    live: Mapping[str, Any] | None = None,
) -> None:
    """Run one test. With `live` models it is an eval: the models answer
    for themselves and only tools are scripted."""

    _check_one(program, test)
    registry = _FunctionRegistry(program.functions, ExecutionControl(ExecutionLimits()))
    values: dict[str, Any] = {}

    def evaluate(expression: Any, scope: dict[str, Any] | None = None) -> Any:
        return _evaluate_expression(
            expression,
            values if scope is None else scope,
            program,
            registry,
            [],
            depth=0,
        )

    declared = [model.name for model in program.models if model.provider is None]
    scripts: dict[str, list[Any]] = {name: [] for name in declared}
    tools: dict[str, _ScriptedTool] = {}
    for name, expression in test.answers:
        answers = evaluate(expression, {})
        if not isinstance(answers, list) or not all(
            isinstance(answer, dict) for answer in answers
        ):
            raise _Failure(f"answers for {name!r} must be a list of records", test.line)
        if name in scripts:
            if live is not None:
                raise _Failure(
                    f"an eval asks the real model, so it cannot script "
                    f"answers for {name!r}",
                    test.line,
                )
            scripts[name] = answers
        else:
            tools[name] = _ScriptedTool(name, answers)
    from .system_tools import bind_kosika_tools

    memory_tools = {
        **bind_kosika_tools(program),
        **bind_memory_tools(program, None, store=MemoryStore()),
    }
    providers: dict[str, Any] = dict(live or {}) or {
        name: ScriptedModelProvider(
            [ModelResponse(content=json.dumps(answer)) for answer in answers]
        )
        for name, answers in scripts.items()
    }

    for step in test.steps:
        try:
            if step.kind == "expect":
                if evaluate(step.value) is True:
                    continue
                detail = ""
                expression = step.value
                if (
                    isinstance(expression, BinaryExpr)
                    and expression.operator in _COMPARISONS
                ):
                    detail = (
                        f" (left is {_show(evaluate(expression.left))}, "
                        f"right is {_show(evaluate(expression.right))})"
                    )
                raise _Failure(f"expected {step.text}{detail}", step.line)
            target = _workflow_run(program, step)
            if target is None:
                values[step.name] = evaluate(step.value)
                continue
            workflow, arguments = target
            values[step.name] = run(
                program,
                workflow.name,
                None,
                inputs={name: evaluate(value) for name, value in arguments.items()},
                model_bindings=providers,
                # Each test starts with empty memories of its own, kept only
                # for the test, so tests never read or write a real store.
                tool_bindings={**memory_tools, **tools},
            )
        except _Failure:
            raise
        except (CheckError, RuntimeError, ValueError, PermissionError) as exc:
            raise _Failure(str(exc), step.line) from exc

    unused = {
        **(
            {}
            if live is not None
            else {
                name: len(scripts[name]) - len(p.requests)
                for name, p in providers.items()
            }
        ),
        **{name: len(tool.answers) - tool.asked for name, tool in tools.items()},
    }
    for name, count in unused.items():
        if count > 0:
            raise _Failure(
                f"{count} answer(s) for {name!r} were never asked for", test.line
            )


def measure(
    program: Program,
    bind: Callable[[str], Mapping[str, Any]],
    models: Sequence[str],
    *,
    repeat: int = 1,
    only: str | None = None,
) -> dict[str, Any]:
    """Run the program's evals on each model and say how each one did.

    `bind(name)` gives the providers to use when every declared model is
    answered by the model called `name`. Each eval runs `repeat` times on
    each model, because a model does not answer the same way twice.
    """

    evals = [
        test
        for test in program.tests
        if test.live and (only is None or test.name == only)
    ]
    report: dict[str, Any] = {
        "evals": [test.name for test in evals],
        "repeat": repeat,
        "models": [],
    }
    for name in models:
        providers = bind(name)
        rows: list[dict[str, Any]] = []
        for test in evals:
            passed, calls, seconds, why = 0, 0, 0.0, ""
            for _ in range(repeat):
                # Counted afresh each run, and counted whether or not the
                # eval passes: a failed attempt still cost its model calls.
                counted = {key: _Counted(value) for key, value in providers.items()}
                started = time.monotonic()
                try:
                    _run_one(program, test, counted)
                    passed += 1
                except _Failure as failure:
                    why = why or f"line {failure.line}: {failure}"
                seconds += time.monotonic() - started
                calls += sum(item.calls for item in counted.values())
            rows.append(
                {
                    "name": test.name,
                    "passed": passed,
                    "runs": repeat,
                    "model_calls": calls,
                    "seconds": round(seconds, 1),
                    "first_failure": why,
                }
            )
        report["models"].append(
            {
                "model": name,
                "passed": sum(row["passed"] for row in rows),
                "runs": repeat * len(evals),
                "model_calls": sum(row["model_calls"] for row in rows),
                "seconds": round(sum(row["seconds"] for row in rows), 1),
                "evals": rows,
            }
        )
    return report


def measure_to_text(report: dict[str, Any]) -> str:
    if not report["evals"]:
        return (
            "No evals found. Write one like a test, with the word eval:\n"
            '  eval "it finds the date" { ... expect a.text.contains("Tuesday") }'
        )
    models = report["models"]
    width = max(len(entry["model"]) for entry in models) + 2
    lines = [
        f"Measured {len(report['evals'])} eval(s), {report['repeat']} time(s) each",
        "",
        f"{'model':<{width}}{'passed':<12}{'seconds':<10}model calls",
    ]
    for entry in models:
        score = f"{entry['passed']} of {entry['runs']}"
        lines.append(
            f"{entry['model']:<{width}}{score:<12}{entry['seconds']:<10}"
            f"{entry['model_calls']}"
        )
    name_width = min(max(len(name) for name in report["evals"]), 48) + 2
    lines += [
        "",
        f"{'eval':<{name_width}}" + "".join(f"{e['model']:<{width}}" for e in models),
    ]
    for index, name in enumerate(report["evals"]):
        cells = "".join(
            f"{str(e['evals'][index]['passed']) + ' of ' + str(e['evals'][index]['runs']):<{width}}"
            for e in models
        )
        lines.append(f"{name[:48]:<{name_width}}{cells}")
    failures = [
        f"  {entry['model']}, {row['name']}: {row['first_failure'][:300]}"
        for entry in models
        for row in entry["evals"]
        if row["first_failure"]
    ]
    if failures:
        lines += ["", "First failure of each eval that did not always pass:", *failures]
    return "\n".join(lines)


def run_tests(program: Program, *, only: str | None = None) -> list[TestResult]:
    """Run the program's tests, or the one named `only`, and report each.

    Evals are not tests: they ask a real model, so they are left to
    `measure`.
    """

    results: list[TestResult] = []
    for test in program.tests:
        if test.live or (only is not None and test.name != only):
            continue
        try:
            _run_one(program, test)
        except _Failure as failure:
            results.append(TestResult(test.name, False, str(failure), failure.line))
        else:
            results.append(TestResult(test.name, True, line=test.line))
    return results


def to_text(results: list[TestResult]) -> str:
    lines = []
    for result in results:
        lines.append(f"{'PASS' if result.passed else 'FAIL'}  {result.name}")
        if not result.passed:
            lines.append(f"      line {result.line}: {result.message}")
    failed = sum(not result.passed for result in results)
    lines.append(
        f"{len(results) - failed} passed, {failed} failed"
        if results
        else "No tests found"
    )
    return "\n".join(lines)
