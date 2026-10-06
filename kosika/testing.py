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
from dataclasses import dataclass
from typing import Any

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

    def __init__(self, name: str, answers: list[Any]):
        self.name, self.answers, self.asked = name, list(answers), 0

    def __call__(self, tool: ToolDecl, arguments: dict[str, Any]) -> Any:
        if self.asked >= len(self.answers):
            raise RuntimeError(f"Scripted tool {self.name!r} has no answer left")
        self.asked += 1
        return self.answers[self.asked - 1]


def _run_one(program: Program, test: TestDecl) -> None:
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
            scripts[name] = answers
        else:
            tools[name] = _ScriptedTool(name, answers)
    providers = {
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
                tool_bindings=tools,
            )
        except _Failure:
            raise
        except (CheckError, RuntimeError, ValueError, PermissionError) as exc:
            raise _Failure(str(exc), step.line) from exc

    unused = {
        **{name: len(scripts[name]) - len(p.requests) for name, p in providers.items()},
        **{name: len(tool.answers) - tool.asked for name, tool in tools.items()},
    }
    for name, count in unused.items():
        if count > 0:
            raise _Failure(
                f"{count} answer(s) for {name!r} were never asked for", test.line
            )


def run_tests(program: Program, *, only: str | None = None) -> list[TestResult]:
    """Run the program's tests, or the one named `only`, and report each."""

    results: list[TestResult] = []
    for test in program.tests:
        if only is not None and test.name != only:
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
