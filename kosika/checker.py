from __future__ import annotations

from .ast import (
    AgentCall,
    ArrayExpr,
    FieldExpr,
    IndexExpr,
    LiteralExpr,
    ModelCall,
    ModelDecl,
    Program,
    RecordExpr,
    ToolDecl,
    VariableExpr,
    WorkflowCall,
)


class CheckError(ValueError):
    pass


_ALLOWED_ACCESS = {"observe", "analyze", "prepare", "execute"}
_PRIMITIVE_TYPES = {"Text", "Number", "Bool", "Null"}


def _record_fields(program: Program, record_name: str) -> dict[str, str]:
    for record in program.records:
        if record.name == record_name:
            return {field.name: field.type_name for field in record.fields}
    raise CheckError(f"Unknown record type {record_name!r}")


def _validate_type_name(program: Program, type_name: str) -> None:
    if type_name in _PRIMITIVE_TYPES:
        return
    if type_name.endswith("[]"):
        _validate_type_name(program, type_name[:-2])
        return
    if type_name not in {record.name for record in program.records}:
        raise CheckError(f"Unknown field type {type_name!r}")


def _literal_type(value: object) -> str:
    if value is None:
        return "Null"
    if isinstance(value, bool):
        return "Bool"
    if isinstance(value, str):
        return "Text"
    if isinstance(value, (int, float)):
        return "Number"
    raise CheckError(f"Unsupported literal value {value!r}")


def _expression_type(
    expression: object,
    bindings: dict[str, str],
    program: Program,
    *,
    step_name: str,
    expected_type: str | None = None,
) -> str:
    if isinstance(expression, LiteralExpr):
        return _literal_type(expression.value)
    if isinstance(expression, VariableExpr):
        value_type = bindings.get(expression.name)
        if value_type is None:
            raise CheckError(
                f"Step {step_name!r} references unknown variable {expression.name!r}"
            )
        return value_type
    if isinstance(expression, FieldExpr):
        base_type = _expression_type(
            expression.base, bindings, program, step_name=step_name
        )
        if base_type.endswith("[]"):
            raise CheckError(
                f"Step {step_name!r} cannot access field {expression.field_name!r} "
                f"on array type {base_type!r}"
            )
        fields = _record_fields(program, base_type)
        field_type = fields.get(expression.field_name)
        if field_type is None:
            raise CheckError(
                f"Step {step_name!r} references unknown field "
                f"{expression.field_name!r} on {base_type!r}"
            )
        return field_type
    if isinstance(expression, IndexExpr):
        base_type = _expression_type(
            expression.base, bindings, program, step_name=step_name
        )
        if not base_type.endswith("[]"):
            raise CheckError(
                f"Step {step_name!r} cannot index non-array type {base_type!r}"
            )
        index_type = _expression_type(
            expression.index, bindings, program, step_name=step_name
        )
        if index_type != "Number":
            raise CheckError(
                f"Step {step_name!r} array index expects 'Number', got {index_type!r}"
            )
        return base_type[:-2]
    if isinstance(expression, ArrayExpr):
        if expected_type is None or not expected_type.endswith("[]"):
            raise CheckError(
                f"Step {step_name!r} array literal requires an array-typed field"
            )
        element_type = expected_type[:-2]
        for item in expression.items:
            actual_type = _expression_type(
                item,
                bindings,
                program,
                step_name=step_name,
                expected_type=element_type,
            )
            if actual_type != element_type:
                raise CheckError(
                    f"Step {step_name!r} array item expects {element_type!r}, "
                    f"got {actual_type!r}"
                )
        return expected_type
    if isinstance(expression, RecordExpr):
        if expected_type is None or expected_type.endswith("[]"):
            raise CheckError(
                f"Step {step_name!r} record literal requires a record-typed field"
            )
        fields = _record_fields(program, expected_type)
        names = [name for name, _ in expression.fields]
        if len(names) != len(set(names)):
            raise CheckError(f"Step {step_name!r} has duplicate record literal fields")
        unknown = set(names) - set(fields)
        if unknown:
            raise CheckError(
                f"Step {step_name!r} has unknown record fields {sorted(unknown)!r}"
            )
        missing = set(fields) - set(names)
        if missing:
            raise CheckError(
                f"Step {step_name!r} is missing record fields {sorted(missing)!r}"
            )
        for name, value in expression.fields:
            actual_type = _expression_type(
                value,
                bindings,
                program,
                step_name=step_name,
                expected_type=fields[name],
            )
            if actual_type != fields[name]:
                raise CheckError(
                    f"Step {step_name!r} record field {name!r} expects "
                    f"{fields[name]!r}, got {actual_type!r}"
                )
        return expected_type
    raise CheckError(f"Step {step_name!r} contains an unsupported expression")


