import hashlib
import json
import os
import tempfile
import threading
from copy import deepcopy
from dataclasses import asdict, dataclass
from dataclasses import replace as dataclass_replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from .agent import ApprovalRequiredError, ToolExecutor
from .agent_steps import call_agent, validate_agent_transcript
from .ast import (
    AgentCall,
    ArrayExpr,
    Assignment,
    BinaryExpr,
    Block,
    BreakExpr,
    ConditionalExpr,
    ContinueExpr,
    Expression,
    Field,
    FieldExpr,
    ForExpr,
    FunctionCall,
    FunctionCallExpr,
    FunctionDecl,
    FunctionRefExpr,
    IndexExpr,
    LiteralExpr,
    LocalBinding,
    MatchExpr,
    MethodCallExpr,
    ModelCall,
    Program,
    RecordExpr,
    RepeatExpr,
    ReturnExpr,
    ToolCall,
    ToolDecl,
    UnaryExpr,
    VariableExpr,
    WorkflowCall,
    function_body,
    program_payload,
)
from .builtin_methods import call as call_method
from .builtin_methods import resolve_for_value as resolve_method_for_value
from .checker import check
from .controls import ExecutionError
from .durable import ExecutionSession
from .evidence import EvidenceError, answer_schema, split, verify
from .execution import ExecutionControl, ExecutionLimits, within_budget
from .function_types import public_type
from .intelligence_runtime import FunctionResultCache, IntelligenceRecorder
from .manifest import ManifestError, MCPManifest, verify_server
from .mcp_stdio import MCPStdioClient
from .model import ModelProvider
from .model_steps import RejectedAnswer, call_model
from .schema import (
    schema_for_record,
    schema_for_tool_input,
    schema_for_type,
    schemas_equal,
    validate,
)
from .sugar import resolve_unnamed
from .thinking import ThinkingAnswerError, check_answer
from .trace_replay import replay_trace


class _BreakLoop(Exception):
    """Raised by ``break`` to leave the innermost loop."""


class _FunctionReturn(Exception):
    """Raised by an early ``return`` to leave the enclosing function."""

    def __init__(self, value: Any) -> None:
        super().__init__()
        self.value = value


class _ContinueLoop(Exception):
    """Raised by ``continue`` to skip to the next loop iteration."""


class _FunctionRegistry(dict[str, FunctionDecl]):
    """Pure execution bounds shared by steps in one workflow invocation."""

    def __init__(
        self,
        declarations: Sequence[FunctionDecl],
        control: ExecutionControl,
        cache: FunctionResultCache | None = None,
        intel: IntelligenceRecorder | None = None,
    ):
        super().__init__((item.name, item) for item in declarations)
        self.active: list[str] = []
        self.control = control
        self.cache = cache
        self.intel = intel


@dataclass(frozen=True)
class MockMCP:
    """A deterministic fallback used by the original v0.1 example."""

    def call(self, source: str, operation: str) -> dict:
        if source == "local_mock" and operation == "get_disk_info":
            return {"mount": "/", "used_percent": 42.0}
        if source == "local_mock" and operation == "get_structured_info":
            return {
                "mount": "/",
                "tags": ["disk", "local"],
                "metadata": {"name": "root-disk"},
            }
        raise RuntimeError(
            f"Mock MCP operation is not allowlisted: {source}/{operation}"
        )


def _event(event: str, **data: object) -> dict:
    return {
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **data,
    }


