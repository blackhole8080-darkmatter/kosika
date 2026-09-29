"""Bounded local module loading. Importing declarations grants no host bindings."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .ast import (
    AgentCall,
    FunctionCall,
    FunctionRefExpr,
    ModelCall,
    Program,
    ToolCall,
    WorkflowCall,
    expression_function_calls,
    static_function_references,
)
from .checker import CheckError, check
from .function_types import referenced_types
from .parser import parse

CATEGORIES = (
    "records",
    "tools",
    "agents",
    "swarms",
    "models",
    "functions",
    "workflows",
)


def _references(program: Program) -> set[str]:
    names: set[str] = set()
    for record in program.records:
        names.update(field.type_name.removesuffix("[]") for field in record.fields)
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
            call.function_name
            for call in static_function_references(function)
        )
        for binding in function.bindings:
            names.update(referenced_types(binding.type_name))
    for workflow in program.workflows:
        names.add(workflow.output_type)
        names.update(field.type_name for field in workflow.parameters)
        bound = {p.name for p in workflow.parameters}
        for step in workflow.steps:
            if step.binding_type:
                names.update(referenced_types(step.binding_type))
            call = step.call
            for _, argument in call.arguments:
                names.update(
                    nested.function_name
                    for nested in expression_function_calls(argument)
                    if isinstance(nested, FunctionRefExpr) or nested.function_name not in bound
                )
            if isinstance(call, WorkflowCall):
                names.add(call.workflow_name)
            elif isinstance(call, FunctionCall):
                if call.function_name not in bound:
                    names.add(call.function_name)
            elif isinstance(call, ModelCall):
                names.add(call.model_name)
            elif isinstance(call, (AgentCall, ToolCall)):
                names.add(call.agent_name)
                if isinstance(call, ToolCall):
                    names.add(call.tool_name)
            bound.add(step.binding_name)
    return names - {"Text", "Number", "Bool", "Null"}


def load_program(path: str | Path, *, root: str | Path | None = None) -> Program:
    entry = Path(path).absolute()
    boundary = (
        Path(root).resolve(strict=True) if root else entry.parent.resolve(strict=True)
    )
    loaded: dict[Path, Program] = {}
    active: set[Path] = set()
    merged = Program()
    owners: dict[str, Path] = {}

    def load(candidate: Path) -> Program:
        resolved = candidate.resolve(strict=True)
        if (
            candidate.is_symlink()
            or not resolved.is_relative_to(boundary)
            or resolved.suffix != ".kos"
        ):
            raise CheckError(
                f"Import outside the granted .kos module root: {candidate}"
            )
        if resolved in active:
            raise CheckError(f"Import cycle at {resolved}")
        if resolved in loaded:
            return loaded[resolved]
        if len(loaded) + len(active) >= 64 or len(active) >= 16:
            raise CheckError("Module graph exceeds file/depth limit")
        with resolved.open("rb") as stream:
            data = stream.read(262145)
        if len(data) > 262144:
            raise CheckError(f"Module exceeds 256 KiB: {resolved}")
        try:
            module = parse(data.decode("utf-8"))
        except ValueError as exc:
            raise CheckError(f"{resolved}: {exc}") from exc
        active.add(resolved)
        visible = set()
        direct_imports: list[str] = []
        for name in module.imports:
            relative = Path(name)
            if relative.is_absolute() or relative.drive:
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
            raise CheckError(f"{resolved}: exports must name unique local declarations")
        hidden = _references(module) - local - visible
        if hidden:
            raise CheckError(
                f"{resolved}: unavailable or private symbols {sorted(hidden)!r}"
            )
        for name in local:
            if name in owners and owners[name] != resolved:
                raise CheckError(f"Conflicting module declaration {name!r}")
            owners[name] = resolved
        module_key = resolved.relative_to(boundary).as_posix()
        merged.module_declarations[module_key] = tuple(sorted(local))
        merged.module_imports[module_key] = tuple(sorted(direct_imports))
        for category in CATEGORIES:
            getattr(merged, category).extend(getattr(module, category))
        merged.module_hashes[module_key] = hashlib.sha256(data).hexdigest()
        active.remove(resolved)
        loaded[resolved] = module
        return module

    load(entry)
    check(merged)
    return merged
