"""Lower lexical functions to private names and explicit, immutable captures.

Internal names contain '/' and '$', which cannot be spelled as source identifiers.
Captures use binding identities, not caller names, so shadowing is lexical.
"""

from __future__ import annotations

from dataclasses import replace

from .ast import (
    ArrayExpr,
    BinaryExpr,
    ConditionalExpr,
    Expression,
    Field,
    FieldExpr,
    FunctionCallExpr,
    FunctionDecl,
    FunctionRefExpr,
    IndexExpr,
    LocalBinding,
    MatchExpr,
    MethodCallExpr,
    Program,
    RecordExpr,
    UnaryExpr,
    VariableExpr,
)

# Source variable -> (lexical binding identity, type).
Variables = dict[str, tuple[str, str]]
# Source function -> (internal name, captured binding identities).
Functions = dict[str, tuple[str, tuple[str, ...]]]


def _expression(
    value: Expression,
    variables: Variables,
    environment: dict[str, str],
    functions: Functions,
) -> Expression:
    def walk(item: Expression) -> Expression:
        return _expression(item, variables, environment, functions)

    if isinstance(value, VariableExpr):
        binding = variables.get(value.name)
        return VariableExpr(environment[binding[0]]) if binding else value
    if isinstance(value, FunctionCallExpr):
        binding = variables.get(value.function_name)
        if binding:
            return FunctionCallExpr(
                environment[binding[0]],
                tuple((key, walk(arg)) for key, arg in value.arguments),
            )
        name, captures = functions.get(value.function_name, (value.function_name, ()))
        arguments = tuple((key, walk(argument)) for key, argument in value.arguments)
        arguments += tuple(
            (f"$capture:{key}", VariableExpr(environment[key])) for key in captures
        )
        return FunctionCallExpr(name, arguments)
    if isinstance(value, FunctionRefExpr):
        name, captures = functions.get(value.function_name, (value.function_name, ()))
        return FunctionRefExpr(
            name,
            tuple(
                (f"$capture:{key}", VariableExpr(environment[key])) for key in captures
            ),
        )
    if isinstance(value, FieldExpr):
        return replace(value, base=walk(value.base))
    if isinstance(value, IndexExpr):
        return replace(value, base=walk(value.base), index=walk(value.index))
    if isinstance(value, ArrayExpr):
        return replace(value, items=tuple(walk(item) for item in value.items))
    if isinstance(value, RecordExpr):
        return replace(
            value, fields=tuple((key, walk(item)) for key, item in value.fields)
        )
    if isinstance(value, MethodCallExpr):
        return replace(
            value,
            target=walk(value.target),
            arguments=tuple((key, walk(item)) for key, item in value.arguments),
        )
    if isinstance(value, UnaryExpr):
        return replace(value, operand=walk(value.operand))
    if isinstance(value, BinaryExpr):
        return replace(value, left=walk(value.left), right=walk(value.right))
    if isinstance(value, ConditionalExpr):
        return replace(
            value,
            condition=walk(value.condition),
            when_true=walk(value.when_true),
            when_false=walk(value.when_false),
        )
    if isinstance(value, MatchExpr):
        return replace(
            value,
            subject=walk(value.subject),
            arms=tuple(replace(arm, value=walk(arm.value)) for arm in value.arms),
        )
    return value


def lower_nested_functions(program: Program) -> Program:
    if not any(function.nested for function in program.functions):
        return program
    lowered: list[FunctionDecl] = []

    def lower(
        function: FunctionDecl,
        name: str,
        inherited: Variables,
        capture_types: dict[str, str],
        functions: Functions,
    ) -> None:
        variables = dict(inherited)
        environment = {key: f"$capture:{key}" for key in capture_types}
        types = dict(capture_types)
        for parameter in function.parameters:
            identity = f"{name}/{parameter.name}"
            variables[parameter.name] = (identity, parameter.type_name)
            types[identity] = parameter.type_name
            environment[identity] = parameter.name
        bindings: list[LocalBinding] = []
        for binding in function.bindings:
            value = _expression(binding.value, variables, environment, functions)
            bindings.append(replace(binding, value=value))
            identity = f"{name}/{binding.name}"
            variables[binding.name] = (identity, binding.type_name or "Any")
            types[identity] = binding.type_name or "Any"
            environment[identity] = binding.name
        local_functions = dict(functions)
        seen: set[str] = set()
        for child in function.nested:
            if child.name in seen:
                raise ValueError(
                    f"Duplicate nested function {child.name!r} in {name!r}"
                )
            seen.add(child.name)
            local_functions[child.name] = (f"{name}/{child.name}", tuple(types))
        result = _expression(
            function.return_value, variables, environment, local_functions
        )
        parameters = function.parameters + tuple(
            Field(f"$capture:{key}", type_name)
            for key, type_name in capture_types.items()
        )
        lowered.append(
            replace(
                function,
                name=name,
                parameters=parameters,
                bindings=tuple(bindings),
                return_value=result,
                nested=(),
            )
        )
        for child in function.nested:
            lower(child, f"{name}/{child.name}", variables, types, local_functions)

    for function in program.functions:
        lower(function, function.name, {}, {}, {})
    return replace(program, functions=lowered)