def _value_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return left.keys() == right.keys() and all(
            _value_equal(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _value_equal(a, b) for a, b in zip(left, right)
        )
    return bool(left == right)


def _format_number(value: int | float) -> str:
    """Render a Number for concatenation without a host-locale surprise.

    A whole float keeps no trailing ``.0`` so ``"n=" + 5.0`` reads as ``n=5``,
    matching how the value is written in source.
    """

    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


class _Scope(dict):
    """A dict-compatible environment view over a chain of enclosing scopes.

    Reads fall through to the parent, so a loop body can see an outer `var`.
    `assign` walks back up to whichever scope actually owns the name and writes
    there, which is what makes `for x in v { total = total + x }` accumulate
    instead of writing into a throwaway copy.

    Subclasses ``dict`` so every existing ``name in environment`` and
    ``environment[name]`` call keeps working; only scope creation needs to know
    this class exists, and those go through `child()`.
    """

    __slots__ = ("_parent",)

    def __init__(self, source=None, parent: "_Scope | None" = None):
        super().__init__(source or {})
        self._parent = parent

    def __contains__(self, key: object) -> bool:
        if super().__contains__(key):
            return True
        return self._parent is not None and key in self._parent

    def __missing__(self, key: str) -> Any:
        if self._parent is None:
            raise KeyError(key)
        return self._parent[key]

    def assign(self, key: str, value: Any) -> None:
        if super().__contains__(key) or self._parent is None:
            self[key] = value
        else:
            self._parent.assign(key, value)

    def child(self) -> "_Scope":
        return _Scope(parent=self)


def _child_scope(environment: Any) -> _Scope:
    """Push a new scope, tolerating a plain dict for existing call paths."""
    return (
        environment.child() if isinstance(environment, _Scope) else _Scope(environment)
    )


def _evaluate_expression(
    expression: Expression | Assignment,
    environment: dict[str, Any],
    program: Program,
    functions: Mapping[str, FunctionDecl],
    function_events: list[dict[str, Any]],
    *,
    depth: int,
) -> Any:
    if isinstance(functions, _FunctionRegistry):
        functions.control.checkpoint()
    if isinstance(expression, MatchExpr):
        subject = _evaluate_expression(
            expression.subject,
            environment,
            program,
            functions,
            function_events,
            depth=depth,
        )
        for arm in expression.arms:
            if arm.pattern is None or _value_equal(subject, arm.pattern.value):
                return _evaluate_expression(
                    arm.value,
                    environment,
                    program,
                    functions,
                    function_events,
                    depth=depth,
                )
        raise RuntimeError("Non-exhaustive match reached execution")
    if isinstance(expression, Assignment):
        # The checker already proved the target exists, is a `var`, and that the
        # value matches its type, so this only has to perform the write.
        if not isinstance(environment, _Scope):
            raise RuntimeError("Assignment outside a scoped function body")
        environment.assign(
            expression.target,
            _evaluate_expression(
                expression.value,
                environment,
                program,
                functions,
                function_events,
                depth=depth,
            ),
        )
        return None
    if isinstance(expression, Block):
        inner = _child_scope(environment)
        for statement in expression.statements:
            if isinstance(statement, LocalBinding):
                value = _evaluate_expression(
                    statement.value,
                    inner,
                    program,
                    functions,
                    function_events,
                    depth=depth,
                )
                if statement.type_name is not None:
                    validate(value, schema_for_type(program, statement.type_name))
                inner[statement.name] = value
            else:
                _evaluate_expression(
                    statement,
                    inner,
                    program,
                    functions,
                    function_events,
                    depth=depth,
                )
        if expression.result is None:
            return None
        return _evaluate_expression(
            expression.result,
            inner,
            program,
            functions,
            function_events,
            depth=depth,
        )
    if isinstance(expression, LiteralExpr):
        return expression.value
    if isinstance(expression, VariableExpr):
        if expression.name not in environment:
            raise RuntimeError(f"Workflow variable {expression.name!r} is unavailable")
        return environment[expression.name]
    if isinstance(expression, FieldExpr):
        base = _evaluate_expression(
            expression.base,
            environment,
            program,
            functions,
            function_events,
            depth=depth,
        )
        if not isinstance(base, dict):
            raise RuntimeError(
                f"Cannot read field {expression.field_name!r} from "
                f"{type(base).__name__}"
            )
        if expression.field_name not in base:
            raise RuntimeError(
                f"Workflow result has no field {expression.field_name!r}"
            )
        return base[expression.field_name]
    if isinstance(expression, IndexExpr):
        base = _evaluate_expression(
            expression.base,
            environment,
            program,
            functions,
            function_events,
            depth=depth,
        )
        index = _evaluate_expression(
            expression.index,
            environment,
            program,
            functions,
            function_events,
            depth=depth,
        )
        if not isinstance(base, list):
            raise RuntimeError(f"Cannot index non-array value {type(base).__name__}")
        if not isinstance(index, int) or isinstance(index, bool):
            raise RuntimeError("Array indexes must be integers")
        try:
            return base[index]
        except IndexError as exc:
            raise RuntimeError(f"Array index {index} is out of range") from exc
    if isinstance(expression, ArrayExpr):
        return [
            _evaluate_expression(
                item,
                environment,
                program,
                functions,
                function_events,
                depth=depth,
            )
            for item in expression.items
        ]
    if isinstance(expression, RecordExpr):
        return {
            name: _evaluate_expression(
                value,
                environment,
                program,
                functions,
                function_events,
                depth=depth,
            )
            for name, value in expression.fields
        }
    if isinstance(expression, MethodCallExpr):
        receiver = _evaluate_expression(
            expression.target,
            environment,
            program,
            functions,
            function_events,
            depth=depth,
        )
        method = resolve_method_for_value(receiver, expression.method)
        method_arguments = {
            name: _evaluate_expression(
                argument,
                environment,
                program,
                functions,
                function_events,
                depth=depth,
            )
            for name, argument in resolve_unnamed(
                expression.arguments, [name for name, _ in method.parameters]
            )
        }
        return call_method(method, receiver, method_arguments)
    if isinstance(expression, UnaryExpr):
        operand = _evaluate_expression(
            expression.operand,
            environment,
            program,
            functions,
            function_events,
            depth=depth,
        )
        return not operand if expression.operator == "not" else -operand
    if isinstance(expression, BinaryExpr):
        left = _evaluate_expression(
            expression.left,
            environment,
            program,
            functions,
            function_events,
            depth=depth,
        )
        if expression.operator == "and" and not left:
            return False
        if expression.operator == "or" and left:
            return True
        right = _evaluate_expression(
            expression.right,
            environment,
            program,
            functions,
            function_events,
            depth=depth,
        )
        if expression.operator == "+":
            # The checker admits Text+Text and Text+Number as concatenation.
            # The runtime must honor exactly what the checker allows, so a
            # Number operand is rendered rather than raising a host TypeError.
            if isinstance(left, str) and isinstance(right, str):
                return left + right
            if (
                isinstance(left, str)
                and isinstance(right, (int, float))
                and not isinstance(right, bool)
            ):
                return left + _format_number(right)
            if (
                isinstance(left, (int, float))
                and isinstance(right, str)
                and not isinstance(left, bool)
            ):
                return _format_number(left) + right
            return left + right
        if expression.operator == "-":
            return left - right
        if expression.operator == "*":
            return left * right
        if expression.operator == "/":
            if right == 0:
                raise RuntimeError("Division by zero")
            return left / right
        if expression.operator == "%":
            if right == 0:
                raise RuntimeError("Remainder by zero")
            # Floored remainder: the result takes the sign of the divisor.
            return left % right
        if expression.operator == "<":
            return left < right
        if expression.operator == "<=":
            return left <= right
        if expression.operator == ">":
            return left > right
        if expression.operator == ">=":
            return left >= right
        if expression.operator in {"==", "!="}:
            equal = _value_equal(left, right)
            return equal if expression.operator == "==" else not equal
        if expression.operator == "and":
            return bool(right)
        if expression.operator == "or":
            return bool(right)
        raise RuntimeError(f"Unknown binary operator {expression.operator!r}")
    if isinstance(expression, ConditionalExpr):
        condition = _evaluate_expression(
            expression.condition,
            environment,
            program,
            functions,
            function_events,
            depth=depth,
        )
        branch = expression.when_true if condition else expression.when_false
        return _evaluate_expression(
            branch,
            environment,
            program,
            functions,
            function_events,
            depth=depth,
        )
    if isinstance(expression, FunctionRefExpr):
        captures = {
            name: _evaluate_expression(
                value, environment, program, functions, function_events, depth=depth
            )
            for name, value in expression.captures
        }
        return {
            "$function": expression.function_name,
            "$type": public_type(functions[expression.function_name]),
            "$captures": deepcopy(captures),
        }
    if isinstance(expression, FunctionCallExpr):
        argument_events: list[dict[str, Any]] = []
        arguments = {
            name: _evaluate_expression(
                value,
                environment,
                program,
                functions,
                argument_events,
                depth=depth,
            )
            for name, value in expression.arguments
        }
        function, captures = _resolve_function(
            expression.function_name, environment, functions
        )
        if set(arguments) & set(captures):
            raise RuntimeError("Cannot override lexical captures")
        call_result, call_events = _execute_function(
            program,
            function,
            {**arguments, **captures},
            functions,
            argument_events=argument_events,
            depth=depth + 1,
        )
        function_events.extend(call_events)
        return call_result
    if isinstance(expression, ForExpr):
        # Functional map: bind item_name per element in a child scope and
        # collect the body results into a new array.
        sequence = _evaluate_expression(
            expression.sequence,
            environment,
            program,
            functions,
            function_events,
            depth=depth,
        )
        if not isinstance(sequence, list):
            raise RuntimeError("for loop requires an array sequence")
        if expression.capped and len(sequence) > expression.limit:
            raise RuntimeError(
                f"for loop exceeded its limit of {expression.limit} iterations"
            )
        collected: list[Any] = []
        for element in sequence:
            if isinstance(functions, _FunctionRegistry):
                functions.control.consume("loop_iterations")
            scope = _child_scope(environment)
            scope[expression.item_name] = element
            if expression.condition is not None and not _evaluate_expression(
                expression.condition,
                scope,
                program,
                functions,
                function_events,
                depth=depth + 1,
            ):
                continue
            try:
                collected.append(
                    _evaluate_expression(
                        expression.body,
                        scope,
                        program,
                        functions,
                        function_events,
                        depth=depth + 1,
                    )
                )
            except _BreakLoop:
                break
            except _ContinueLoop:
                continue
        return collected
    if isinstance(expression, RepeatExpr):
        # Evaluated up to count times; yields the last result.
        raw_count = _evaluate_expression(
            expression.count,
            environment,
            program,
            functions,
            function_events,
            depth=depth,
        )
        if isinstance(raw_count, bool) or not isinstance(raw_count, (int, float)):
            raise RuntimeError("repeat count must be a number")
        count = int(raw_count)
        if count < 0 or count != raw_count:
            raise RuntimeError("repeat count must be a whole number, zero or more")
        result: Any = None
        for _ in range(count):
            if isinstance(functions, _FunctionRegistry):
                functions.control.consume("loop_iterations")
            scope = _child_scope(environment)
            try:
                result = _evaluate_expression(
                    expression.body,
                    scope,
                    program,
                    functions,
                    function_events,
                    depth=depth + 1,
                )
            except _BreakLoop:
                break
            except _ContinueLoop:
                continue
        return result
    if isinstance(expression, BreakExpr):
        raise _BreakLoop
    if isinstance(expression, ContinueExpr):
        raise _ContinueLoop
    if isinstance(expression, ReturnExpr):
        raise _FunctionReturn(
            _evaluate_expression(
                expression.value,
                environment,
                program,
                functions,
                function_events,
                depth=depth,
            )
        )
    raise RuntimeError("Workflow contains an unsupported expression")


def _resolve_function(
    name: str,
    environment: Mapping[str, Any],
    functions: Mapping[str, FunctionDecl],
) -> tuple[FunctionDecl, dict[str, Any]]:
    if name not in environment:
        return functions[name], {}
    closure = environment[name]
    if not isinstance(closure, dict) or set(closure) != {
        "$function",
        "$type",
        "$captures",
    }:
        raise RuntimeError("Invalid function value")
    target = closure["$function"]
    if not isinstance(target, str) or target not in functions:
        raise RuntimeError("Unknown closure target")
    function = functions[target]
    captures = closure["$captures"]
    if closure["$type"] != public_type(function) or not isinstance(captures, dict):
        raise RuntimeError("Closure signature mismatch")
    if set(captures) != {
        p.name for p in function.parameters if p.name.startswith("$capture:")
    }:
        raise RuntimeError("Closure capture mismatch")
    return function, captures


def _call_declared_tool(
    tool: ToolDecl,
    arguments: dict[str, Any],
    input_schema: dict,
    output_schema: dict,
    mcp_commands: dict[str, Sequence[str]] | None,
    manifest: MCPManifest | None,
) -> tuple[object, str]:
    if mcp_commands is not None and tool.source in mcp_commands:
        if manifest is None:
            raise ManifestError(
                "Live MCP execution requires a pinned manifest; pass --mcp-manifest"
            )
        if manifest.source != tool.source:
            raise ManifestError(
                f"Pinned manifest source {manifest.source!r} does not match tool source {tool.source!r}"
            )
        pinned = manifest.tools.get(tool.operation)
        if pinned is None:
            raise ManifestError(
                f"No pinned manifest entry for MCP tool {tool.operation!r}"
            )
        if not schemas_equal(pinned.input_schema, input_schema):
            raise ManifestError(
                f"Pinned input schema for {tool.operation!r} does not match "
                f"Kosika record {tool.input!r}"
            )
        if not schemas_equal(pinned.output_schema, output_schema):
            raise ManifestError(
                f"Pinned output schema for {tool.operation!r} does not match Kosika record {tool.output!r}"
            )
        with MCPStdioClient(mcp_commands[tool.source]) as client:
            verify_server(client, manifest)
            advertised = client.require_tool(tool.operation)
            result = client.call_tool(
                advertised.name,
                arguments,
                input_schema=pinned.input_schema,
                output_schema=output_schema,
            )
            return result, "stdio"
    return MockMCP().call(tool.source, tool.operation), "mock"


def _parameter_schema(program: Program, fields: Sequence[Field]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            field.name: schema_for_type(program, field.type_name) for field in fields
        },
        "required": [field.name for field in fields],
        "additionalProperties": False,
    }