def _check_arguments(
    program: Program,
    tool_input: str | None,
    arguments: tuple[tuple[str, object], ...],
    bindings: dict[str, str],
    *,
    step_name: str,
    expected_fields: dict[str, str] | None = None,
) -> None:
    argument_names = [name for name, _ in arguments]
    if len(argument_names) != len(set(argument_names)):
        raise CheckError(f"Step {step_name!r} has duplicate tool arguments")
    expected = (
        expected_fields
        if expected_fields is not None
        else ({} if tool_input is None else _record_fields(program, tool_input))
    )
    provided = set(argument_names)
    unknown = provided - set(expected)
    if unknown:
        raise CheckError(
            f"Step {step_name!r} has unknown tool arguments {sorted(unknown)!r}"
        )
    missing = set(expected) - provided
    if missing:
        raise CheckError(
            f"Step {step_name!r} is missing tool arguments {sorted(missing)!r}"
        )
    for name, expression in arguments:
        actual_type = _expression_type(
            expression,
            bindings,
            program,
            step_name=step_name,
            expected_type=expected[name],
        )
        expected_type = expected[name]
        if actual_type != expected_type:
            raise CheckError(
                f"Step {step_name!r} argument {name!r} expects {expected_type!r}, "
                f"got {actual_type!r}"
            )


