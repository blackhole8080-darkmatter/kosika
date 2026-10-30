"""Versioned machine interface over the authoritative Kosika toolchain."""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import __version__
from .agent import ToolExecutor
from .ast import Program
from .devtools import inspect_trace as inspect_validated_trace
from .diagnostics import report_path
from .durable import ExecutionSession
from .execution import ExecutionLimits
from .manifest import MCPManifest
from .mindmap import build_mindmap
from .model import ModelProvider
from .model_steps import strict_json
from .modules import CATEGORIES, load_program
from .runtime import run
from .schema import schema_for_record

DEVELOPMENT_SCHEMA = "kosika.development/v1"
LANGUAGE_CONTRACT = f"kosika-language/{__version__}"
_OPERATIONS = frozenset({"inspect", "check", "run", "trace", "map"})
_COMMON_REQUEST_FIELDS = frozenset({"schema", "operation"})
_OPERATION_FIELDS = {
    "inspect": frozenset({"source", "root"}),
    "check": frozenset({"source", "root"}),
    "run": frozenset({"source", "root", "workflow", "inputs", "limits"}),
    "trace": frozenset({"trace"}),
    "map": frozenset({"source", "root"}),
}
_LIMIT_FIELDS = frozenset(
    {
        "max_function_calls",
        "max_steps",
        "max_model_calls",
        "max_tool_calls",
        "max_loop_iterations",
        "deadline_seconds",
    }
)


def _project_reference(source: str | Path, root: str | Path | None) -> dict[str, str]:
    entry = Path(source).resolve()
    boundary = Path(root).resolve() if root is not None else entry.parent
    try:
        relative_entry = entry.relative_to(boundary).as_posix()
    except ValueError:
        relative_entry = str(entry)
    return {"root": str(boundary), "entry": relative_entry}


def _response(
    operation: str,
    *,
    ok: bool,
    project: dict[str, str] | None = None,
    result: Any = None,
    diagnostics: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema": DEVELOPMENT_SCHEMA,
        "operation": operation,
        "ok": ok,
        "project": project,
        "result": result,
        "diagnostics": diagnostics,
        "error": error,
    }