def _execute_function(
    program: Program,
    function: FunctionDecl,
    arguments: dict[str, Any],
    functions: Mapping[str, FunctionDecl],
    *,
    argument_events: list[dict[str, Any]],
    depth: int,
) -> tuple[Any, list[dict[str, Any]]]:
    if not isinstance(functions, _FunctionRegistry):
        raise RuntimeError("Function execution requires a bounded registry")
    # Optional pure-function result cache.
    if functions.cache is not None:
        cached = functions.cache.get(function.name, arguments)
        if cached is not None:
            functions.control.checkpoint()
            # A cache hit is still a real invocation, so it is published as a
            # normal call/return pair marked ``cache_hit``. Omitting the event
            # would leave a trace that silently drops every cached execution,
            # and would hide the operation from the intelligence recorder.
            input_schema = _parameter_schema(program, function.parameters)
            output_schema = schema_for_type(program, function.output_type)
            return cached, [
                _event(
                    "function_call",
                    function=function.name,
                    arguments=arguments,
                    input_schema=input_schema,
                    output_schema=output_schema,
                    expression_events=[],
                    cache_hit=True,
                ),
                _event(
                    "function_return",
                    function=function.name,
                    result=cached,
                    events=[],
                    cache_hit=True,
                ),
            ]
    limit = function.recursion_limit or 1
    if functions.active.count(function.name) >= limit:
        raise RuntimeError(f"Recursion limit {limit} exceeded for {function.name!r}")
    functions.control.consume("function_calls")
    functions.active.append(function.name)
    try:
        return _execute_function_body(
            program,
            function,
            arguments,
            functions,
            argument_events=argument_events,
            depth=depth,
        )
    except (Exception, KeyboardInterrupt):
        if not functions.control.failure_stack:
            functions.control.failure_stack = list(functions.active)
        raise
    finally:
        functions.active.pop()


