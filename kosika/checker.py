from __future__ import annotations

import math
from dataclasses import fields, is_dataclass, replace
from typing import Any

from .ast import (
    AgentCall,
    ArrayExpr,
    Assignment,
    BinaryExpr,
    Block,
    BreakExpr,
    ConditionalExpr,
    ContinueExpr,
    FailExpr,
    Field,
    FieldExpr,
    ForExpr,
    FunctionCall,
    FunctionCallExpr,
    FunctionExpr,
    FunctionRefExpr,
    IndexExpr,
    LiteralExpr,
    LocalBinding,
    MatchExpr,
    MethodCallExpr,
    ModelCall,
    ModelVarExpr,
    Program,
    RecordExpr,
    RepeatExpr,
    ReturnExpr,
    Step,
    ThinkCall,
    ToolCall,
    ToolVarExpr,
    TryExpr,
    UnaryExpr,
    VariableExpr,
    WorkflowCall,
    function_body,
    method_function,
    static_function_references,
)
from .builtin_methods import MethodError, did_you_mean
from .builtin_methods import parameter_types as method_parameter_types
from .builtin_methods import resolve as resolve_method
from .builtin_methods import return_type as method_return_type
from .function_types import (
    NEVER,
    assignable,
    function_type,
    is_map,
    is_optional,
    public_type,
    signature,
    unify,
)
from .sugar import resolve_unnamed
from .system_tools import declaration_problem


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
    if type_name.endswith(("[]", "{}")):
        _validate_type_name(program, type_name[:-2])
        return
    if type_name.endswith("?"):
        if type_name.endswith("??") or type_name == "Null?":
            raise CheckError(f"Unknown field type {type_name!r}")
        _validate_type_name(program, type_name[:-1])
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


def _check_field_argument(
    program: Program,
    argument: object,
    element_type: str,
    allowed: list[str],
    *,
    step_name: str,
    method: str,
) -> None:
    """A `field:` argument names a field of the array's record element.

    It is a text literal, not a computed value, so the checker can prove the
    field exists and has a type the method can order, add, or group by.
    """

    if element_type not in {record.name for record in program.records}:
        array_type = element_type + "[]"
        raise CheckError(
            f"Step {step_name!r} unknown method: {method!r} needs an array of "
            f"records, got {array_type!r}"
        )
    if not isinstance(argument, LiteralExpr) or not isinstance(argument.value, str):
        raise CheckError(
            f"Step {step_name!r} method {method!r} takes (field: Text), got "
            "a computed value; write the field name in quotes"
        )
    fields = _record_fields(program, element_type)
    field_type = fields.get(argument.value)
    if field_type is None:
        raise CheckError(
            f"Step {step_name!r} references unknown field "
            f"{argument.value!r} on {element_type!r}."
            f"{did_you_mean(argument.value, fields)}"
        )
    if field_type not in allowed:
        wanted = " or ".join(allowed)
        raise CheckError(
            f"Step {step_name!r} method {method!r} argument 'field' expects "
            f"{wanted!r}, got {field_type!r}"
        )


def _absent_hint(type_name: str) -> str:
    if is_map(type_name):
        return "a map is read with .get(key: ...), .has(key: ...), or .keys()"
    return (
        "the value may be absent; use .or(default: ...) to supply a fallback, "
        ".exists() to test it, or .value() to require it"
    )


class _Env(dict):
    """Name -> type map that also records which names may be assigned.

    Subclasses ``dict`` so every existing ``name in bindings``,
    ``bindings.get(name)`` and indexing call keeps working unchanged. Only scope
    creation needs to know this class exists, and those go through ``child()``.
    """

    def __init__(self, source=None, *, mutable: set[str] | None = None):
        super().__init__(source or {})
        self.mutable: set[str] = set() if mutable is None else mutable
        # The result type of the function being checked, for early returns.
        # None outside a function body, where `return` is not allowed.
        self.returns: str | None = None
        # Optional names proved present by a test such as `if x.exists()`.
        # Their type here is the plain `T`; `exists`, `value`, and `or` still
        # work on them so code written before narrowing keeps checking.
        self.present: set[str] = set()

    def child(self) -> _Env:
        # A nested scope still sees the enclosing mutable names, so a `var`
        # declared outside a loop stays assignable inside it.
        scope = _Env(self, mutable=set(self.mutable))
        scope.returns = self.returns
        scope.present = set(self.present)
        return scope

    def narrowed(self, names: frozenset[str]) -> _Env:
        """A child scope in which each of `names` is known to hold a value."""
        scope = self.child()
        scope.prove_present(names)
        return scope

    def prove_present(self, names: frozenset[str]) -> None:
        for name in names:
            self[name] = self[name][:-1]
            self.present.add(name)


_NOTHING: frozenset[str] = frozenset()