def _error_code(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    return f"runtime/{code}" if isinstance(code, str) and code else "runtime/error"


def _diagnostic_failure(
    operation: str,
    source: str | Path,
    *,
    root: str | Path | None,
    source_overrides: Mapping[Path, str] | None,
) -> dict[str, Any]:
    diagnostics = report_path(source, root=root, source_overrides=source_overrides)
    return _response(
        operation,
        ok=False,
        project=_project_reference(source, root),
        diagnostics=diagnostics,
        error={
            "code": "project/diagnostics",
            "message": "Kosika project checking failed",
        },
    )


def _load_project(
    operation: str,
    source: str | Path,
    *,
    root: str | Path | None,
    source_overrides: Mapping[Path, str] | None,
) -> tuple[Program | None, dict[str, Any] | None]:
    try:
        return (
            load_program(source, root=root, source_overrides=source_overrides),
            None,
        )
    except (ValueError, OSError) as exc:
        failure = _diagnostic_failure(
            operation,
            source,
            root=root,
            source_overrides=source_overrides,
        )
        if not failure["diagnostics"]["diagnostics"]:
            failure["error"] = {"code": "project/error", "message": str(exc)}
        return None, failure


def _input_schema(program: Program, workflow_name: str) -> dict[str, Any]:
    workflow = next(item for item in program.workflows if item.name == workflow_name)
    return {
        "type": "object",
        "properties": {
            field.name: schema_for_record(program, field.type_name)
            for field in workflow.parameters
        },
        "required": [field.name for field in workflow.parameters],
        "additionalProperties": False,
    }


def _project_inventory(program: Program) -> dict[str, Any]:
    declaration_kinds = {
        "records": "record",
        "tools": "tool",
        "agents": "agent",
        "swarms": "swarm",
        "models": "model",
        "functions": "function",
        "workflows": "workflow",
        "enums": "enum",
        "shapes": "shape",
        "choices": "choice",
        "stores": "store",
    }
    return {
        "language_contract": LANGUAGE_CONTRACT,
        "modules": [
            {
                "path": path,
                "sha256": program.module_hashes[path],
                "imports": list(program.module_imports.get(path, ())),
                "declarations": list(program.module_declarations.get(path, ())),
            }
            for path in sorted(program.module_hashes)
        ],
        "declarations": {
            declaration_kinds[category]: sorted(
                item.name for item in getattr(program, category)
            )
            for category in CATEGORIES
            # Kinds added later are listed only when a program has some, so
            # the inventory of every other program is as it was.
            if category not in ("enums", "shapes", "choices", "stores")
            or getattr(program, category)
        },
        "workflows": [
            {
                "name": workflow.name,
                "input_schema": _input_schema(program, workflow.name),
                "output_schema": schema_for_record(program, workflow.output_type),
            }
            for workflow in sorted(program.workflows, key=lambda item: item.name)
        ],
    }


def inspect_project(
    source: str | Path,
    *,
    root: str | Path | None = None,
    source_overrides: Mapping[Path, str] | None = None,
) -> dict[str, Any]:
    """Return checked module and declaration metadata without executing code."""

    program, failure = _load_project(
        "inspect", source, root=root, source_overrides=source_overrides
    )
    if failure is not None:
        return failure
    assert program is not None
    return _response(
        "inspect",
        ok=True,
        project=_project_reference(source, root),
        result=_project_inventory(program),
    )


def check_project(
    source: str | Path,
    *,
    root: str | Path | None = None,
    source_overrides: Mapping[Path, str] | None = None,
) -> dict[str, Any]:
    """Return K04 diagnostics inside the development response envelope."""

    diagnostics = report_path(source, root=root, source_overrides=source_overrides)
    ok = bool(diagnostics["ok"])
    return _response(
        "check",
        ok=ok,
        project=_project_reference(source, root),
        result={"checked": ok},
        diagnostics=diagnostics,
        error=None
        if ok
        else {
            "code": "project/diagnostics",
            "message": "Kosika project checking failed",
        },
    )


def run_project(
    source: str | Path,
    *,
    workflow: str = "main",
    inputs: Mapping[str, Any] | None = None,
    trace_path: str | Path | None = None,
    root: str | Path | None = None,
    source_overrides: Mapping[Path, str] | None = None,
    model_bindings: Mapping[str, ModelProvider] | None = None,
    tool_bindings: Mapping[str, ToolExecutor] | None = None,
    mcp_commands: dict[str, Sequence[str]] | None = None,
    manifest: MCPManifest | None = None,
    session: ExecutionSession | None = None,
    limits: ExecutionLimits | None = None,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    """Run one checked workflow with explicit host bindings and bounded controls."""

    program, failure = _load_project(
        "run", source, root=root, source_overrides=source_overrides
    )
    if failure is not None:
        return failure
    assert program is not None
    events: list[dict[str, Any]] = []
    effective_limits = limits or ExecutionLimits()
    try:
        value = run(
            program,
            workflow,
            trace_path,
            mcp_commands=mcp_commands,
            manifest=manifest,
            inputs=dict(inputs or {}),
            model_bindings=model_bindings,
            tool_bindings=tool_bindings,
            session=session,
            limits=effective_limits,
            cancel=cancel,
            _events_out=events,
        )
    except (ValueError, RuntimeError, OSError) as exc:
        return _response(
            "run",
            ok=False,
            project=_project_reference(source, root),
            result={
                "workflow": workflow,
                "events": events,
                "trace_path": str(trace_path) if trace_path is not None else None,
            },
            error={"code": _error_code(exc), "message": str(exc)},
        )
    return _response(
        "run",
        ok=True,
        project=_project_reference(source, root),
        result={
            "workflow": workflow,
            "value": value,
            "events": events,
            "trace_path": str(trace_path) if trace_path is not None else None,
            "execution": events[-1].get("execution") if events else None,
            "limits": asdict(effective_limits),
        },
    )


def inspect_trace(path: str | Path) -> dict[str, Any]:
    """Validate a completed trace and return its bounded structural summary."""

    try:
        result = inspect_validated_trace(path)
    except (ValueError, OSError) as exc:
        return _response(
            "trace",
            ok=False,
            error={"code": "trace/invalid", "message": str(exc)},
        )
    return _response("trace", ok=True, result=result)


def map_project(
    source: str | Path,
    *,
    root: str | Path | None = None,
    source_overrides: Mapping[Path, str] | None = None,
) -> dict[str, Any]:
    """Return the deterministic checked-AST mindmap for one project."""

    program, failure = _load_project(
        "map", source, root=root, source_overrides=source_overrides
    )
    if failure is not None:
        return failure
    assert program is not None
    try:
        result = build_mindmap(program)
    except ValueError as exc:
        return _response(
            "map",
            ok=False,
            project=_project_reference(source, root),
            error={"code": "map/invalid", "message": str(exc)},
        )
    return _response(
        "map",
        ok=True,
        project=_project_reference(source, root),
        result=result,
    )


def _request_path(request: Mapping[str, Any], field: str) -> str:
    value = request.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Development request field {field!r} must be non-empty text")
    return value


def _request_root(request: Mapping[str, Any]) -> str | None:
    root = request.get("root")
    if root is not None and (not isinstance(root, str) or not root):
        raise ValueError("Development request field 'root' must be non-empty text")
    return root


def _request_limits(request: Mapping[str, Any]) -> ExecutionLimits:
    raw = request.get("limits", {})
    if not isinstance(raw, Mapping) or set(raw) - _LIMIT_FIELDS:
        raise ValueError("Development run limits contain unknown fields")
    return ExecutionLimits(**dict(raw))


def execute_development_request(
    request: object,
    *,
    model_bindings: Mapping[str, ModelProvider] | None = None,
    tool_bindings: Mapping[str, ToolExecutor] | None = None,
) -> dict[str, Any]:
    """Validate and execute one JSON-compatible development request."""

    operation = "invalid"
    try:
        if not isinstance(request, Mapping):
            raise ValueError("Development request must be a JSON object")
        if request.get("schema") != DEVELOPMENT_SCHEMA:
            raise ValueError(
                f"Development request schema must be {DEVELOPMENT_SCHEMA!r}"
            )
        raw_operation = request.get("operation")
        if not isinstance(raw_operation, str) or raw_operation not in _OPERATIONS:
            raise ValueError("Development request operation is not supported")
        operation = raw_operation
        extra = set(request) - _COMMON_REQUEST_FIELDS - _OPERATION_FIELDS[operation]
        if extra:
            raise ValueError(
                "Development request contains unknown fields: "
                + ", ".join(sorted(str(item) for item in extra))
            )

        if operation == "trace":
            return inspect_trace(_request_path(request, "trace"))

        source = _request_path(request, "source")
        root = _request_root(request)
        if operation == "inspect":
            return inspect_project(source, root=root)
        if operation == "check":
            return check_project(source, root=root)
        if operation == "map":
            return map_project(source, root=root)

        workflow = request.get("workflow", "main")
        if not isinstance(workflow, str) or not workflow:
            raise ValueError("Development run workflow must be non-empty text")
        inputs = request.get("inputs", {})
        if not isinstance(inputs, Mapping):
            raise ValueError("Development run inputs must be a JSON object")
        return run_project(
            source,
            root=root,
            workflow=workflow,
            inputs=inputs,
            limits=_request_limits(request),
            model_bindings=model_bindings,
            tool_bindings=tool_bindings,
        )
    except (TypeError, ValueError) as exc:
        return _response(
            operation,
            ok=False,
            error={"code": "request/invalid", "message": str(exc)},
        )


def execute_development_json(
    source: str,
    *,
    model_bindings: Mapping[str, ModelProvider] | None = None,
    tool_bindings: Mapping[str, ToolExecutor] | None = None,
) -> dict[str, Any]:
    """Parse strict JSON and execute one development request."""

    try:
        request = strict_json(source)
    except ValueError as exc:
        return _response(
            "invalid",
            ok=False,
            error={"code": "request/invalid", "message": str(exc)},
        )
    return execute_development_request(
        request,
        model_bindings=model_bindings,
        tool_bindings=tool_bindings,
    )


__all__ = [
    "DEVELOPMENT_SCHEMA",
    "LANGUAGE_CONTRACT",
    "check_project",
    "execute_development_json",
    "execute_development_request",
    "inspect_project",
    "inspect_trace",
    "map_project",
    "run_project",
]