def check(program: Program) -> None:
    if program.imports:
        raise CheckError("Unresolved imports: use load_program(path)")
    records = {record.name for record in program.records}
    tools = {tool.name: tool for tool in program.tools}
    agents = {agent.name: agent for agent in program.agents}
    workflows = {workflow.name: workflow for workflow in program.workflows}
    models = {model.name: model for model in program.models}

    if len(models) != len(program.models):
        raise CheckError("Duplicate model declaration")
    for model in program.models:
        if model.input not in records or model.output not in records:
            raise CheckError(
                f"Model {model.name!r} requires known input and output records"
            )

    if len(records) != len(program.records):
        raise CheckError("Duplicate record declaration")
    if len(tools) != len(program.tools):
        raise CheckError("Duplicate tool declaration")
    if len(agents) != len(program.agents):
        raise CheckError("Duplicate agent declaration")
    if len(workflows) != len(program.workflows):
        raise CheckError("Duplicate workflow declaration")

    visited: set[str] = set()
    active: set[str] = set()

    def visit(name: str) -> None:
        if name in active:
            raise CheckError(f"Recursive workflow call involving {name!r}")
        if name in visited:
            return
        if name not in workflows:
            raise CheckError(f"Unknown workflow {name!r}")
        if len(active) >= 32:
            raise CheckError("Workflow call depth exceeds 32")
        active.add(name)
        for item in workflows[name].steps:
            if isinstance(item.call, WorkflowCall):
                visit(item.call.workflow_name)
        active.remove(name)
        visited.add(name)

    for name in workflows:
        visit(name)

    for record in program.records:
        field_names = [field.name for field in record.fields]
        if len(field_names) != len(set(field_names)):
            raise CheckError(f"Record {record.name!r} has duplicate fields")
        for field in record.fields:
            _validate_type_name(program, field.type_name)

    for tool in program.tools:
        if tool.access not in _ALLOWED_ACCESS:
            raise CheckError(
                f"Tool {tool.name!r} has invalid access level {tool.access!r}"
            )
        if tool.output not in records:
            raise CheckError(
                f"Tool {tool.name!r} returns unknown record {tool.output!r}"
            )
        if tool.input is not None and tool.input not in records:
            raise CheckError(
                f"Tool {tool.name!r} accepts unknown record {tool.input!r}"
            )

    for agent in program.agents:
        if agent.model is not None and agent.model not in models:
            raise CheckError(f"Agent {agent.name!r} uses unknown model {agent.model!r}")
        if type(agent.max_steps) is not int or not 1 <= agent.max_steps <= 64:
            raise CheckError(f"Agent {agent.name!r} max_steps must be between 1 and 64")
        for tool_name in agent.can:
            if tool_name not in tools:
                raise CheckError(
                    f"Agent {agent.name!r} can use unknown tool {tool_name!r}"
                )
        overlap = set(agent.can) & set(agent.cannot)
        if overlap:
            raise CheckError(
                f"Agent {agent.name!r} both allows and denies {sorted(overlap)!r}"
            )

    for workflow in program.workflows:
        if workflow.output_type not in records:
            raise CheckError(
                f"Workflow {workflow.name!r} returns unknown record {workflow.output_type!r}"
            )
        if not workflow.steps:
            raise CheckError(f"Workflow {workflow.name!r} has no steps")
        step_names = [step.name for step in workflow.steps]
        if len(set(step_names)) != len(step_names):
            raise CheckError(f"Workflow {workflow.name!r} has duplicate step names")
        final_call = workflow.steps[-1].call
        final_tool: ModelDecl | ToolDecl | None
        if isinstance(final_call, WorkflowCall):
            child = workflows[final_call.workflow_name]
            final_tool = ModelDecl(child.name, "", child.output_type)
        elif isinstance(final_call, AgentCall):
            final_agent = agents.get(final_call.agent_name)
            final_tool = models.get(final_agent.model or "") if final_agent else None
        elif isinstance(final_call, ModelCall):
            final_tool = models.get(final_call.model_name)
        else:
            final_tool = tools.get(final_call.tool_name)
        if final_tool is not None and final_tool.output != workflow.output_type:
            raise CheckError(
                f"Workflow {workflow.name!r} returns {workflow.output_type!r}, "
                f"but final tool returns {final_tool.output!r}"
            )

        bindings: dict[str, str] = {}
        for parameter in workflow.parameters:
            if parameter.name in bindings:
                raise CheckError(f"Duplicate workflow parameter {parameter.name!r}")
            if parameter.type_name not in records:
                raise CheckError(
                    f"Workflow parameter {parameter.name!r} requires a known record"
                )
            bindings[parameter.name] = parameter.type_name
        call_input: str | None
        for step in workflow.steps:
            expected_fields = None
            if isinstance(step.call, WorkflowCall):
                child = workflows[step.call.workflow_name]
                call_input, call_output = None, child.output_type
                expected_fields = {
                    field.name: field.type_name for field in child.parameters
                }
                if child.approval_required and not workflow.approval_required:
                    raise CheckError(
                        f"Workflow {workflow.name!r} must require approval for {child.name!r}"
                    )
            elif isinstance(step.call, AgentCall):
                asked_agent = agents.get(step.call.agent_name)
                if asked_agent is None or asked_agent.model is None:
                    raise CheckError(
                        f"Step {step.name!r} requires a known agent with a model"
                    )
                asked_model = models[asked_agent.model]
                call_input, call_output = asked_model.input, asked_model.output
            elif isinstance(step.call, ModelCall):
                resolved_model = models.get(step.call.model_name)
                if resolved_model is None:
                    raise CheckError(
                        f"Step {step.name!r} uses unknown model {step.call.model_name!r}"
                    )
                call_input, call_output = resolved_model.input, resolved_model.output
            else:
                workflow_agent = agents.get(step.call.agent_name)
                if workflow_agent is None:
                    raise CheckError(
                        f"Step {step.name!r} uses unknown agent {step.call.agent_name!r}"
                    )
                workflow_tool = tools.get(step.call.tool_name)
                if workflow_tool is None:
                    raise CheckError(
                        f"Step {step.name!r} uses unknown tool {step.call.tool_name!r}"
                    )
                if step.call.tool_name not in workflow_agent.can:
                    raise CheckError(
                        f"Capability violation: agent {workflow_agent.name!r} cannot use tool {workflow_tool.name!r}"
                    )
                if workflow_tool.access == "execute" and not workflow.approval_required:
                    raise CheckError(
                        f"Workflow {workflow.name!r} needs approval before execute tool {workflow_tool.name!r}"
                    )
                call_input, call_output = workflow_tool.input, workflow_tool.output
            _check_arguments(
                program,
                call_input,
                step.call.arguments,
                bindings,
                step_name=step.name,
                expected_fields=expected_fields,
            )
            if step.binding_type is not None:
                if step.binding_type not in records:
                    raise CheckError(
                        f"Step {step.name!r} binds to unknown record type "
                        f"{step.binding_type!r}"
                    )
                if step.binding_type != call_output:
                    raise CheckError(
                        f"Step {step.name!r} binding {step.binding_name!r} is "
                        f"{step.binding_type!r}, but call returns {call_output!r}"
                    )
            if step.return_name != step.binding_name:
                raise CheckError(
                    f"Step {step.name!r} must return its binding "
                    f"{step.binding_name!r}, got {step.return_name!r}"
                )
            if step.binding_name in {
                parameter.name for parameter in workflow.parameters
            }:
                raise CheckError(
                    f"Step {step.name!r} cannot shadow workflow input {step.binding_name!r}"
                )
            bindings[step.binding_name] = call_output
