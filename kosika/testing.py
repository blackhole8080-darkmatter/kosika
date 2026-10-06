"""Tests written in Kosika.

A program may hold tests next to the code they exercise:

    test "a double charge goes to billing" {
      answers assistant: [{label: "billing", confidence: 0.9}]
      result = main({text: "My card was charged twice."})
      expect result.queue == "billing"
    }

A test body is a list of lines, run in order:

`answers MODEL: [record, ...]`
    The answers a declared model gives, in the order it is asked. No real
    model is called by a test. A model with no `answers` line fails the test
    if it is asked anything, and so do answers that were never used.
`name = expression`
    Binds a value. The expression may call a function, or run a workflow by
    calling it like a function.
`expect expression`
    The expression must be `true`.

Tests are not part of the program that runs: `program_payload` leaves them
out, so adding or changing a test never changes a program's hash, its traces,
or its approvals. A test can only reach what a run with scripted models can
reach. It cannot execute a tool.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .ast import BinaryExpr, FunctionCallExpr, Program, TestDecl, WorkflowDecl
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


def _run_one(program: Program, test: TestDecl) -> None:
    where = f"test {test.name}"
    workflows: dict[str, WorkflowDecl] = {w.name: w for w in program.workflows}
    registry = _FunctionRegistry(program.functions, ExecutionControl(ExecutionLimits()))
    values: dict[str, Any] = {}
    types = _Env()

    def evaluate(expression: Any) -> Any:
        return _evaluate_expression(expression, values, program, registry, [], depth=0)

    declared = [model.name for model in program.models if model.provider is None]
    scripts: dict[str, list[ModelResponse]] = {name: [] for name in declared}
    for model, expression in test.answers:
        if model not in scripts:
            known = ", ".join(sorted(declared)) or "none"
            raise _Failure(
                f"answers names unknown model {model!r}; declared: {known}", test.line
            )
        answers = _evaluate_expression(expression, {}, program, registry, [], depth=0)
        if not isinstance(answers, list) or not all(
            isinstance(answer, dict) for answer in answers
        ):
            raise _Failure(
                f"answers for {model!r} must be a list of records", test.line
            )
        scripts[model].extend(
            ModelResponse(content=json.dumps(answer)) for answer in answers
        )
    providers = {
        name: ScriptedModelProvider(responses) for name, responses in scripts.items()
    }

    for step in test.steps:
        try:
            if step.kind == "expect":
                kind = _expression_type(step.value, types, program, step_name=where)
                if kind != "Bool":
                    raise _Failure(
                        f"expect needs a Bool, got {kind!r}: {step.text}", step.line
                    )
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
            if step.name in types:
                raise _Failure(f"{step.name!r} is already bound", step.line)
            call = step.value
            workflow = (
                workflows.get(call.function_name)
                if isinstance(call, FunctionCallExpr)
                else None
            )
            if workflow is None or not isinstance(call, FunctionCallExpr):
                found = _expression_type(
                    call,
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
                values[step.name] = evaluate(call)
                continue
            parameters = {p.name: p.type_name for p in workflow.parameters}
            arguments = dict(resolve_unnamed(call.arguments, list(parameters)))
            if sorted(arguments) != sorted(parameters):
                wanted = ", ".join(parameters) or "nothing"
                raise _Failure(f"workflow {workflow.name!r} takes {wanted}", step.line)
            inputs: dict[str, Any] = {}
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
                inputs[name] = evaluate(argument)
            values[step.name] = run(
                program, workflow.name, None, inputs=inputs, model_bindings=providers
            )
            types[step.name] = workflow.output_type
        except _Failure:
            raise
        except (CheckError, RuntimeError, ValueError) as exc:
            raise _Failure(str(exc), step.line) from exc

    for name, provider in providers.items():
        unused = len(scripts[name]) - len(provider.requests)
        if unused > 0:
            raise _Failure(
                f"{unused} answer(s) for {name!r} were never asked for", test.line
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
