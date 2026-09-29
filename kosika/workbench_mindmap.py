"""Deterministic, non-executing explanations of checked Kosika syntax."""

from __future__ import annotations

from typing import Any

from .ast import (
    AgentCall,
    BinaryExpr,
    ConditionalExpr,
    FieldExpr,
    FunctionCall,
    FunctionCallExpr,
    ModelCall,
    Program,
    ToolCall,
    UnaryExpr,
    VariableExpr,
    WorkflowCall,
)
from .lexer import lex


def locations(program: Program, files: dict[str, str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for filename in sorted(program.module_hashes):
        tokens = lex(files[filename])
        depth = 0
        workflow = ""
        for index, token in enumerate(tokens[:-1]):
            key = None
            if (
                token.kind == "IDENT"
                and depth == 0
                and token.value
                in {"record", "model", "agent", "tool", "fn", "workflow"}
            ):
                name = tokens[index + 1].value
                key = f"{token.value}:{name}"
                workflow = name if token.value == "workflow" else ""
            elif (
                token.kind == "IDENT"
                and depth == 1
                and workflow
                and token.value == "step"
            ):
                key = f"step:{workflow}:{tokens[index + 1].value}"
            if key:
                result[key] = {
                    "file": filename,
                    "line": token.line,
                    "column": token.column,
                }
            if token.kind == "SYMBOL":
                if token.value == "{":
                    depth += 1
                elif token.value == "}":
                    depth -= 1
    return result


def reference(value: Any) -> str:
    if isinstance(value, VariableExpr):
        return value.name
    if isinstance(value, FieldExpr):
        return f"{reference(value.base)}.{value.field_name}"
    if isinstance(value, FunctionCallExpr):
        return f"{value.function_name}(...)"
    if isinstance(value, UnaryExpr):
        return f"{value.operator} {reference(value.operand)}"
    if isinstance(value, BinaryExpr):
        return f"{reference(value.left)} {value.operator} {reference(value.right)}"
    if isinstance(value, ConditionalExpr):
        return f"if {reference(value.condition)} {{...}} else {{...}}"
    return "an expression or literal"


def build_mindmap(
    program: Program, workflow_name: str, files: dict[str, str]
) -> dict[str, Any]:
    workflows = {item.name: item for item in program.workflows}
    if workflow_name not in workflows:
        raise ValueError(f"Unknown workflow {workflow_name!r}.")
    places = locations(program, files)
    count = 0

    def node(
        kind: str,
        title: str,
        explanation: str,
        *,
        key: str = "",
        children: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        nonlocal count
        count += 1
        if count > 400:
            raise ValueError(
                "Mindmap exceeds 400 nodes. Split the program into smaller entry modules."
            )
        return {
            "id": f"node-{count}",
            "kind": kind,
            "title": title,
            "explanation": explanation,
            "location": places.get(key),
            "children": children or [],
        }

    selected = workflows[workflow_name]
    workflow_nodes = []
    for workflow in program.workflows:
        steps = []
        for index, step in enumerate(workflow.steps, 1):
            call = step.call
            if isinstance(call, ModelCall):
                action, target, kind = "Ask model", call.model_name, "model"
            elif isinstance(call, AgentCall):
                action, target, kind = "Run bounded agent", call.agent_name, "agent"
            elif isinstance(call, WorkflowCall):
                action, target, kind = "Call workflow", call.workflow_name, "workflow"
            elif isinstance(call, FunctionCall):
                action, target, kind = (
                    "Compute with function",
                    call.function_name,
                    "function",
                )
            elif isinstance(call, ToolCall):
                action, target, kind = "Use tool", call.tool_name, "tool"
            else:
                raise ValueError("Unsupported call in mindmap.")
            arguments = (
                "; ".join(
                    f"{name} receives {reference(value)}"
                    for name, value in call.arguments
                )
                or "No arguments"
            )
            steps.append(
                node(
                    kind,
                    f"{index}. {step.name}",
                    f"{action} {target}. Save its output as {step.binding_name}; return {step.return_name} from this step.",
                    key=f"step:{workflow.name}:{step.name}",
                    children=[
                        node("data", "Data passed in", arguments),
                        node(
                            kind,
                            target,
                            f"Declared {kind}; click to inspect its definition.",
                            key=f"{kind}:{target}",
                        ),
                    ],
                )
            )
        workflow_nodes.append(
            node(
                "workflow",
                workflow.name + (" · entry" if workflow.name == workflow_name else ""),
                f"{len(workflow.steps)} ordered steps → {workflow.output_type}. "
                + (
                    "Human approval is declared."
                    if workflow.approval_required
                    else "No workflow-level approval requirement is declared."
                ),
                key=f"workflow:{workflow.name}",
                children=steps,
            )
        )
    records = [
        node(
            "record",
            item.name,
            "; ".join(f"{field.name}: {field.type_name}" for field in item.fields)
            or "Empty record",
            key=f"record:{item.name}",
        )
        for item in program.records
    ]
    functions = [
        node(
            "function",
            item.name,
            "Pure computation. "
            + ", ".join(f"{field.name}: {field.type_name}" for field in item.parameters)
            + f" -> {item.output_type}."
            + " Locals: "
            + (
                ", ".join(
                    f"{binding.name}: {binding.type_name}" for binding in item.bindings
                )
                or "none"
            ),
            key=f"fn:{item.name}",
        )
        for item in program.functions
    ]
    intelligence = [
        node(
            "model",
            item.name,
            f"Accepts {item.input}; must return {item.output}. The host chooses the provider; this declaration grants no network access.",
            key=f"model:{item.name}",
        )
        for item in program.models
    ]
    intelligence += [
        node(
            "agent",
            item.name,
            f"Model: {item.model or 'none'}. Step limit: {item.max_steps}. Declared tools: {', '.join(item.can) or 'none'}. Forbidden: {', '.join(item.cannot) or 'none listed'}. Host grants are also required.",
            key=f"agent:{item.name}",
        )
        for item in program.agents
    ]
    tools = [
        node(
            "tool",
            item.name,
            f"{item.source} / {item.operation}. Access: {item.access}. Input: {item.input or 'no fields'} → {item.output}. Declaration only; execution still requires a host grant.",
            key=f"tool:{item.name}",
        )
        for item in program.tools
    ]
    branches = [
        node(
            "data",
            "Start with inputs",
            "; ".join(
                f"{field.name}: {field.type_name}" for field in selected.parameters
            )
            or "This workflow needs no inputs.",
        ),
        node(
            "workflow",
            "Follow the workflows",
            "Steps run in their declared order. Called workflows are shown as references, not duplicated executions.",
            children=workflow_nodes,
        ),
        node(
            "model",
            "Models and agents",
            "Typed AI interfaces and bounded agents."
            if intelligence
            else "This program declares no models or agents.",
            children=intelligence,
        ),
        node(
            "tool",
            "Tools and permissions",
            "Declared operations; not proof of permission or availability."
            if tools
            else "This program declares no tools.",
            children=tools,
        ),
        node(
            "record",
            "Data contracts",
            "Records define the shapes that cross program boundaries.",
            children=records,
        ),
        node(
            "function",
            "Pure functions",
            "Typed computation with no model, tool, or host authority."
            if functions
            else "This program declares no pure functions.",
            children=functions,
        ),
    ]
    return {
        "version": 1,
        "workflow": workflow_name,
        "files": sorted(program.module_hashes),
        "node_count": count + 1,
        "root": node(
            "program",
            workflow_name,
            f"Receives {len(selected.parameters)} input record(s) and returns {selected.output_type}.",
            key=f"workflow:{workflow_name}",
            children=branches,
        ),
        "notice": "Derived from checked source and imported modules. This is a static explanation, not an execution trace or an AI-generated claim about intent.",
    }