def _execute_function_body(
    program: Program,
    function: FunctionDecl,
    arguments: dict[str, Any],
    functions: Mapping[str, FunctionDecl],
    *,
    argument_events: list[dict[str, Any]],
    depth: int,
) -> tuple[Any, list[dict[str, Any]]]:
    if depth >= 128:
        raise RuntimeError("Function call depth exceeds 128")
    input_schema = _parameter_schema(program, function.parameters)
    output_schema = schema_for_type(program, function.output_type)
    validate(arguments, input_schema)
    body_events: list[dict[str, Any]] = []
    local_environment = _Scope(arguments)
    try:
        for binding in function_body(function):
            if not isinstance(binding, LocalBinding):
                _evaluate_expression(
                    binding,
                    local_environment,
                    program,
                    functions,
                    body_events,
                    depth=depth,
                )
                continue
            value = _evaluate_expression(
                binding.value,
                local_environment,
                program,
                functions,
                body_events,
                depth=depth,
            )
            # An inferred binding has no declared type, so there is no contract to
            # re-check here; the checker already resolved and proved it.
            if binding.type_name is not None:
                validate(value, schema_for_type(program, binding.type_name))
            local_environment[binding.name] = value
        result = _evaluate_expression(
            function.return_value,
            local_environment,
            program,
            functions,
            body_events,
            depth=depth,
        )
    except _FunctionReturn as early:
        # An early return supplies the result; it is validated below
        # exactly like the final one.
        result = early.value
    validate(result, output_schema)
    # Optional pure-function result cache — safe because these functions are
    # deterministic and free of model/tool/host authority.
    if isinstance(functions, _FunctionRegistry) and functions.cache is not None:
        functions.cache.put(function.name, arguments, result)
    return result, [
        _event(
            "function_call",
            function=function.name,
            arguments=arguments,
            input_schema=input_schema,
            output_schema=output_schema,
            expression_events=argument_events,
        ),
        _event(
            "function_return",
            function=function.name,
            result=result,
            events=body_events,
        ),
    ]