def _proved_present(
    condition: object, bindings: _Env
) -> tuple[frozenset[str], frozenset[str]]:
    """Names a condition proves present: (when it is true, when it is false).

    Only a plain immutable name counts. A `var` could be set back to null
    after the test, and a field or index is not a name the scope tracks.
    """

    if (
        isinstance(condition, MethodCallExpr)
        and condition.method == "exists"
        and not condition.arguments
        and isinstance(condition.target, VariableExpr)
    ):
        name = condition.target.name
        if (
            name not in bindings.mutable
            and name not in bindings.present
            and is_optional(bindings.get(name) or "")
        ):
            return frozenset({name}), _NOTHING
        return _NOTHING, _NOTHING
    if isinstance(condition, UnaryExpr) and condition.operator == "not":
        when_true, when_false = _proved_present(condition.operand, bindings)
        return when_false, when_true
    if isinstance(condition, BinaryExpr) and condition.operator in ("and", "or"):
        left = _proved_present(condition.left, bindings)
        right = _proved_present(condition.right, bindings)
        if condition.operator == "and":
            return left[0] | right[0], _NOTHING
        return _NOTHING, left[1] | right[1]
    return _NOTHING, _NOTHING


def _always_leaves(expression: object) -> bool:
    """True when evaluating `expression` never continues to the next line."""

    if isinstance(expression, (ReturnExpr, BreakExpr, ContinueExpr, FailExpr)):
        return True
    if isinstance(expression, Block):
        if expression.result is not None:
            return _always_leaves(expression.result)
        return bool(expression.statements) and _always_leaves(expression.statements[-1])
    if isinstance(expression, ConditionalExpr):
        return _always_leaves(expression.when_true) and _always_leaves(
            expression.when_false
        )
    return False


def _present_after(statement: object, bindings: _Env) -> frozenset[str]:
    """Names proved present for the lines after a guard statement.

    `if not x.exists() { return ... }` leaves when `x` is absent, so every
    line after it may use `x` directly.
    """

    if not isinstance(statement, ConditionalExpr):
        return _NOTHING
    when_true, when_false = _proved_present(statement.condition, bindings)
    names = _NOTHING
    if _always_leaves(statement.when_true):
        names |= when_false
    if _always_leaves(statement.when_false):
        names |= when_true
    return names


