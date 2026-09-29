from __future__ import annotations

import math

from .ast import (
    AgentCall,
    ArrayExpr,
    BinaryExpr,
    BreakExpr,
    ConditionalExpr,
    ContinueExpr,
    Field,
    FieldExpr,
    ForExpr,
    FunctionCall,
    FunctionCallExpr,
    FunctionExpr,
    FunctionRefExpr,
    IndexExpr,
    LiteralExpr,
    MatchExpr,
    MethodCallExpr,
    ModelCall,
    ModelVarExpr,
    Program,
    RecordExpr,
    RepeatExpr,
    ToolCall,
    ToolVarExpr,
    TryExpr,
    UnaryExpr,
    VariableExpr,
    WorkflowCall,
    static_function_references,
)
from .function_types import function_type, public_type, signature


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
    function_signature = signature(type_name)
    if function_signature:
        parameters, output = function_signature
        if len({p.name for p in parameters}) != len(parameters):
            raise CheckError("Duplicate parameter in function type")
        for parameter in parameters:
            _validate_type_name(program, parameter.type_name)
        _validate_type_name(program, output)
        return
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
    if isinstance(expression, MatchExpr):
        subject_type = _expression_type(expression.subject, bindings, program, step_name=step_name)
        if subject_type not in _PRIMITIVE_TYPES:
            raise CheckError("Match subjects must be scalar values")
        seen: list[object] = []
        exhaustive = False
        result_type = expected_type
        for arm in expression.arms:
            if exhaustive:
                raise CheckError("Unreachable match arm")
            if arm.pattern is None:
                exhaustive = True
            else:
                pattern = arm.pattern.value
                if _literal_type(pattern) != subject_type:
                    raise CheckError("Match pattern type differs from subject")
                if pattern in seen:
                    raise CheckError("Duplicate match pattern")
                seen.append(pattern)
                exhaustive = subject_type == "Null" or (subject_type == "Bool" and len(seen) == 2)
            arm_type = _expression_type(arm.value, bindings, program, step_name=step_name,
                                        expected_type=result_type)
            if result_type is not None and arm_type != result_type:
                raise CheckError("Match branches must have the same result type")
            result_type = arm_type
        if not exhaustive or result_type is None:
            raise CheckError("Non-exhaustive match: add a final case _")
        return result_type
    if isinstance(expression, LiteralExpr):
        return _literal_type(expression.value)
    if isinstance(expression, VariableExpr):
        value_type = bindings.get(expression.name)
        if value_type is None:
            raise CheckError(
                f"Step {step_name!r} references unknown variable {expression.name!r}"
            )
        return value_type
    if isinstance(expression, ModelVarExpr):
        return "ModelMetadata"
    if isinstance(expression, ToolVarExpr):
        return "ToolMetadata"
    if isinstance(expression, BreakExpr):
        return "Null"
    if isinstance(expression, ContinueExpr):
        return "Null"
    if isinstance(expression, RepeatExpr):
        count_type = _expression_type(
            expression.count, bindings, program, step_name=step_name
        )
        if count_type != "Number":
            raise CheckError(
                f"Step {step_name!r} repeat count expects 'Number', got {count_type!r}"
            )
        return _expression_type(
            expression.body, bindings, program, step_name=step_name
        )
    if isinstance(expression, ForExpr):
        sequence_type = _expression_type(
            expression.sequence, bindings, program, step_name=step_name
        )
        if not sequence_type.endswith("[]") or signature(sequence_type):
            raise CheckError(
                f"Step {step_name!r} for loop requires an array, "
                f"got {sequence_type!r}"
            )
        # The body sees the element in a child scope, so its binding cannot
        # widen or shadow the enclosing one.
        item_bindings = dict(bindings)
        item_bindings[expression.item_name] = sequence_type[:-2]
        body_type = _expression_type(
            expression.body, item_bindings, program, step_name=step_name
        )
        return f"{body_type}[]"
    if isinstance(expression, TryExpr):
        return _expression_type(
            expression.body, bindings, program, step_name=step_name
        )
    if isinstance(expression, FunctionExpr):
        function_bindings: dict[str, str] = {}
        for parameter in expression.parameters:
            if parameter.name in function_bindings:
                raise CheckError(
                    f"Step {step_name!r} function has duplicate parameter "
                    f"{parameter.name!r}"
                )
            _validate_type_name(program, parameter.type_name)
            function_bindings[parameter.name] = parameter.type_name
        body_type = _expression_type(
            expression.body, function_bindings, program,
            step_name=step_name, expected_type=expression.output_type,
        )
        output_type = expression.output_type or body_type
        if output_type != body_type:
            raise CheckError(
                f"Step {step_name!r} function body returns {body_type!r}, "
                f"but declared output is {output_type!r}"
            )
        return function_type(
            tuple(Field(p.name, p.type_name) for p in expression.parameters),
            output_type,
        )
    if isinstance(expression, MethodCallExpr):
        # Method dispatch on Text and array values is parsed but has no
        # checker or runtime implementation yet. Reject it here rather than
        # accepting a call the runtime cannot execute.
        _expression_type(
            expression.target, bindings, program, step_name=step_name
        )
        raise CheckError(
            f"Step {step_name!r} method call {expression.method!r} is not "
            f"implemented in Kosika v0.1"
        )
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
        if not base_type.endswith("[]") or signature(base_type) is not None:
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
        if (expected_type is None or not expected_type.endswith("[]")
                or signature(expected_type) is not None):
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
    if isinstance(expression, UnaryExpr):
        operand_type = _expression_type(
            expression.operand, bindings, program, step_name=step_name
        )
        required = "Bool" if expression.operator == "not" else "Number"
        if operand_type != required:
            raise CheckError(
                f"Step {step_name!r} operator {expression.operator!r} expects "
                f"{required!r}, got {operand_type!r}"
            )
        return required
    if isinstance(expression, BinaryExpr):
        left_type = _expression_type(
            expression.left, bindings, program, step_name=step_name
        )
        right_type = _expression_type(
            expression.right,
            bindings,
            program,
            step_name=step_name,
            expected_type=left_type,
        )
        operator = expression.operator
        if operator in {"+", "-", "*", "/", "<", "<=", ">", ">="}:
            if left_type == "Number" and right_type == "Number":
                return "Bool" if operator in {"<", "<=", ">", ">="} else "Number"
            if left_type == "Text" and right_type == "Text" and operator == "+":
                return "Text"
            if left_type == "Text" and right_type == "Number" and operator == "+":
                return "Text"
            raise CheckError(
                f"Step {step_name!r} operator {operator!r} expects "
                f"Number operands, got {left_type!r} and {right_type!r}"
            )
        if operator in {"and", "or"}:
            if left_type != "Bool" or right_type != "Bool":
                raise CheckError(
                    f"Step {step_name!r} operator {operator!r} expects Bool "
                    f"operands, got {left_type!r} and {right_type!r}"
                )
            return "Bool"
        if operator in {"==", "!="}:
            if left_type != right_type:
                raise CheckError(
                    f"Step {step_name!r} operator {operator!r} requires matching "
                    f"types, got {left_type!r} and {right_type!r}"
                )
            return "Bool"
        raise CheckError(f"Step {step_name!r} has unknown operator {operator!r}")
    if isinstance(expression, ConditionalExpr):
        condition_type = _expression_type(
            expression.condition, bindings, program, step_name=step_name
        )
        if condition_type != "Bool":
            raise CheckError(
                f"Step {step_name!r} condition expects 'Bool', "
                f"got {condition_type!r}"
            )
        true_type = _expression_type(
            expression.when_true,
            bindings,
            program,
            step_name=step_name,
            expected_type=expected_type,
        )
        false_type = _expression_type(
            expression.when_false,
            bindings,
            program,
            step_name=step_name,
            expected_type=expected_type or true_type,
        )
        if true_type != false_type:
            raise CheckError(
                f"Step {step_name!r} conditional branches differ: "
                f"{true_type!r} and {false_type!r}"
            )
        return true_type
    if isinstance(expression, FunctionRefExpr):
        function = next((item for item in program.functions
                         if item.name == expression.function_name), None)
        if function is None:
            raise CheckError(f"Unknown function reference {expression.function_name!r}")
        _check_arguments(program, None, expression.captures, bindings, step_name=step_name,
                         expected_fields={p.name: p.type_name for p in function.parameters
                                          if p.name.startswith('$capture:')})
        return public_type(function)
    if isinstance(expression, FunctionCallExpr):
        if expression.function_name in bindings:
            function_signature = signature(bindings[expression.function_name])
            if function_signature is None:
                raise CheckError(f"{expression.function_name!r} is not a function value")
            parameters, output = function_signature
            _check_arguments(program, None, expression.arguments, bindings,
                             step_name=step_name,
                             expected_fields={p.name: p.type_name for p in parameters})
            return output
        function = next(
            (
                item
                for item in program.functions
                if item.name == expression.function_name
            ),
            None,
        )
        if function is None:
            raise CheckError(
                f"Step {step_name!r} uses unknown function "
                f"{expression.function_name!r}"
            )
        _check_arguments(
            program,
            None,
            expression.arguments,
            bindings,
            step_name=step_name,
            expected_fields={
                field.name: field.type_name for field in function.parameters
            },
        )
        return function.output_type
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
    if any(function.nested for function in program.functions):
        raise CheckError("Unlowered nested functions: use lower_nested_functions(program)")
    if program.imports:
        raise CheckError("Unresolved imports: use load_program(path)")
    records = {record.name for record in program.records}
    tools = {tool.name: tool for tool in program.tools}
    agents = {agent.name: agent for agent in program.agents}
    workflows = {workflow.name: workflow for workflow in program.workflows}
    functions = {function.name: function for function in program.functions}
    models = {model.name: model for model in program.models}

    if len(models) != len(program.models):
        raise CheckError("Duplicate model declaration")
    for model in program.models:
        if model.input not in records or model.output not in records:
            raise CheckError(
                f"Model {model.name!r} requires known input and output records"
            )
        if model.deployment is not None and not model.deployment:
            raise CheckError(f"Model {model.name!r} deployment must not be empty")
        temperature = model.parameters.temperature
        if temperature is not None and (
            type(temperature) not in (int, float)
            or not math.isfinite(temperature)
            or not 0 <= temperature <= 2
        ):
            raise CheckError(
                f"Model {model.name!r} temperature must be between 0 and 2"
            )
        max_output_tokens = model.parameters.max_output_tokens
        if max_output_tokens is not None and (
            type(max_output_tokens) is not int
            or not 1 <= max_output_tokens <= 1_000_000
        ):
            raise CheckError(
                f"Model {model.name!r} max_output_tokens must be between 1 and 1000000"
            )
        for parameter_name, parameter_value, lower, upper in (
            ("top_p", model.parameters.top_p, 0, 1),
            ("frequency_penalty", model.parameters.frequency_penalty, -2, 2),
            ("presence_penalty", model.parameters.presence_penalty, -2, 2),
        ):
            if parameter_value is not None and (
                type(parameter_value) not in (int, float)
                or not math.isfinite(parameter_value)
                or not lower <= parameter_value <= upper
            ):
                raise CheckError(
                    f"Model {model.name!r} {parameter_name} must be between "
                    f"{lower} and {upper}"
                )
        stop = model.parameters.stop
        if len(stop) > 4 or len(set(stop)) != len(stop) or any(
            type(value) is not str or not 1 <= len(value) <= 256 for value in stop
        ):
            raise CheckError(
                f"Model {model.name!r} stop must contain up to four unique "
                "non-empty strings of at most 256 characters"
            )

    if len(records) != len(program.records):
        raise CheckError("Duplicate record declaration")
    if len(tools) != len(program.tools):
        raise CheckError("Duplicate tool declaration")
    if len(agents) != len(program.agents):
        raise CheckError("Duplicate agent declaration")
    swarms = {swarm.name: swarm for swarm in program.swarms}
    if len(swarms) != len(program.swarms):
        raise CheckError("Duplicate swarm declaration")
    if len(workflows) != len(program.workflows):
        raise CheckError("Duplicate workflow declaration")
    if len(functions) != len(program.functions):
        raise CheckError("Duplicate function declaration")

    def workflow_agents(name: str, seen: set[str]) -> set[str]:
        """Return agents used by a workflow, following nested workflow calls."""

        if name in seen or name not in workflows:
            return set()
        seen.add(name)
        found: set[str] = set()
        for item in workflows[name].steps:
            call = item.call
            if isinstance(call, (AgentCall, ToolCall)):
                found.add(call.agent_name)
            elif isinstance(call, WorkflowCall):
                found.update(workflow_agents(call.workflow_name, seen))
        return found

    for swarm in program.swarms:
        if not swarm.agents:
            raise CheckError(f"Swarm {swarm.name!r} requires at least one agent")
        if len(set(swarm.agents)) != len(swarm.agents):
            raise CheckError(f"Swarm {swarm.name!r} lists a duplicate agent")
        unknown = sorted(set(swarm.agents) - set(agents))
        if unknown:
            raise CheckError(
                f"Swarm {swarm.name!r} names unknown agent(s): {', '.join(unknown)}"
            )
        if swarm.workflow not in workflows:
            raise CheckError(
                f"Swarm {swarm.name!r} names unknown workflow {swarm.workflow!r}"
            )
        if not swarm.approval_required:
            raise CheckError(
                f"Swarm {swarm.name!r} must require human approval"
            )
        if (
            type(swarm.max_rounds) is not int
            or not 1 <= swarm.max_rounds <= 32
        ):
            raise CheckError("Swarm max_rounds must be an integer from 1 to 32")
        used = workflow_agents(swarm.workflow, set())
        participants = sorted(set(swarm.agents) - used)
        if participants:
            raise CheckError(
                f"Swarm {swarm.name!r} lists agent(s) that {swarm.workflow!r} never "
                f"uses: {', '.join(participants)}"
            )

    visited: dict[str, int] = {}
    active: set[str] = set()

    def visit(name: str) -> int:
        if name in active:
            raise CheckError(f"Recursive workflow call involving {name!r}")
        if name in visited:
            return visited[name]
        if name not in workflows:
            raise CheckError(f"Unknown workflow {name!r}")
        if len(active) >= 32:
            raise CheckError("Workflow call depth exceeds 32")
        active.add(name)
        height = 1
        for item in workflows[name].steps:
            if isinstance(item.call, WorkflowCall):
                height = max(height, 1 + visit(item.call.workflow_name))
        active.remove(name)
        if height > 32:
            raise CheckError("Workflow call depth exceeds 32")
        visited[name] = height
        return height

    for name in workflows:
        visit(name)

    for record in program.records:
        field_names = [field.name for field in record.fields]
        if len(field_names) != len(set(field_names)):
            raise CheckError(f"Record {record.name!r} has duplicate fields")
        for field in record.fields:
            if field.type_name.startswith('fn('):
                raise CheckError("Function values are not allowed in external record schemas")
            _validate_type_name(program, field.type_name)

    visited_functions: dict[str, int] = {}
    active_functions: list[str] = []
    function_graph = {
        name: tuple(call.function_name for call in static_function_references(function))
        for name, function in functions.items()
    }
    # Check every unannotated vertex, including cycles hidden by DFS memoization.
    for name, function in functions.items():
        if function.recursion_limit is not None:
            continue
        pending = list(function_graph[name])
        reached: set[str] = set()
        while pending:
            target = pending.pop()
            if target == name:
                raise CheckError(f"Recursive function call involving {name!r} requires recurse limits")
            if target not in reached:
                reached.add(target)
                pending.extend(function_graph.get(target, ()))

    def visit_function(name: str) -> int:
        if name in active_functions:
            cycle = active_functions[active_functions.index(name):]
            if any(functions[item].recursion_limit is None for item in cycle):
                raise CheckError(f"Recursive function call involving {name!r} requires recurse limits")
            return 0
        if name in visited_functions:
            return visited_functions[name]
        if name not in functions:
            raise CheckError(f"Unknown function {name!r}")
        if len(active_functions) >= 32:
            raise CheckError("Function call depth exceeds 32")
        active_functions.append(name)
        height = 1
        for call in static_function_references(functions[name]):
            height = max(height, 1 + visit_function(call.function_name))
        active_functions.pop()
        if height > 32:
            raise CheckError("Function call depth exceeds 32")
        visited_functions[name] = height
        return height

    for name in functions:
        visit_function(name)

    for function in program.functions:
        if function.recursion_limit is not None and (
            type(function.recursion_limit) is not int or not 1 <= function.recursion_limit <= 32
        ):
            raise CheckError("Recursion limit must be an integer from 1 to 32")
        _validate_type_name(program, function.output_type)
        function_bindings: dict[str, str] = {}
        for parameter in function.parameters:
            if parameter.name in function_bindings:
                raise CheckError(
                    f"Function {function.name!r} has duplicate parameter "
                    f"{parameter.name!r}"
                )
            _validate_type_name(program, parameter.type_name)
            function_bindings[parameter.name] = parameter.type_name
        for binding in function.bindings:
            if binding.name in function_bindings:
                raise CheckError(f"Function {function.name!r} cannot rebind {binding.name!r}")
            _validate_type_name(program, binding.type_name)
            binding_type = _expression_type(
                binding.value, function_bindings, program,
                step_name=f"function {function.name} local {binding.name}",
                expected_type=binding.type_name,
            )
            if binding_type != binding.type_name:
                raise CheckError(
                    f"Local {binding.name!r} expects {binding.type_name!r}, got {binding_type!r}"
                )
            function_bindings[binding.name] = binding.type_name
        actual_type = _expression_type(
            function.return_value,
            function_bindings,
            program,
            step_name=f"function {function.name}",
            expected_type=function.output_type,
        )
        if actual_type != function.output_type:
            raise CheckError(
                f"Function {function.name!r} returns {function.output_type!r}, "
                f"got {actual_type!r}"
            )

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
        if agent.instructions is not None and not agent.instructions.strip():
            raise CheckError(f"Agent {agent.name!r} instructions must not be empty")
        if agent.deadline_seconds is not None and (
            type(agent.deadline_seconds) not in (int, float)
            or not math.isfinite(agent.deadline_seconds)
            or not 0 < agent.deadline_seconds <= 86_400
        ):
            raise CheckError(
                f"Agent {agent.name!r} deadline_seconds must be between "
                "0 and 86400"
            )
        if type(agent.max_steps) is not int or not 1 <= agent.max_steps <= 64:
            raise CheckError(f"Agent {agent.name!r} max_steps must be between 1 and 64")
        if type(agent.max_tool_calls) is not int or not 1 <= agent.max_tool_calls <= 1_000_000:
            raise CheckError(
                f"Agent {agent.name!r} max_tool_calls must be between 1 and 1000000"
            )
        if type(agent.max_model_calls) is not int or not 1 <= agent.max_model_calls <= 1_000_000:
            raise CheckError(
                f"Agent {agent.name!r} max_model_calls must be between 1 and 1000000"
            )
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
        final_output: str | None
        if isinstance(final_call, WorkflowCall):
            child = workflows.get(final_call.workflow_name)
            final_output = child.output_type if child else None
        elif isinstance(final_call, FunctionCall):
            final_function = functions.get(final_call.function_name)
            final_output = final_function.output_type if final_function else None
        elif isinstance(final_call, AgentCall):
            final_agent = agents.get(final_call.agent_name)
            final_model = models.get(final_agent.model or "") if final_agent else None
            final_output = final_model.output if final_model else None
        elif isinstance(final_call, ModelCall):
            final_model = models.get(final_call.model_name)
            final_output = final_model.output if final_model else None
        else:
            final_tool = tools.get(final_call.tool_name)
            final_output = final_tool.output if final_tool else None
        if final_output is not None and final_output != workflow.output_type:
            final_kind = "tool" if isinstance(final_call, ToolCall) else "call"
            raise CheckError(
                f"Workflow {workflow.name!r} returns {workflow.output_type!r}, "
                f"but final {final_kind} returns {final_output!r}"
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
            elif isinstance(step.call, FunctionCall):
                if step.call.function_name in bindings:
                    function_signature = signature(bindings[step.call.function_name])
                    if function_signature is None:
                        raise CheckError(f"{step.call.function_name!r} is not a function value")
                    parameters, call_output = function_signature
                    call_input = None
                    expected_fields = {p.name: p.type_name for p in parameters}
                else:
                    called_function = functions.get(step.call.function_name)
                    if called_function is None:
                        raise CheckError(
                            f"Step {step.name!r} uses unknown function "
                            f"{step.call.function_name!r}"
                        )
                    call_input, call_output = None, called_function.output_type
                    expected_fields = {
                        field.name: field.type_name
                        for field in called_function.parameters
                    }
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
                _validate_type_name(program, step.binding_type)
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