def _write_trace(trace_path: str | Path, events: list[dict[str, Any]]) -> None:
    path = Path(trace_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        "\n".join(json.dumps(item, sort_keys=True, allow_nan=False) for item in events)
        + "\n"
    )
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".kosika-trace-",
            delete=False,
        ) as stream:
            temporary = stream.name
            stream.write(payload)
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def run(
    program: Program,
    workflow_name: str,
    trace_path: str | Path | None,
    *,
    mcp_commands: dict[str, Sequence[str]] | None = None,
    manifest: MCPManifest | None = None,
    inputs: dict[str, Any] | None = None,
    model_bindings: Mapping[str, ModelProvider] | None = None,
    tool_bindings: Mapping[str, ToolExecutor] | None = None,
    session: ExecutionSession | None = None,
    limits: ExecutionLimits | None = None,
    cancel: threading.Event | None = None,
    intelligence: IntelligenceRecorder | None = None,
    function_cache: FunctionResultCache | None = None,
    _events_out: list[dict[str, Any]] | None = None,
    _scope: tuple[str, ...] = (),
    _control: ExecutionControl | None = None,
) -> dict[str, Any]:
    if _control is None:
        # The workflow being run may declare a budget, which tightens the
        # caller's limits for the whole run, including workflows it calls.
        declared = next(
            (item.budget for item in program.workflows if item.name == workflow_name),
            (),
        )
        _control = ExecutionControl(
            within_budget(limits or ExecutionLimits(), declared), cancel
        )
    control = _control
    control.scope = (*_scope, workflow_name)
    events: list[dict[str, Any]] = []
    try:
        control.checkpoint()
        result = _run_workflow(
            program,
            workflow_name,
            None,
            mcp_commands=mcp_commands,
            manifest=manifest,
            inputs=inputs,
            model_bindings=model_bindings,
            tool_bindings=tool_bindings,
            session=session,
            _events_out=events,
            _scope=_scope,
            _control=control,
            intelligence=intelligence,
            function_cache=function_cache,
        )
    except (Exception, KeyboardInterrupt) as exc:
        interrupted = isinstance(exc, KeyboardInterrupt)
        code = (
            "cancelled"
            if interrupted
            else (
                "approval_required"
                if isinstance(exc, ApprovalRequiredError)
                else exc.code
                if isinstance(exc, ExecutionError)
                and exc.code
                in {
                    "cancelled",
                    "deadline",
                    "budget",
                    "approval_required",
                    "usage_unknown",
                    "transient",
                }
                else "runtime_error"
            )
        )
        if not events:
            events.append(_event("run_start", workflow=workflow_name, trace_version=5))
        terminal = "run_cancelled" if code == "cancelled" else "run_failed"
        if code == "approval_required":
            terminal = "run_paused"
        events.append(
            _event(
                terminal,
                workflow=workflow_name,
                scope=list(control.scope),
                error={"code": code, "type": type(exc).__name__},
                execution=control.snapshot(),
            )
        )
        if _events_out is not None:
            _events_out.extend(events)
        if trace_path is not None:
            try:
                _write_trace(trace_path, events)
            except (OSError, ValueError, TypeError):
                add_note = getattr(exc, "add_note", None)
                if callable(add_note):
                    add_note("Could not persist the execution failure trace")
        if interrupted:
            control.cancel.set()
            raise ExecutionError("cancelled", "Execution was cancelled") from exc
        raise
    if events and events[-1].get("event") == "run_complete":
        events[-1]["execution"] = control.snapshot()
    if _events_out is not None:
        _events_out.extend(events)
    if trace_path is not None:
        _write_trace(trace_path, events)
    return result


def _entry_point_error(program: Program, workflow_name: str) -> str:
    """Explain a missing entry point, including when a swarm is the real reason.

    A program whose only entry point is a declared swarm has nothing to run.
    Reporting ``Unknown workflow 'main'`` there is worse than unhelpful: it reads
    as a forgotten ``main`` and sends the reader looking for the wrong bug. The
    swarm is the honest answer, so the error says so and names what can be run.
    """

    if program.swarms:
        names = ", ".join(sorted(swarm.name for swarm in program.swarms))
        available = sorted(workflow.name for workflow in program.workflows)
        entry = (
            f"This program declares swarm(s) {names}, and a declared swarm is "
            "not an executable entry point for `run`, so there is nothing to run "
            f"for {workflow_name!r}. A swarm runs only under approval through "
            "`kosika swarm`."
        )
        if available:
            entry += f" Runnable workflow(s): {', '.join(available)}."
        else:
            entry += " This program declares no workflow to run."
        return entry
    return f"Unknown workflow {workflow_name!r}"