def _expression_type(
    expression: object,
    bindings: _Env,
    program: Program,
    *,
    step_name: str,
    expected_type: str | None = None,
) -> str:
    # A literal written where `T?` is wanted is typed as the plain `T`; the
    # caller's assignability check then accepts it.
    if expected_type is not None and is_optional(expected_type):
        expected_type = expected_type[:-1]
    if isinstance(expression, Assignment):
        target_type = bindings.get(expression.target)
        if target_type is None:
            raise CheckError(
                f"Step {step_name!r} assigns to unknown variable {expression.target!r}"
            )
        if expression.target not in bindings.mutable:
            raise CheckError(
                f"Step {step_name!r} cannot assign to {expression.target!r}: "
                f"declare it with 'var' to make it assignable"
            )
        assigned_type = _expression_type(
            expression.value,
            bindings,
            program,
            step_name=step_name,
            expected_type=target_type,
        )
        if not assignable(assigned_type, target_type):
            raise CheckError(
                f"Step {step_name!r} assigns {assigned_type!r} to "
                f"{expression.target!r} of type {target_type!r}"
            )
        return target_type
    if isinstance(expression, Block):
        inner = bindings.child()
        for statement in expression.statements:
            if isinstance(statement, LocalBinding):
                if statement.name in bindings:
                    raise CheckError(
                        f"Step {step_name!r} local {statement.name!r} shadows an "
                        f"enclosing binding"
                    )
                if statement.type_name is not None:
                    _validate_type_name(program, statement.type_name)
                declared = _expression_type(
                    statement.value,
                    inner,
                    program,
                    step_name=step_name,
                    expected_type=statement.type_name,
                )
                if statement.type_name is not None and declared != statement.type_name:
                    raise CheckError(
                        f"Step {step_name!r} local {statement.name!r} expects "
                        f"{statement.type_name!r}, got {declared!r}"
                    )
                inner[statement.name] = declared
                if statement.mutable:
                    inner.mutable.add(statement.name)
            else:
                _expression_type(statement, inner, program, step_name=step_name)
                inner.prove_present(_present_after(statement, inner))
        if expression.result is None:
            return "Null"
        return _expression_type(
            expression.result,
            inner,
            program,
            step_name=step_name,
            expected_type=expected_type,
        )
    if isinstance(expression, MatchExpr):
        subject_type = _expression_type(
            expression.subject, bindings, program, step_name=step_name
        )
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
                exhaustive = subject_type == "Null" or (
                    subject_type == "Bool" and len(seen) == 2
                )
            arm_type = _expression_type(
                arm.value,
                bindings,
                program,
                step_name=step_name,
                expected_type=result_type,
            )
            merged = arm_type if result_type is None else unify(result_type, arm_type)
            if merged is None:
                raise CheckError("Match branches must have the same result type")
            result_type = merged
        if not exhaustive or result_type is None:
            raise CheckError("Non-exhaustive match: add a final case _")
        return result_type
    if isinstance(expression, LiteralExpr):
        return _literal_type(expression.value)
    if isinstance(expression, VariableExpr):
        value_type = bindings.get(expression.name)
        if value_type is None:
            raise CheckError(
                f"Step {step_name!r} references unknown variable {expression.name!r}."
                f"{did_you_mean(expression.name, bindings)}"
            )
        return value_type
    if isinstance(expression, ModelVarExpr):
        return "ModelMetadata"
    if isinstance(expression, ToolVarExpr):
        return "ToolMetadata"
    # `break` and `continue` never produce a value, so they agree with
    # whatever the other branch of a choice yields.
    if isinstance(expression, (BreakExpr, ContinueExpr)):
        return NEVER
    if isinstance(expression, ReturnExpr):
        if bindings.returns is None:
            raise CheckError(f"Step {step_name!r} uses return outside a function body")
        returned_type = _expression_type(
            expression.value,
            bindings,
            program,
            step_name=step_name,
            expected_type=bindings.returns,
        )
        if not assignable(returned_type, bindings.returns):
            raise CheckError(
                f"Step {step_name!r} early return expects {bindings.returns!r}, "
                f"got {returned_type!r}"
            )
        return NEVER
    if isinstance(expression, RepeatExpr):
        count_type = _expression_type(
            expression.count, bindings, program, step_name=step_name
        )
        if count_type != "Number":
            raise CheckError(
                f"Step {step_name!r} repeat count expects 'Number', got {count_type!r}"
            )
        repeated = _expression_type(
            expression.body, bindings, program, step_name=step_name
        )
        return "Null" if repeated == NEVER else repeated
    if isinstance(expression, ForExpr):
        sequence_type = _expression_type(
            expression.sequence, bindings, program, step_name=step_name
        )
        if not sequence_type.endswith("[]") or signature(sequence_type):
            raise CheckError(
                f"Step {step_name!r} for loop requires an array, got {sequence_type!r}"
            )
        # The body sees the element in a child scope, so its binding cannot
        # widen or shadow the enclosing one.
        item_bindings = bindings.child()
        item_bindings[expression.item_name] = sequence_type[:-2]
        if expression.condition is not None:
            condition_type = _expression_type(
                expression.condition, item_bindings, program, step_name=step_name
            )
            if condition_type != "Bool":
                raise CheckError(
                    f"Step {step_name!r} for loop where condition expects 'Bool', "
                    f"got {condition_type!r}"
                )
            item_bindings.prove_present(
                _proved_present(expression.condition, item_bindings)[0]
            )
        body_type = _expression_type(
            expression.body,
            item_bindings,
            program,
            step_name=step_name,
            expected_type=expected_type[:-2]
            if expected_type is not None and expected_type.endswith("[]")
            else None,
        )
        return "Null[]" if body_type == NEVER else f"{body_type}[]"
    if isinstance(expression, FailExpr):
        reason_type = _expression_type(
            expression.message, bindings, program, step_name=step_name
        )
        if reason_type != "Text":
            raise CheckError(
                f"Step {step_name!r} fail expects a Text reason, got {reason_type!r}"
            )
        # Like `return`, it never gives a value, so it agrees with whatever
        # the other branch of a choice yields.
        return NEVER
    if isinstance(expression, TryExpr):
        body_type = _expression_type(
            expression.body,
            bindings.child(),
            program,
            step_name=step_name,
            expected_type=expected_type,
        )
        if expression.fallback is None:
            return body_type
        rescue = bindings.child()
        if expression.error_name is not None:
            rescue[expression.error_name] = "Text"
        fallback_type = _expression_type(
            expression.fallback,
            rescue,
            program,
            step_name=step_name,
            expected_type=expected_type if body_type == NEVER else body_type,
        )
        if body_type == NEVER:
            return fallback_type
        if fallback_type not in (NEVER, body_type):
            raise CheckError(
                f"Step {step_name!r} try gives {body_type!r} but its otherwise "
                f"gives {fallback_type!r}; both must give the same type"
            )
        return body_type
    if isinstance(expression, FunctionExpr):
        function_bindings = _Env()
        for parameter in expression.parameters:
            if parameter.name in function_bindings:
                raise CheckError(
                    f"Step {step_name!r} function has duplicate parameter "
                    f"{parameter.name!r}"
                )
            _validate_type_name(program, parameter.type_name)
            function_bindings[parameter.name] = parameter.type_name
        body_type = _expression_type(
            expression.body,
            function_bindings,
            program,
            step_name=step_name,
            expected_type=expression.output_type,
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
        # Methods are a closed built-in set, resolved from the receiver's type.
        receiver_type = _expression_type(
            expression.target, bindings, program, step_name=step_name
        )
        # Unless the receiver is a record that has a function of that name:
        # then the call is that function, with the record as `self`.
        owner = next(
            (
                record
                for record in program.records
                if record.name == receiver_type and expression.method in record.methods
            ),
            None,
        )
        if owner is not None:
            belonging = next(
                function
                for function in program.functions
                if function.name == method_function(owner.name, expression.method)
            )
            rest = resolve_unnamed(
                expression.arguments,
                [parameter.name for parameter in belonging.parameters[1:]],
            )
            return _expression_type(
                FunctionCallExpr(belonging.name, (("self", expression.target), *rest)),
                bindings,
                program,
                step_name=step_name,
            )
        if (
            isinstance(expression.target, VariableExpr)
            and expression.target.name in bindings.present
            and expression.method in ("exists", "value", "or")
        ):
            receiver_type += "?"
        try:
            method = resolve_method(receiver_type, expression.method)
        except MethodError as exc:
            raise CheckError(f"Step {step_name!r} unknown method: {exc}") from exc
        expected = dict(method_parameter_types(method, receiver_type))
        method_arguments = resolve_unnamed(expression.arguments, list(expected))
        supplied = [name for name, _ in method_arguments]
        if sorted(supplied) != sorted(expected):
            wanted = ", ".join(
                f"{name}: {'Text' if kind.startswith('$field') else kind}"
                for name, kind in expected.items()
            )
            hint = did_you_mean(supplied[0], expected) if supplied else ""
            raise CheckError(
                f"Step {step_name!r} method {expression.method!r} takes "
                f"({wanted}), got ({', '.join(supplied)}).{hint}"
            )
        for name, argument in method_arguments:
            if expected[name].startswith("$field"):
                _check_field_argument(
                    program,
                    argument,
                    receiver_type[:-2],
                    expected[name].split(":", 1)[1].split("|"),
                    step_name=step_name,
                    method=expression.method,
                )
                continue
            argument_type = _expression_type(
                argument,
                bindings,
                program,
                step_name=step_name,
                expected_type=expected[name],
            )
            if not assignable(argument_type, expected[name]):
                raise CheckError(
                    f"Step {step_name!r} method {expression.method!r} argument "
                    f"{name!r} expects {expected[name]!r}, got {argument_type!r}"
                )
        return method_return_type(method, receiver_type)
    if isinstance(expression, FieldExpr):
        base_type = _expression_type(
            expression.base, bindings, program, step_name=step_name
        )
        if base_type.endswith("[]"):
            raise CheckError(
                f"Step {step_name!r} cannot access field {expression.field_name!r} "
                f"on array type {base_type!r}"
            )
        if is_optional(base_type) or is_map(base_type):
            raise CheckError(
                f"Step {step_name!r} cannot access field {expression.field_name!r} "
                f"on {base_type!r}: {_absent_hint(base_type)}"
            )
        fields = _record_fields(program, base_type)
        field_type = fields.get(expression.field_name)
        if field_type is None:
            raise CheckError(
                f"Step {step_name!r} references unknown field "
                f"{expression.field_name!r} on {base_type!r}."
                f"{did_you_mean(expression.field_name, fields)}"
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
        if expected_type is None:
            # No type from the context: `var items = [1, 2, 3]`. The items
            # decide it when they agree. An empty literal, or items of
            # different types, stay ambiguous, so ask rather than guess.
            inferred: str | None = None
            for item in expression.items:
                item_type = _expression_type(
                    item, bindings, program, step_name=step_name
                )
                inferred = item_type if inferred is None else unify(inferred, item_type)
                if inferred is None:
                    break
            if inferred is None or inferred in (NEVER, "Null"):
                raise CheckError(
                    f"Step {step_name!r} cannot infer the type of an array literal; "
                    f"annotate the binding with an array type"
                )
            return f"{inferred}[]"
        if not expected_type.endswith("[]") or signature(expected_type) is not None:
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
            if not assignable(actual_type, element_type):
                raise CheckError(
                    f"Step {step_name!r} array item expects {element_type!r}, "
                    f"got {actual_type!r}"
                )
        return expected_type
    if isinstance(expression, RecordExpr) and expression.base is not None:
        # `{..task, done: true}`: the type is the base's own, so nothing has
        # to be inferred, and only the fields named are checked.
        base_type = _expression_type(
            expression.base, bindings, program, step_name=step_name
        )
        if base_type not in {record.name for record in program.records}:
            raise CheckError(
                f"Step {step_name!r} record update needs a record after '..', "
                f"got {base_type!r}"
            )
        known_fields = _record_fields(program, base_type)
        changed = [name for name, _ in expression.fields]
        if len(changed) != len(set(changed)):
            raise CheckError(
                f"Step {step_name!r} record update names a field more than once"
            )
        for name, value in expression.fields:
            if name not in known_fields:
                raise CheckError(
                    f"Step {step_name!r} record update names {name!r}, which is "
                    f"not a field of {base_type!r}."
                    f"{did_you_mean(name, known_fields)}"
                )
            given = _expression_type(
                value,
                bindings,
                program,
                step_name=step_name,
                expected_type=known_fields[name],
            )
            if not assignable(given, known_fields[name]):
                raise CheckError(
                    f"Step {step_name!r} record update field {name!r} expects "
                    f"{known_fields[name]!r}, got {given!r}"
                )
        return base_type
    if isinstance(expression, RecordExpr):
        if expected_type is None:
            raise CheckError(
                f"Step {step_name!r} cannot infer the type of a record literal; "
                f"annotate the binding with a record type"
            )
        if is_map(expected_type):
            keys = [name for name, _ in expression.fields]
            if len(keys) != len(set(keys)):
                raise CheckError(
                    f"Step {step_name!r} has duplicate record literal fields"
                )
            value_type = expected_type[:-2]
            for name, value in expression.fields:
                actual_type = _expression_type(
                    value,
                    bindings,
                    program,
                    step_name=step_name,
                    expected_type=value_type,
                )
                if not assignable(actual_type, value_type):
                    raise CheckError(
                        f"Step {step_name!r} record field {name!r} expects "
                        f"{value_type!r}, got {actual_type!r}"
                    )
            return expected_type
        if expected_type.endswith(("[]", "{}")):
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
                f"Step {step_name!r} has unknown record fields {sorted(unknown)!r}."
                f"{did_you_mean(sorted(unknown)[0], fields)}"
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
            if not assignable(actual_type, fields[name]):
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
        # The right side of `and` runs only when the left is true, and of `or`
        # only when it is false, so what the left proves holds on the right:
        # `n.exists() and n > 0`.
        right_bindings = bindings
        if expression.operator in ("and", "or"):
            when_true, when_false = _proved_present(expression.left, bindings)
            proved = when_true if expression.operator == "and" else when_false
            if proved:
                right_bindings = bindings.narrowed(proved)
        right_type = _expression_type(
            expression.right,
            right_bindings,
            program,
            step_name=step_name,
            expected_type=left_type,
        )
        operator = expression.operator
        if operator in {"+", "-", "*", "/", "%", "<", "<=", ">", ">="}:
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
            if unify(left_type, right_type) is None:
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
                f"Step {step_name!r} condition expects 'Bool', got {condition_type!r}"
            )
        present_if_true, present_if_false = _proved_present(
            expression.condition, bindings
        )
        true_type = _expression_type(
            expression.when_true,
            bindings.narrowed(present_if_true) if present_if_true else bindings,
            program,
            step_name=step_name,
            expected_type=expected_type,
        )
        false_type = _expression_type(
            expression.when_false,
            bindings.narrowed(present_if_false) if present_if_false else bindings,
            program,
            step_name=step_name,
            expected_type=expected_type or true_type,
        )
        branch_type = unify(true_type, false_type)
        if branch_type is None:
            raise CheckError(
                f"Step {step_name!r} conditional branches differ: "
                f"{true_type!r} and {false_type!r}"
            )
        return branch_type
    if isinstance(expression, FunctionRefExpr):
        function = next(
            (
                item
                for item in program.functions
                if item.name == expression.function_name
            ),
            None,
        )
        if function is None:
            raise CheckError(f"Unknown function reference {expression.function_name!r}")
        _check_arguments(
            program,
            None,
            expression.captures,
            bindings,
            step_name=step_name,
            expected_fields={
                p.name: p.type_name
                for p in function.parameters
                if p.name.startswith("$capture:")
            },
        )
        return public_type(function)
    if isinstance(expression, FunctionCallExpr):
        if expression.function_name in bindings:
            function_signature = signature(bindings[expression.function_name])
            if function_signature is None:
                raise CheckError(
                    f"{expression.function_name!r} is not a function value"
                )
            parameters, output = function_signature
            _check_arguments(
                program,
                None,
                expression.arguments,
                bindings,
                step_name=step_name,
                expected_fields={p.name: p.type_name for p in parameters},
            )
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
                f"Step {step_name!r} uses unknown function {expression.function_name!r}"
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
    bindings: _Env,
    *,
    step_name: str,
    expected_fields: dict[str, str] | None = None,
) -> None:
    argument_names = [name for name, _ in arguments]
    if "" in argument_names:
        raise CheckError(f"Step {step_name!r} argument needs a name; write name: value")
    if len(argument_names) != len(set(argument_names)):
        raise CheckError(f"Step {step_name!r} has duplicate tool arguments")
    expected = (
        expected_fields
        if expected_fields is not None
        else ({} if tool_input is None else _record_fields(program, tool_input))
    )
    provided = set(argument_names)
    if "" in provided:
        raise CheckError(f"Step {step_name!r} argument needs a name; write name: value")
    unknown = provided - set(expected)
    if unknown:
        raise CheckError(
            f"Step {step_name!r} has unknown tool arguments {sorted(unknown)!r}."
            f"{did_you_mean(sorted(unknown)[0], expected)}"
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
        if not assignable(actual_type, expected_type):
            raise CheckError(
                f"Step {step_name!r} argument {name!r} expects {expected_type!r}, "
                f"got {actual_type!r}"
            )


def _call_output(program: Program, call: object) -> str | None:
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


def _check_fallbacks(program: Program) -> None:
    """Check `otherwise` by checking the program each fallback would make.

    A step with a fallback is valid when the program is valid with the first
    call and also valid with the fallback in its place, and both calls produce
    the same type. Neither call may be a tool that changes the outside world:
    after such a call fails, whether it happened is not known, so quietly
    doing something else instead would hide that.
    """

    tools = {tool.name: tool for tool in program.tools}
    for workflow in program.workflows:
        for step in workflow.steps:
            if step.fallback is None:
                continue
            for call in (step.call, step.fallback):
                tool = tools.get(call.tool_name) if isinstance(call, ToolCall) else None
                if tool is not None and tool.access == "execute":
                    raise CheckError(
                        f"Step {step.name!r} cannot use otherwise with execute "
                        f"tool {tool.name!r}: a failed action may still have happened"
                    )
    without = replace(
        program,
        workflows=[
            replace(
                workflow,
                steps=tuple(replace(step, fallback=None) for step in workflow.steps),
            )
            for workflow in program.workflows
        ],
    )
    swapped = replace(
        program,
        workflows=[
            replace(
                workflow,
                steps=tuple(
                    replace(
                        step,
                        call=step.fallback or step.call,
                        fallback=None,
                        retries=step.retries if step.fallback is None else 0,
                        votes=step.votes if step.fallback is None else 0,
                        escalate=step.escalate if step.fallback is None else None,
                    )
                    for step in workflow.steps
                ),
            )
            for workflow in program.workflows
        ],
    )
    # The program must first be sound without its fallbacks. A fault in a
    # declaration, such as a tool that returns an unknown record, is then
    # reported as itself and not as the type disagreement it also causes.
    check(without)
    for workflow in program.workflows:
        for step in workflow.steps:
            if step.fallback is None:
                continue
            first = _call_output(program, step.call)
            second = _call_output(program, step.fallback)
            if first is not None and second is not None and first != second:
                raise CheckError(
                    f"Step {step.name!r} otherwise branch returns {second!r}, "
                    f"but the step returns {first!r}"
                )
    check(swapped)


def _check_verify(program: Program) -> None:
    """Check `verify` by checking the program in which the check is a step.

    A verified step is valid when the program is valid with the check
    written as an ordinary step straight after it, where the answer is in
    scope under the step's own name. The check must be a function that
    returns Text, and only a model's answer can be checked this way.
    """

    functions = {function.name: function for function in program.functions}
    workflows = []
    for workflow in program.workflows:
        steps: list[Step] = []
        for index, step in enumerate(workflow.steps):
            if step.verify is None:
                steps.append(step)
                continue
            if not isinstance(step.call, ModelCall):
                raise CheckError(
                    f"Step {step.name!r} cannot verify: only a model step "
                    "(use_model or a thinking step) gives an answer to check"
                )
            if step.branches:
                raise CheckError(
                    f"Step {step.name!r} cannot verify: a match step makes "
                    "one of several calls"
                )
            checker = (
                functions.get(step.verify.function_name)
                if isinstance(step.verify, FunctionCall)
                else None
            )
            if checker is None:
                raise CheckError(
                    f"Step {step.name!r} cannot verify: the check must be a "
                    "function declared in the program"
                )
            if checker.output_type != "Text":
                raise CheckError(
                    f"Step {step.name!r} cannot verify: function "
                    f"{checker.name!r} must return Text, the reason the answer "
                    f'is refused or "" when it is fine, but returns '
                    f"{checker.output_type!r}"
                )
            plain = replace(step, verify=None)
            steps.append(plain)
            check_name = f"{step.name}__verified"
            steps.append(Step(check_name, step.verify, check_name, None, check_name))
            if index == len(workflow.steps) - 1:
                # The workflow's value is its last step's. Put the answer
                # back in that place so the workflow's own type still holds.
                again = f"{step.name}__answer"
                steps.append(
                    replace(
                        plain,
                        name=again,
                        binding_name=again,
                        return_name=again,
                        fallback=None,
                        retries=0,
                        votes=0,
                    )
                )
        workflows.append(replace(workflow, steps=tuple(steps)))
    check(replace(program, workflows=workflows))


def _check_branches(program: Program) -> None:
    """Check a `match` step by checking the program each branch would make.

    Every branch must produce the same type, the last one must be `case _`
    so that some call is always made, and no value may be listed twice. A
    branch may not be a tool that changes the world: which call a step makes
    is decided as the workflow runs, after any approval of the plan.
    """

    tools = {tool.name: tool for tool in program.tools}
    most = 0
    for workflow in program.workflows:
        for step in workflow.steps:
            if not step.branches:
                continue
            most = max(most, len(step.branches))
            patterns = [pattern for pattern, _ in step.branches]
            if patterns[-1] is not None or None in patterns[:-1]:
                raise CheckError(
                    f"Step {step.name!r} match must end with exactly one `case _`"
                )
            seen = [(type(pattern), pattern) for pattern in patterns[:-1]]
            if len(seen) != len(set(seen)):
                raise CheckError(f"Step {step.name!r} match lists a value twice")
            outputs = set()
            for _, call in step.branches:
                tool = tools.get(call.tool_name) if isinstance(call, ToolCall) else None
                if tool is not None and tool.access == "execute":
                    raise CheckError(
                        f"Step {step.name!r} match cannot choose execute tool "
                        f"{tool.name!r}"
                    )
                outputs.add(_call_output(program, call))
            outputs.discard(None)
            if len(outputs) > 1:
                raise CheckError(
                    f"Step {step.name!r} match branches return different "
                    f"types: {', '.join(sorted(map(str, outputs)))}"
                )
    for index in range(most):
        check(
            replace(
                program,
                workflows=[
                    replace(
                        workflow,
                        steps=tuple(
                            replace(
                                step,
                                call=step.branches[min(index, len(step.branches) - 1)][
                                    1
                                ],
                                branches=(),
                            )
                            if step.branches
                            else step
                            for step in workflow.steps
                        ),
                    )
                    for workflow in program.workflows
                ],
            )
        )


#: Names a record's own function may not take, because a map already
#: answers to them and a record and a map look alike when a program runs.
_MAP_METHODS = ("get", "has", "set", "remove", "keys", "values", "length")


def _check_record_methods(program: Program) -> None:
    shapes: dict[tuple[str, frozenset[str]], str] = {}
    for record in program.records:
        fields = {field.name for field in record.fields}
        for method in record.methods:
            if record.methods.count(method) > 1:
                raise CheckError(
                    f"Record {record.name!r} method {method!r} is declared "
                    "more than once"
                )
            if method in fields:
                raise CheckError(
                    f"Record {record.name!r} method {method!r} has the same "
                    "name as one of its fields"
                )
            if method in _MAP_METHODS:
                raise CheckError(
                    f"Record {record.name!r} method {method!r} cannot use that "
                    f"name; these belong to maps: {', '.join(_MAP_METHODS)}"
                )
            # A record is told apart by its fields when a program runs, so
            # two records with the same fields cannot both answer to a name.
            key = (method, frozenset(fields))
            if key in shapes:
                raise CheckError(
                    f"Record {record.name!r} method {method!r} is also on "
                    f"{shapes[key]!r}, which has exactly the same fields; "
                    "give one of the methods another name"
                )
            shapes[key] = record.name


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


def _check_parallel(program: Program) -> None:
    """Steps written in one `parallel` block must be able to run apart."""

    tools = {tool.name: tool for tool in program.tools}
    for workflow in program.workflows:
        blocks: dict[int, list[Step]] = {}
        for step in workflow.steps:
            if step.group:
                blocks.setdefault(step.group, []).append(step)
        for members in blocks.values():
            names = {member.binding_name for member in members}
            for member in members:
                reason = ""
                call = member.call
                if member.branches:
                    reason = "a match step chooses its call as the workflow runs"
                elif isinstance(call, WorkflowCall):
                    reason = "it calls a workflow"
                elif isinstance(call, AgentCall):
                    reason = "it asks an agent, which chooses its own tools"
                elif isinstance(call, ToolCall):
                    tool = tools.get(call.tool_name)
                    if tool is not None and tool.access == "execute":
                        reason = "it changes something, and changes happen in order"
                    elif tool is not None and tool.source.startswith(
                        ("memory:", "board:")
                    ):
                        reason = "memory and boards are read and written in order"
                if reason:
                    raise CheckError(
                        f"Step {member.name!r} cannot run in parallel: {reason}"
                    )
                used = _names_used((call, member.verify, member.fallback)) & (
                    names - {member.binding_name}
                )
                if used:
                    raise CheckError(
                        f"Step {member.name!r} cannot run in parallel: it uses "
                        f"{sorted(used)[0]!r}, which is worked out in the same "
                        "parallel block"
                    )


def check(program: Program) -> None:
    _check_record_methods(program)
    _check_parallel(program)
    if any(
        isinstance(step.call, ThinkCall)
        for workflow in program.workflows
        for step in workflow.steps
    ):
        raise CheckError("Unlowered thinking steps: use lower_thinking(program)")
    for workflow in program.workflows:
        kinds = [kind for kind, _ in workflow.budget]
        for kind, amount in workflow.budget:
            label = kind.replace("_", " ")
            if kinds.count(kind) > 1:
                raise CheckError(
                    f"Workflow {workflow.name!r} budget names {label} more than once"
                )
            whole = type(amount) is int and 1 <= amount <= 1_000_000
            if kind == "seconds":
                whole = type(amount) in (int, float) and 0 < amount <= 86_400
            if not whole:
                raise CheckError(
                    f"Workflow {workflow.name!r} budget for {label} is out of "
                    f"range, got {amount}"
                )
        for step in workflow.steps:
            if step.votes:
                asked = (
                    next(
                        (m for m in program.models if m.name == step.call.model_name),
                        None,
                    )
                    if isinstance(step.call, ModelCall)
                    else None
                )
                if asked is None:
                    raise CheckError(
                        f"Step {step.name!r} cannot vote: only a model step "
                        "(use_model or a thinking step) can be asked several times"
                    )
                if asked.verb == "summarize":
                    raise CheckError(
                        f"Step {step.name!r} cannot vote: two summaries are "
                        "never word for word the same, so they cannot agree"
                    )
                if step.votes not in (3, 5, 7, 9):
                    raise CheckError(
                        f"Step {step.name!r} cannot vote: the count must be "
                        f"3, 5, 7, or 9, got {step.votes}"
                    )
            if step.escalate is not None:
                if not isinstance(step.call, ModelCall) or step.branches:
                    raise CheckError(
                        f"Step {step.name!r} cannot escalate: only a model step "
                        "(use_model or a thinking step) can be asked of "
                        "another model"
                    )
                if step.votes:
                    raise CheckError(
                        f"Step {step.name!r} cannot escalate: a voting step "
                        "already asks more than once; name its models with using"
                    )
                stronger = next(
                    (m for m in program.models if m.name == step.escalate), None
                )
                if stronger is None or stronger.provider is not None:
                    raise CheckError(
                        f"Step {step.name!r} cannot escalate: {step.escalate!r} "
                        "is not a model declared in the program"
                    )
                if step.escalate == step.call.model_name:
                    raise CheckError(
                        f"Step {step.name!r} cannot escalate: {step.escalate!r} "
                        "is the model it already asks"
                    )
            if step.retries == 0:
                continue
            if not isinstance(step.call, ModelCall):
                raise CheckError(
                    f"Step {step.name!r} cannot retry: only a model step "
                    "(use_model or a thinking step) can be asked again"
                )
            if not 1 <= step.retries <= 5:
                raise CheckError(
                    f"Step {step.name!r} cannot retry: the count must be "
                    f"from 1 to 5, got {step.retries}"
                )
    if any(step.verify for workflow in program.workflows for step in workflow.steps):
        _check_verify(program)
        return
    if any(step.branches for workflow in program.workflows for step in workflow.steps):
        _check_branches(program)
        return
    if any(
        step.fallback is not None
        for workflow in program.workflows
        for step in workflow.steps
    ):
        _check_fallbacks(program)
        return
    if any(function.nested for function in program.functions):
        raise CheckError(
            "Unlowered nested functions: use lower_nested_functions(program)"
        )
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
        if (
            len(stop) > 4
            or len(set(stop)) != len(stop)
            or any(
                type(value) is not str or not 1 <= len(value) <= 256 for value in stop
            )
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
            raise CheckError(f"Swarm {swarm.name!r} must require human approval")
        if type(swarm.max_rounds) is not int or not 1 <= swarm.max_rounds <= 32:
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
            if field.type_name.startswith("fn("):
                raise CheckError(
                    "Function values are not allowed in external record schemas"
                )
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
                raise CheckError(
                    f"Recursive function call involving {name!r} requires recurse limits"
                )
            if target not in reached:
                reached.add(target)
                pending.extend(function_graph.get(target, ()))

    def visit_function(name: str) -> int:
        if name in active_functions:
            cycle = active_functions[active_functions.index(name) :]
            if any(functions[item].recursion_limit is None for item in cycle):
                raise CheckError(
                    f"Recursive function call involving {name!r} requires recurse limits"
                )
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
            type(function.recursion_limit) is not int
            or not 1 <= function.recursion_limit <= 128
        ):
            raise CheckError("Recursion limit must be an integer from 1 to 128")
        _validate_type_name(program, function.output_type)
        function_bindings = _Env()
        function_bindings.returns = function.output_type
        for parameter in function.parameters:
            if parameter.name in function_bindings:
                raise CheckError(
                    f"Function {function.name!r} has duplicate parameter "
                    f"{parameter.name!r}"
                )
            _validate_type_name(program, parameter.type_name)
            function_bindings[parameter.name] = parameter.type_name
        for binding in function_body(function):
            if not isinstance(binding, LocalBinding):
                # A statement sees only the names declared above it.
                _expression_type(
                    binding,
                    function_bindings,
                    program,
                    step_name=f"function {function.name} statement",
                )
                function_bindings.prove_present(
                    _present_after(binding, function_bindings)
                )
                continue
            if binding.name in function_bindings:
                raise CheckError(
                    f"Function {function.name!r} cannot rebind {binding.name!r}"
                )
            if binding.type_name is not None:
                _validate_type_name(program, binding.type_name)
            binding_type = _expression_type(
                binding.value,
                function_bindings,
                program,
                step_name=f"function {function.name} local {binding.name}",
                expected_type=binding.type_name,
            )
            if binding.type_name is not None and not assignable(
                binding_type, binding.type_name
            ):
                raise CheckError(
                    f"Step 'function {function.name} local {binding.name}' local "
                    f"{binding.name!r} expects {binding.type_name!r}, "
                    f"got {binding_type!r}"
                )
            # An annotation is the binding's type (it may be wider than the
            # initialiser, as in `let x: Text? = "a"`); without one the
            # inferred type is used, so both behave alike downstream.
            function_bindings[binding.name] = binding.type_name or binding_type
            if binding.mutable:
                function_bindings.mutable.add(binding.name)
        actual_type = _expression_type(
            function.return_value,
            function_bindings,
            program,
            step_name=f"function {function.name}",
            expected_type=function.output_type,
        )
        if not assignable(actual_type, function.output_type):
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
        system_problem = declaration_problem(program, tool)
        if system_problem is not None:
            raise CheckError(f"Invalid system tool: {system_problem}")

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
                f"Agent {agent.name!r} deadline_seconds must be between 0 and 86400"
            )
        if type(agent.max_steps) is not int or not 1 <= agent.max_steps <= 64:
            raise CheckError(f"Agent {agent.name!r} max_steps must be between 1 and 64")
        if (
            type(agent.max_tool_calls) is not int
            or not 1 <= agent.max_tool_calls <= 1_000_000
        ):
            raise CheckError(
                f"Agent {agent.name!r} max_tool_calls must be between 1 and 1000000"
            )
        if (
            type(agent.max_model_calls) is not int
            or not 1 <= agent.max_model_calls <= 1_000_000
        ):
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

        bindings = _Env()
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
            if step.subject is not None:
                subject_type = _expression_type(
                    step.subject, _Env(bindings), program, step_name=step.name
                )
                if subject_type not in ("Text", "Number", "Bool"):
                    raise CheckError(
                        f"Step {step.name!r} match expects Text, Number, or "
                        f"Bool, got {subject_type!r}"
                    )
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
                        raise CheckError(
                            f"{step.call.function_name!r} is not a function value"
                        )
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
