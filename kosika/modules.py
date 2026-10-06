"""Bounded local module loading. Importing declarations grants no host bindings."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .ast import (
    AgentCall,
    FunctionCall,
    FunctionCallExpr,
    FunctionRefExpr,
    ModelCall,
    Program,
    ThinkCall,
    ToolCall,
    WorkflowCall,
    expression_function_calls,
    static_function_references,
)
from .builtin_methods import did_you_mean
from .checker import CheckError, check
from .function_types import base_type, referenced_types
from .parser import ParseError, parse
from .sugar import lower_sugar
from .thinking import BUILT_IN_RECORDS, ThinkingError, lower_thinking

CATEGORIES = (
    "records",
    "tools",
    "agents",
    "swarms",
    "models",
    "functions",
    "workflows",
)


@dataclass
class ModuleLoadContext:
    """Source ownership collected by the authoritative module loader.

    Diagnostics pass one context into :func:`load_program` and use it only when
    loading fails. Runtime callers do not pay for a second module traversal.
    """

    boundary: Path | None = None
    sources: dict[Path, str] = field(default_factory=dict)
    declarations: dict[str, set[Path]] = field(default_factory=dict)
    steps: dict[str, set[Path]] = field(default_factory=dict)
    error_path: Path | None = None

    def owner_path(self, name: str, kind: str | None) -> Path | None:
        candidates = (
            self.steps.get(name, set())
            if kind == "step"
            else self.declarations.get(name, set())
        )
        return next(iter(candidates)) if len(candidates) == 1 else None


def _references(program: Program) -> set[str]:
    names: set[str] = set()
    for record in program.records:
        names.update(base_type(field.type_name) for field in record.fields)
    for tool in program.tools:
        names.add(tool.output)
        if tool.input:
            names.add(tool.input)
    for model in program.models:
        names.update((model.input, model.output))
    for agent in program.agents:
        names.update(agent.can)
        if agent.model:
            names.add(agent.model)
    for swarm in program.swarms:
        names.update(swarm.agents)
        names.add(swarm.workflow)
    for function in program.functions:
        names.update(referenced_types(function.output_type))
        names.update(
            type_name
            for parameter in function.parameters
            for type_name in referenced_types(parameter.type_name)
        )
        names.update(
            call.function_name for call in static_function_references(function)
        )
        for binding in function.bindings:
            # An inferred binding has no annotation in the source, so it
            # contributes no type reference here.
            if binding.type_name:
                names.update(referenced_types(binding.type_name))
    for workflow in program.workflows:
        names.add(workflow.output_type)
        names.update(field.type_name for field in workflow.parameters)
        bound = {p.name for p in workflow.parameters}
        for step in workflow.steps:
            if step.binding_type:
                names.update(referenced_types(step.binding_type))
            for call in (step.call, step.fallback):
                if call is None:
                    continue
                if isinstance(call, ThinkCall):
                    # Not yet lowered: its expressions are its subject and extra.
                    call_arguments: tuple = tuple(
                        ("", item)
                        for item in (call.subject, call.extra)
                        if item is not None
                    )
                else:
                    call_arguments = call.arguments
                for _, argument in call_arguments:
                    names.update(
                        nested.function_name
                        for nested in expression_function_calls(argument)
                        if isinstance(nested, FunctionRefExpr)
                        or (
                            isinstance(nested, FunctionCallExpr)
                            and nested.function_name not in bound
                        )
                    )
                if isinstance(call, WorkflowCall):
                    names.add(call.workflow_name)
                elif isinstance(call, FunctionCall):
                    if call.function_name not in bound:
                        names.add(call.function_name)
                elif isinstance(call, ModelCall):
                    names.add(call.model_name)
                elif isinstance(call, ThinkCall):
                    if call.model is not None:
                        names.add(call.model)
                    if call.record is not None:
                        names.add(call.record)
                elif isinstance(call, (AgentCall, ToolCall)):
                    names.add(call.agent_name)
                    if isinstance(call, ToolCall):
                        names.add(call.tool_name)
                bound.add(step.binding_name)
    return names - {"Text", "Number", "Bool", "Null"}


def load_program(
    path: str | Path,
    *,
    root: str | Path | None = None,
    source_overrides: Mapping[Path, str] | None = None,
    _diagnostic_context: ModuleLoadContext | None = None,
) -> Program:
    entry = Path(path).absolute()
    boundary = (
        Path(root).resolve(strict=True) if root else entry.parent.resolve(strict=True)
    )
    overrides = {
        Path(candidate).resolve(strict=True): source
        for candidate, source in (source_overrides or {}).items()
    }
    if any(not isinstance(source, str) for source in overrides.values()):
        raise TypeError("Module source overrides must be text")
    loaded: dict[Path, Program] = {}
    active: set[Path] = set()
    merged = Program()
    owners: dict[str, Path] = {}
    if _diagnostic_context is not None:
        _diagnostic_context.boundary = boundary
        _diagnostic_context.sources.clear()
        _diagnostic_context.declarations.clear()
        _diagnostic_context.steps.clear()
        _diagnostic_context.error_path = None

    def mark_error(candidate: Path) -> None:
        if _diagnostic_context is not None and _diagnostic_context.error_path is None:
            _diagnostic_context.error_path = candidate

    def load(candidate: Path) -> Program:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            if (
                _diagnostic_context is not None
                and _diagnostic_context.error_path is None
            ):
                _diagnostic_context.error_path = candidate.absolute()
            raise
        if (
            candidate.is_symlink()
            or not resolved.is_relative_to(boundary)
            or resolved.suffix != ".kos"
        ):
            mark_error(resolved)
            raise CheckError(
                f"Import outside the granted .kos module root: {candidate}"
            )
        if resolved in active:
            mark_error(resolved)
            raise CheckError(f"Import cycle at {resolved}")
        if resolved in loaded:
            return loaded[resolved]
        if len(loaded) + len(active) >= 64 or len(active) >= 16:
            mark_error(resolved)
            raise CheckError("Module graph exceeds file/depth limit")
        if resolved in overrides:
            source = overrides[resolved]
            data = source.encode("utf-8")
        else:
            with resolved.open("rb") as stream:
                data = stream.read(262145)
            if len(data) > 262144:
                mark_error(resolved)
                raise CheckError(f"Module exceeds 256 KiB: {resolved}")
            try:
                source = data.decode("utf-8")
            except UnicodeDecodeError:
                if (
                    _diagnostic_context is not None
                    and _diagnostic_context.error_path is None
                ):
                    _diagnostic_context.error_path = resolved
                raise
        if len(data) > 262144:
            mark_error(resolved)
            raise CheckError(f"Module exceeds 256 KiB: {resolved}")
        if _diagnostic_context is not None:
            _diagnostic_context.sources[resolved] = source
        try:
            module = parse(source, lower_thinking_steps=False)
        except ValueError as exc:
            if (
                _diagnostic_context is not None
                and _diagnostic_context.error_path is None
            ):
                _diagnostic_context.error_path = resolved
            error_type = ParseError if isinstance(exc, ParseError) else ValueError
            raise error_type(f"{resolved}: {exc}") from None
        active.add(resolved)
        visible = set()
        direct_imports: list[str] = []
        for name in module.imports:
            relative = Path(name)
            if relative.is_absolute() or relative.drive:
                mark_error(resolved)
                raise CheckError("Import paths must be relative")
            dependency_path = resolved.parent / relative
            dependency = load(dependency_path)
            visible.update(dependency.exports)
            direct_imports.append(
                dependency_path.resolve(strict=True).relative_to(boundary).as_posix()
            )
        local = {
            item.name for category in CATEGORIES for item in getattr(module, category)
        }
        if (
            len(module.exports) != len(set(module.exports))
            or set(module.exports) - local
        ):
            mark_error(resolved)
            raise CheckError(f"{resolved}: exports must name unique local declarations")
        # Built-in records are added when thinking steps are lowered.
        hidden = _references(module) - local - visible - set(BUILT_IN_RECORDS)
        if hidden:
            mark_error(resolved)
            hint = did_you_mean(
                sorted(hidden)[0], local | visible | {"Text", "Number", "Bool"}
            )
            raise CheckError(
                f"{resolved}: unknown names, not declared here and not exported "
                f"by an import (unavailable or private symbols {sorted(hidden)!r})."
                f"{hint}"
            )
        for name in local:
            if name in owners and owners[name] != resolved:
                mark_error(resolved)
                raise CheckError(f"Conflicting module declaration {name!r}")
            owners[name] = resolved
            if _diagnostic_context is not None:
                _diagnostic_context.declarations.setdefault(name, set()).add(resolved)
        if _diagnostic_context is not None:
            for workflow in module.workflows:
                for step in workflow.steps:
                    _diagnostic_context.steps.setdefault(step.name, set()).add(resolved)
        module_key = resolved.relative_to(boundary).as_posix()
        merged.module_declarations[module_key] = tuple(sorted(local))
        merged.module_imports[module_key] = tuple(sorted(direct_imports))
        for category in CATEGORIES:
            getattr(merged, category).extend(getattr(module, category))
        merged.tests.extend(module.tests)
        merged.module_hashes[module_key] = hashlib.sha256(data).hexdigest()
        active.remove(resolved)
        loaded[resolved] = module
        return module

    load(entry)
    # Thinking steps are lowered once over the merged program, so a general
    # model declared in one module serves steps written in another and the
    # built-in records are added a single time.
    try:
        merged = lower_sugar(lower_thinking(merged))
    except ThinkingError as exc:
        raise CheckError(str(exc)) from exc
    check(merged)
    return merged