def _run_workflow(
    program: Program,
    workflow_name: str,
    trace_path: str | Path | None,
    *,
    mcp_commands: dict[str, Sequence[str]] | None = None,
    manifest: MCPManifest | None = None,
    inputs: dict[str, Any] | None = None,
    model_bindings: Mapping[str, ModelProvider] | None = None,
    tool_bindings: Mapping[str, ToolExecutor] | None = None,
    _events_out: list[dict[str, Any]] | None = None,
    session: ExecutionSession | None = None,
    _scope: tuple[str, ...] = (),
    _control: ExecutionControl,
    intelligence: IntelligenceRecorder | None = None,
    function_cache: FunctionResultCache | None = None,
) -> dict[str, Any]:
    if len(_scope) > 64:
        raise ValueError("Workflow call depth exceeds 32")
    if (
        session is not None
        and session.active
        and session.owner != threading.get_ident()
    ):
        raise RuntimeError("Execution session is already active in another thread")
    if session is not None and not session.active:
        with session.lock():
            return _run_workflow(
                program,
                workflow_name,
                trace_path,
                mcp_commands=mcp_commands,
                manifest=manifest,
                inputs=inputs,
                model_bindings=model_bindings,
                tool_bindings=tool_bindings,
                session=session,
                _events_out=_events_out,
                _scope=_scope,
                _control=_control,
                intelligence=intelligence,
                function_cache=function_cache,
            )
    check(program)
    workflow = next(
        (item for item in program.workflows if item.name == workflow_name), None
    )
    if workflow is None:
        raise ValueError(_entry_point_error(program, workflow_name))
    tools = {tool.name: tool for tool in program.tools}
    agents = {agent.name: agent for agent in program.agents}
    models = {model.name: model for model in program.models}
    workflows = {item.name: item for item in program.workflows}
    functions = _FunctionRegistry(
        program.functions, _control, cache=function_cache, intel=intelligence
    )
    providers = {
        name: _control.provider(provider)
        for name, provider in (model_bindings or {}).items()
    }
    host_tools = dict(tool_bindings or {})
    if set(host_tools) - set(tools):
        raise ValueError("Host supplied bindings for undeclared tools")
    if set(providers) - set(models):
        raise ValueError("Host supplied bindings for undeclared models")
    environment: dict[str, Any] = dict(inputs or {})
    expected_inputs = {parameter.name for parameter in workflow.parameters}
    if set(environment) != expected_inputs:
        raise ValueError(
            f"Workflow requires input parameters {sorted(expected_inputs)!r}"
        )
    for parameter in workflow.parameters:
        validate(
            environment[parameter.name], schema_for_record(program, parameter.type_name)
        )
    json.dumps(environment, allow_nan=False)
    if session is not None and not _scope:
        session.bind(
            {
                "program": program_payload(program),
                "workflow": workflow_name,
                "inputs": environment,
                "manifest": asdict(manifest) if manifest else None,
                "mcp_commands": mcp_commands,
            }
        )
    # Preflight every capability before invoking any tool in the workflow.
    pending = list(workflow.steps)
    visited_workflows = {workflow.name}
    while pending:
        step = pending.pop(0)
        if step.fallback is not None:
            # Both calls may run, so both must be authorized up front.
            pending.append(
                dataclass_replace(step, call=step.fallback, fallback=None, retries=0)
            )
        if isinstance(step.call, WorkflowCall):
            name = step.call.workflow_name
            if name not in visited_workflows:
                visited_workflows.add(name)
                pending.extend(workflows[name].steps)
            continue
        if isinstance(step.call, FunctionCall):
            continue
        if isinstance(step.call, AgentCall):
            asked_agent = agents[step.call.agent_name]
            if asked_agent.model not in providers:
                raise PermissionError(
                    f"Agent {asked_agent.name!r} has no authorized model binding"
                )
            for name in asked_agent.can:
                permitted = tools[name]
                if permitted.access == "execute" and session is None:
                    raise PermissionError(
                        f"Agent tool {name!r} requires a verified approval record"
                    )
                if (
                    name not in host_tools
                    and permitted.source not in (mcp_commands or {})
                    and permitted.source != "local_mock"
                ):
                    raise PermissionError(
                        f"Agent tool {name!r} has no authorized host binding"
                    )
            continue
        if isinstance(step.call, ModelCall):
            # A thinking step's generated model uses the binding of the
            # declared model it names; it has none of its own.
            asked = models[step.call.model_name]
            bound_name = asked.provider or asked.name
            if bound_name not in providers:
                raise PermissionError(
                    f"Model {bound_name!r} has no authorized host binding"
                )
            continue
        tool = tools[step.call.tool_name]
        if tool.access == "execute" and session is None:
            raise PermissionError(
                f"Workflow tool {tool.name!r} requires a verified approval record"
            )
    events = _events_out if _events_out is not None else []
    events.append(
        _event(
            "run_start",
            workflow=workflow.name,
            trace_version=5,
            program_hash=hashlib.sha256(
                json.dumps(program_payload(program), sort_keys=True).encode()
            ).hexdigest(),
            inputs=environment.copy(),
            output_schema=schema_for_record(program, workflow.output_type),
        )
    )
    result: Any = {}
    queue = list(workflow.steps)
    recovered: dict[str, dict[str, str]] = {}
    while queue:
        step = queue.pop(0)
        events_before = len(events)
        try:
            _control.scope = (*_scope, workflow.name, step.name)
            _control.consume("steps")
            expression_events: list[dict[str, Any]] = []
            arguments = {
                name: _evaluate_expression(
                    expression,
                    environment,
                    program,
                    functions,
                    expression_events,
                    # Arguments run before entering the step's function frame.
                    depth=-1,
                )
                for name, expression in step.call.arguments
            }
            scope = (*_scope, workflow.name, step.name)
            action = None
            if isinstance(step.call, WorkflowCall):
                step_output = workflows[step.call.workflow_name].output_type
            elif isinstance(step.call, FunctionCall):
                function, captures = _resolve_function(
                    step.call.function_name, environment, functions
                )
                if set(arguments) & set(captures):
                    raise RuntimeError("Cannot override lexical captures")
                arguments = {**arguments, **captures}
                step_output = function.output_type
            elif isinstance(step.call, ModelCall):
                step_output = models[step.call.model_name].output
            elif isinstance(step.call, AgentCall):
                step_output = models[agents[step.call.agent_name].model or ""].output
            else:
                step_output = tools[step.call.tool_name].output
            if session is not None and not isinstance(step.call, WorkflowCall):
                cached = session.cached(
                    scope, arguments, events[0], schema_for_type(program, step_output)
                )
                if cached is not None:
                    result = cached[-1]["result"]
                    environment[step.binding_name] = result
                    events.extend(cached)
                    continue
            if isinstance(step.call, ToolCall):
                _control.consume("tool_calls")
            _control.checkpoint()
            if session is not None and not isinstance(step.call, WorkflowCall):
                mutation = (
                    tools[step.call.tool_name].name
                    if isinstance(step.call, ToolCall)
                    and tools[step.call.tool_name].access == "execute"
                    else None
                )
                # Check arguments before claiming approval or recording a started step.
                if isinstance(step.call, ToolCall):
                    validate(
                        arguments,
                        schema_for_tool_input(
                            program, tools[step.call.tool_name].input
                        ),
                    )
                action = session.start(scope, mutation, arguments)
            step_offset = len(events)
            events.append(
                _event(
                    "step_start",
                    step=step.name,
                    **(
                        {"recovered_from": recovered[step.name]}
                        if step.name in recovered
                        else {}
                    ),
                )
            )
            if isinstance(step.call, FunctionCall):
                result, function_trace = _execute_function(
                    program,
                    function,
                    arguments,
                    functions,
                    argument_events=expression_events,
                    depth=0,
                )
                events.extend(function_trace)
            elif isinstance(step.call, WorkflowCall):
                child = workflows[step.call.workflow_name]
                events.append(
                    _event(
                        "workflow_call",
                        workflow=child.name,
                        arguments=arguments,
                        expression_events=expression_events,
                    )
                )
                child_events: list[dict[str, Any]] = []
                try:
                    result = run(
                        program,
                        child.name,
                        None,
                        inputs=arguments,
                        model_bindings=providers,
                        tool_bindings=host_tools,
                        mcp_commands=mcp_commands,
                        manifest=manifest,
                        _events_out=child_events,
                        session=session,
                        _scope=scope,
                        _control=_control,
                        intelligence=intelligence,
                        function_cache=function_cache,
                    )
                except (Exception, KeyboardInterrupt):
                    if intelligence is not None:
                        intelligence.record_events(child_events)
                    events.append(
                        _event(
                            "workflow_failed", workflow=child.name, events=child_events
                        )
                    )
                    raise
                if intelligence is not None:
                    intelligence.record_events(child_events)
                events.append(
                    _event(
                        "workflow_return",
                        workflow=child.name,
                        result=result,
                        events=child_events,
                    )
                )
            elif isinstance(step.call, AgentCall):
                asked_agent = agents[step.call.agent_name]
                assert asked_agent.model is not None
                model = models[asked_agent.model]
                input_schema = schema_for_record(program, model.input)
                output_schema = schema_for_record(program, model.output)
                validate(arguments, input_schema)
                call_event = _event(
                    "agent_call",
                    agent=asked_agent.name,
                    model=model.name,
                    arguments=arguments,
                    expression_events=expression_events,
                    input_schema=input_schema,
                    output_schema=output_schema,
                    max_steps=asked_agent.max_steps,
                    tools={
                        name: {
                            "input_schema": schema_for_tool_input(
                                program, tools[name].input
                            ),
                            "output_schema": schema_for_record(
                                program, tools[name].output
                            ),
                        }
                        for name in asked_agent.can
                    },
                )
                events.append(call_event)

                def execute_agent_tool(
                    declaration: ToolDecl, values: dict[str, Any]
                ) -> Any:
                    _control.consume("tool_calls")
                    if declaration.access == "execute":
                        # An ask that reaches an approval-gated tool is gated the
                        # same way a direct call is: claim the action against the
                        # verified approval record, or pause for a human. This is
                        # deliberately not a widening of access, and deliberately
                        # no host-binding exemption - a direct execute tool has
                        # none either, and ask must not become the weaker path.
                        if session is None:
                            raise PermissionError(
                                f"Workflow ask tool {declaration.name!r} requires a "
                                "verified approval record"
                            )
                        inner = (*scope, f"ask:{declaration.name}")
                        action = session.start(inner, declaration.name, values)
                        outcome = _agent_tool_outcome(declaration, values)
                        validate(
                            outcome, schema_for_record(program, declaration.output)
                        )
                        session.complete(
                            inner,
                            [
                                _event(
                                    "tool_return",
                                    agent=asked_agent.name,
                                    tool=declaration.name,
                                    transport="host",
                                    result=outcome,
                                )
                            ],
                            action,
                        )
                        return outcome
                    return _agent_tool_outcome(declaration, values)

                def _agent_tool_outcome(
                    declaration: ToolDecl, values: dict[str, Any]
                ) -> Any:
                    if declaration.name in host_tools:
                        return host_tools[declaration.name](declaration, values)
                    outcome, _ = _call_declared_tool(
                        declaration,
                        values,
                        schema_for_tool_input(program, declaration.input),
                        schema_for_record(program, declaration.output),
                        mcp_commands,
                        manifest,
                    )
                    return outcome

                result, agent_response = call_agent(
                    program,
                    asked_agent,
                    providers[model.name],
                    execute_agent_tool,
                    arguments,
                    output_schema,
                )
                return_event = _event(
                    "agent_return",
                    agent=asked_agent.name,
                    result=result,
                    **agent_response,
                )
                validate_agent_transcript(call_event, return_event)
                events.append(return_event)
            elif isinstance(step.call, ModelCall):
                model = models[step.call.model_name]
                input_schema = schema_for_record(program, model.input)
                output_schema = schema_for_record(program, model.output)
                validate(arguments, input_schema)
                # A step `with evidence` asks the model for the record plus
                # its quotes. The quotes are checked and kept in the trace;
                # the step's value is the record alone.
                asked_schema = output_schema
                call_evidence: dict[str, Any] = {}
                if model.evidence is not None:
                    asked_schema = answer_schema(
                        model.evidence,
                        output_schema,
                        list(output_schema.get("properties", {})),
                    )
                    call_evidence = {"evidence": model.evidence}
                events.append(
                    _event(
                        "model_call",
                        model=model.name,
                        arguments=arguments,
                        expression_events=expression_events,
                        input_schema=input_schema,
                        output_schema=output_schema,
                        **call_evidence,
                    )
                )
                # `retry N`: a rejected answer is shown back to the model with
                # the reason, up to N more times. Every attempt is a model call
                # for the limits, and every rejected one is kept in the trace.
                rejected: list[tuple[str, str]] = []
                while True:
                    answered = None
                    try:
                        result, response = call_model(
                            providers[model.provider or model.name],
                            arguments,
                            asked_schema,
                            instruction=model.instruction,
                            model=model.deployment,
                            temperature=model.parameters.temperature,
                            max_output_tokens=model.parameters.max_output_tokens,
                            top_p=model.parameters.top_p,
                            frequency_penalty=model.parameters.frequency_penalty,
                            presence_penalty=model.parameters.presence_penalty,
                            stop=model.parameters.stop,
                            rejected=tuple(rejected),
                        )
                        answered = response["content"]
                        if model.evidence is not None:
                            result, quotes = split(result)
                            try:
                                verify(model.evidence, arguments, result, quotes)
                            except EvidenceError as exc:
                                raise ThinkingAnswerError(
                                    f"{model.verb} answered without evidence: {exc}"
                                ) from exc
                            response = {**response, "evidence": quotes}
                        check_answer(model, arguments, result)
                        break
                    except (RejectedAnswer, ThinkingAnswerError) as exc:
                        if len(rejected) >= step.retries:
                            raise
                        if isinstance(exc, RejectedAnswer):
                            answered = exc.content
                        rejected.append((cast(str, answered), str(exc)))
                if rejected:
                    response = {
                        **response,
                        "rejected": [
                            {"content": content, "reason": reason}
                            for content, reason in rejected
                        ],
                    }
                events.append(
                    _event("model_return", model=model.name, result=result, **response)
                )
            else:
                tool = tools[step.call.tool_name]
                agent = agents[step.call.agent_name]
                input_schema = schema_for_tool_input(program, tool.input)
                output_schema = schema_for_record(program, tool.output)
                validate(arguments, input_schema)
                events.append(
                    _event(
                        "tool_call",
                        agent=agent.name,
                        tool=tool.name,
                        source=tool.source,
                        operation=tool.operation,
                        access=tool.access,
                        arguments=arguments,
                        expression_events=expression_events,
                        input_schema=input_schema,
                        output_schema=output_schema,
                    )
                )
                if tool.name in host_tools:
                    outcome, transport = host_tools[tool.name](tool, arguments), "host"
                else:
                    outcome, transport = _call_declared_tool(
                        tool,
                        arguments,
                        input_schema,
                        output_schema,
                        mcp_commands,
                        manifest,
                    )
                validate(outcome, output_schema)
                result = cast(dict[str, Any], outcome)
                events.append(
                    _event(
                        "tool_return",
                        tool=tool.name,
                        transport=transport,
                        result=result,
                    )
                )
            _control.scope = (*_scope, workflow.name, step.name)
            environment[step.binding_name] = result
            events.append(
                _event(
                    "step_complete",
                    step=step.name,
                    binding=step.binding_name,
                    result=result,
                )
            )
            if session is not None and not isinstance(step.call, WorkflowCall):
                session.complete(scope, events[step_offset:], action)
            _control.checkpoint()
        except (RuntimeError, ValueError) as exc:
            # `otherwise`: a recoverable failure hands the step to its
            # fallback call. Stops that are not failures of the work itself
            # (budgets, deadlines, cancellation, missing approval) never do.
            if step.fallback is None or isinstance(exc, ApprovalRequiredError):
                raise
            fallback_step = dataclass_replace(
                step, call=step.fallback, fallback=None, retries=0
            )
            if isinstance(exc, ExecutionError):
                # On resume, a step its fallback completed earlier is stored
                # under the fallback's arguments, so look it up that way.
                if exc.code != "checkpoint_mismatch":
                    raise
                del events[events_before:]
                queue.insert(0, fallback_step)
                continue
            del events[events_before:]
            if session is not None:
                session.recover((*_scope, workflow.name, step.name))
            recovered[step.name] = {
                "error": type(exc).__name__,
                "message": str(exc)[:500],
            }
            queue.insert(0, fallback_step)
    _control.checkpoint()
    validate(result, schema_for_record(program, workflow.output_type))
    events.append(_event("run_complete", workflow=workflow.name, result=result))

    if intelligence is not None:
        intelligence.record_events(events)

    if trace_path is None:
        return cast(dict[str, Any], result)
    path = Path(trace_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(item, sort_keys=True, allow_nan=False) for item in events)
        + "\n",
        encoding="utf-8",
    )
    return cast(dict[str, Any], result)


def replay(trace_path: str | Path) -> dict:
    return replay_trace(trace_path)
