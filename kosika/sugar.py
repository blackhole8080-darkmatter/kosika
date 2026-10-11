"""Short forms that are rewritten to the full language before checking.

Two conveniences live here. Neither adds meaning: each is replaced by the
long form it stands for, so the checker and runtime never see it.

* **Unnamed arguments.** A call may leave every argument name out:
  `spend(order)`, `gcd(48, 18)`, or `use_model assistant("hello")`. The parser
  records the names as empty, and `name_sole_arguments` fills them in from the
  callee's parameters: by matching names when the arguments are same-named
  variables, otherwise by position (see `resolve_unnamed`). When the callee
  is not known here (a function held in a variable) the names are read off
  the expressions, as in `f(order)` meaning `f(order: order)`; otherwise the
  checker reports that a name is needed.
  Built-in methods resolve their one unnamed argument in the checker instead,
  because that needs the receiver's type.

* **A value where a call is expected.** After `otherwise`, or as a branch
  of a step's `match`, a record may be written directly:
  `hits = execute hands.search(q) otherwise {matches: []}`. `lower_values`
  replaces it with a call of a generated function that takes nothing and
  returns it. The function's type is the result type of the call the value
  stands in for, so the value is checked against it like any return.

* **A `main` function as the entry point.** A program with `fn main` and no
  `workflow main` gets the one-step workflow that calls it, provided the
  function takes and returns records as a workflow must.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
from typing import Any

from .ast import (
    AgentCall,
    Field,
    FieldExpr,
    FunctionCall,
    FunctionCallExpr,
    FunctionDecl,
    ModelCall,
    Program,
    Step,
    ToolCall,
    VariableExpr,
    WorkflowCall,
    WorkflowDecl,
)

#: The name the parser gives a call's single unnamed argument.
UNNAMED = ""
#: The name the parser gives a value written where a call is expected.
VALUE_CALL = "$value"


def inferred_name(expression: Any) -> str | None:
    """The name a bare variable or field path supplies for itself."""

    if isinstance(expression, VariableExpr):
        return expression.name
    if isinstance(expression, FieldExpr):
        return expression.field_name
    return None


def resolve_unnamed(
    arguments: tuple[tuple[str, Any], ...], parameters: list[str] | None
) -> tuple[tuple[str, Any], ...]:
    """Name a call's unnamed arguments, when that is possible.

    A call is either fully named or fully unnamed. Unnamed arguments are
    matched in this order:

    1. A single argument takes the callee's single parameter.
    2. If every argument is a variable or field whose own name is a parameter,
       and together they name every parameter, they are matched by name:
       `route(refund, label)` is `route(refund: refund, label: label)`.
    3. Otherwise they are matched by position: `gcd(48, 18)`.

    Rule 2 comes first so that passing same-named values in a different order
    can never silently swap them. When neither applies, the arguments are
    returned as they came and the checker reports what is wrong.
    """

    if not arguments or any(name != UNNAMED for name, _ in arguments):
        return arguments
    expressions = [expression for _, expression in arguments]
    if len(arguments) == 1 and parameters is not None and len(parameters) == 1:
        return ((parameters[0], expressions[0]),)
    inferred = [inferred_name(expression) for expression in expressions]
    names = [name for name in inferred if name is not None]
    by_name = tuple(zip(names, expressions)) if len(names) == len(expressions) else None
    if parameters is None:
        return by_name or arguments
    if by_name is not None and sorted(names) == sorted(parameters):
        return by_name
    if len(expressions) == len(parameters):
        return tuple(zip(parameters, expressions))
    # The count is wrong. Names read off the expressions let the checker say
    # which argument is unknown or missing.
    return by_name or arguments


def _uses_unnamed(value: Any) -> bool:
    if isinstance(value, (list, tuple)):
        return any(_uses_unnamed(item) for item in value)
    if is_dataclass(value) and not isinstance(value, type):
        arguments = getattr(value, "arguments", None)
        if isinstance(arguments, tuple) and any(
            isinstance(item, tuple) and len(item) == 2 and item[0] == UNNAMED
            for item in arguments
        ):
            return True
        return any(_uses_unnamed(getattr(value, item.name)) for item in fields(value))
    return False


def name_sole_arguments(program: Program) -> Program:
    """Fill in the name of every single unnamed argument whose callee is known."""

    if not _uses_unnamed([program.functions, program.workflows, program.tests]):
        return program

    functions = {
        function.name: [
            parameter.name
            for parameter in function.parameters
            if not parameter.name.startswith("$capture:")
        ]
        for function in program.functions
    }
    records = {
        record.name: [field.name for field in record.fields]
        for record in program.records
    }
    models = {model.name: records.get(model.input) for model in program.models}
    agents = {agent.name: models.get(agent.model or "") for agent in program.agents}
    tools = {
        tool.name: records.get(tool.input) if tool.input else []
        for tool in program.tools
    }
    workflows = {
        workflow.name: [parameter.name for parameter in workflow.parameters]
        for workflow in program.workflows
    }

    def rewrite(value: Any) -> Any:
        if isinstance(value, tuple):
            return tuple(rewrite(item) for item in value)
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if not is_dataclass(value) or isinstance(value, type):
            return value
        changes = {}
        for item in fields(value):
            current = getattr(value, item.name)
            updated = rewrite(current)
            if updated != current:
                changes[item.name] = updated
        node = replace(value, **changes) if changes else value
        parameters: list[str] | None
        if isinstance(node, (FunctionCallExpr, FunctionCall)):
            # A test runs a workflow by calling it like a function.
            parameters = functions.get(node.function_name) or workflows.get(
                node.function_name
            )
        elif isinstance(node, ModelCall):
            parameters = models.get(node.model_name)
        elif isinstance(node, AgentCall):
            parameters = agents.get(node.agent_name)
        elif isinstance(node, ToolCall):
            parameters = tools.get(node.tool_name)
        elif isinstance(node, WorkflowCall):
            parameters = workflows.get(node.workflow_name)
        else:
            return node
        arguments = resolve_unnamed(node.arguments, parameters)
        return (
            node if arguments is node.arguments else replace(node, arguments=arguments)
        )

    return replace(
        program,
        functions=rewrite(program.functions),
        workflows=rewrite(program.workflows),
        tests=rewrite(program.tests),
    )


def add_main_workflow(program: Program) -> Program:
    """Give a program with `fn main` and no `workflow main` its entry workflow."""

    if any(workflow.name == "main" for workflow in program.workflows):
        return program
    function = next((item for item in program.functions if item.name == "main"), None)
    if function is None:
        return program
    records = {record.name for record in program.records}
    if function.output_type not in records or any(
        parameter.type_name not in records for parameter in function.parameters
    ):
        return program
    step = Step(
        "result",
        FunctionCall(
            "main",
            tuple(
                (parameter.name, VariableExpr(parameter.name))
                for parameter in function.parameters
            ),
        ),
        "result",
        None,
        "result",
    )
    workflow = WorkflowDecl(
        "main", function.output_type, (step,), False, function.parameters
    )
    return replace(program, workflows=[*program.workflows, workflow])


def call_output(program: Program, call: object) -> str | None:
    """The declared result type of a step call, when it can be read off."""

    if isinstance(call, WorkflowCall):
        return next(
            (w.output_type for w in program.workflows if w.name == call.workflow_name),
            None,
        )
    if isinstance(call, FunctionCall):
        return next(
            (f.output_type for f in program.functions if f.name == call.function_name),
            None,
        )
    models = {model.name: model for model in program.models}
    if isinstance(call, ModelCall):
        model = models.get(call.model_name)
        return model.output if model else None
    if isinstance(call, AgentCall):
        agent = next((a for a in program.agents if a.name == call.agent_name), None)
        model = models.get(agent.model or "") if agent else None
        return model.output if model else None
    if isinstance(call, ToolCall):
        return next((t.output for t in program.tools if t.name == call.tool_name), None)
    return None


def _is_value(call: object) -> bool:
    return isinstance(call, FunctionCall) and call.function_name == VALUE_CALL


def _names_used(value: Any) -> set[str]:
    """Every variable an expression mentions."""

    if isinstance(value, VariableExpr):
        return {value.name}
    if isinstance(value, (list, tuple)):
        return set().union(*(_names_used(item) for item in value)) if value else set()
    if is_dataclass(value) and not isinstance(value, type):
        found: set[str] = set()
        for item in fields(value):
            found |= _names_used(getattr(value, item.name))
        return found
    return set()


def lower_values(program: Program) -> Program:
    """Replace each value written as a step call by a function returning it.

    The value may use the workflow's inputs and the steps before it, as in
    `otherwise {text: msg.text}`: each one it names becomes a parameter of
    the function, handed in where the value stood.
    """

    made: list[FunctionDecl] = []
    # What the value may name: filled in per workflow, step by step.
    seen: dict[str, str] = {}

    def stand_in(call: Any, name: str, type_name: str | None) -> Any:
        if not _is_value(call) or type_name is None:
            # With no call to take the type from, the value is left as it
            # is and the checker says what is missing.
            return call
        value = call.arguments[0][1]
        used = sorted(_names_used(value) & set(seen))
        made.append(
            FunctionDecl(
                name, tuple(Field(item, seen[item]) for item in used), type_name, value
            )
        )
        return FunctionCall(name, tuple((item, VariableExpr(item)) for item in used))

    workflows = []
    for workflow in program.workflows:
        steps = []
        seen.clear()
        seen.update({field.name: field.type_name for field in workflow.parameters})
        for step in workflow.steps:
            calls = [call for _, call in step.branches] or [step.call]
            known = next(
                (
                    found
                    for found in (
                        call_output(program, call)
                        for call in calls
                        if not _is_value(call)
                    )
                    if found is not None
                ),
                None,
            )
            prefix = f"{workflow.name}${step.name}$"
            changes: dict[str, Any] = {}
            if step.branches:
                branches = tuple(
                    (pattern, stand_in(call, f"{prefix}case{index + 1}", known))
                    for index, (pattern, call) in enumerate(step.branches)
                )
                if branches != step.branches:
                    changes["branches"] = branches
                    changes["call"] = branches[0][1]
            if step.fallback is not None:
                fallback = stand_in(step.fallback, f"{prefix}otherwise", known)
                if fallback is not step.fallback:
                    changes["fallback"] = fallback
            steps.append(replace(step, **changes) if changes else step)
            if known is not None:
                # From here on, later values may name this step.
                seen[step.name] = known
        workflows.append(replace(workflow, steps=tuple(steps)))
    if not made:
        return program
    return replace(program, workflows=workflows, functions=[*program.functions, *made])


def lower_sugar(program: Program) -> Program:
    return add_main_workflow(name_sole_arguments(lower_values(program)))
