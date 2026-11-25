from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from typing import Any

from .ast import Program
from .function_types import signature


class SchemaValidationError(ValueError):
    pass


def _json_equal(left: Any, right: Any) -> bool:
    """Compare JSON values without Python's bool/int equivalence."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return left.keys() == right.keys() and all(
            _json_equal(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _json_equal(a, b) for a, b in zip(left, right)
        )
    # JSON numeric values 1 and 1.0 remain equivalent.
    return bool(left == right)


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and (not isinstance(value, float) or isfinite(value))
        )
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def validate(
    value: Any,
    schema: Mapping[str, Any],
    *,
    path: str = "$",
    root: Mapping[str, Any] | None = None,
    scopes: tuple[Mapping[str, Any], ...] = (),
) -> None:
    """Validate the JSON Schema subset needed by Kosika v0.1.

    Supported keywords: type, properties, required, additionalProperties (false
    or a schema), items, anyOf, enum, const, and $ref to local #/$defs/...
    definitions.
    """
    if root is None:
        root = schema
    if isinstance(schema.get("$defs"), Mapping):
        # A schema that carries `$defs` (a type holding itself) is where the
        # references inside it are looked up, nearest first.
        scopes = (*scopes, schema["$defs"])
    alternatives = schema.get("anyOf")
    if isinstance(alternatives, list):
        failures: list[str] = []
        for alternative in alternatives:
            try:
                validate(value, alternative, path=path, root=root, scopes=scopes)
                break
            except SchemaValidationError as exc:
                failures.append(str(exc))
        else:
            raise SchemaValidationError(
                f"{path}: matches no allowed form ({'; '.join(failures)})"
            )
    if "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
            raise SchemaValidationError(f"{path}: unsupported schema reference {ref!r}")
        name = ref.removeprefix("#/$defs/")
        definitions = next(
            (scope for scope in reversed(scopes) if name in scope),
            root.get("$defs", {}),
        )
        if name not in definitions:
            raise SchemaValidationError(f"{path}: unknown schema reference {ref!r}")
        validate(value, definitions[name], path=path, root=root, scopes=scopes)
        return

    if "const" in schema and not _json_equal(value, schema["const"]):
        raise SchemaValidationError(
            f"{path}: expected constant {schema['const']!r}, got {value!r}"
        )
    if "enum" in schema and not any(
        _json_equal(value, item) for item in schema["enum"]
    ):
        raise SchemaValidationError(f"{path}: value {value!r} is not in enum")

    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        if not any(_type_matches(value, item) for item in expected_type):
            raise SchemaValidationError(
                f"{path}: expected one of {expected_type}, got {type(value).__name__}"
            )
    elif isinstance(expected_type, str) and not _type_matches(value, expected_type):
        raise SchemaValidationError(
            f"{path}: expected {expected_type}, got {type(value).__name__}"
        )

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                raise SchemaValidationError(
                    f"{path}: missing required property {key!r}"
                )
        properties = schema.get("properties", {})
        if isinstance(properties, Mapping):
            for key, child_schema in properties.items():
                if key in value:
                    validate(
                        value[key],
                        child_schema,
                        path=f"{path}.{key}",
                        root=root,
                        scopes=scopes,
                    )
        additional = schema.get("additionalProperties")
        if additional is False:
            unknown = set(value) - set(properties)
            if unknown:
                raise SchemaValidationError(
                    f"{path}: unknown properties {sorted(unknown)!r}"
                )
        elif isinstance(additional, Mapping):
            for key, item in value.items():
                if key not in properties:
                    validate(
                        item, additional, path=f"{path}.{key}", root=root, scopes=scopes
                    )

    if isinstance(value, list) and isinstance(schema.get("items"), Mapping):
        for index, item in enumerate(value):
            validate(
                item, schema["items"], path=f"{path}[{index}]", root=root, scopes=scopes
            )


def schemas_equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return _json_equal(left, right)


def schema_for_tool_input(program: Program, input_record: str | None) -> dict[str, Any]:
    if input_record is None:
        return {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
    return schema_for_record(program, input_record)


def _schema_for_type(
    program: Program,
    type_name: str,
    stack: tuple[str, ...],
    defs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if defs is None:
        defs = {}
    if signature(type_name):
        return {
            "type": "object",
            "properties": {
                "$function": {"type": "string"},
                "$type": {"const": type_name},
                "$captures": {"type": "object"},
            },
            "required": ["$function", "$type", "$captures"],
            "additionalProperties": False,
        }
    primitive_types = {
        "Text": "string",
        "Number": "number",
        "Bool": "boolean",
        "Null": "null",
    }
    if type_name in primitive_types:
        return {"type": primitive_types[type_name]}
    for enum in program.enums:
        if enum.name == type_name:
            return {"type": "string", "enum": list(enum.values)}
    for shape in program.shapes:
        if shape.name == type_name:
            # A shape's value is one of the records that are it.
            return {
                "anyOf": [
                    _schema_for_type(program, record.name, stack, defs)
                    for record in program.records
                    if implements(program, record.name, shape.name)
                ]
            }
    if type_name in stack:
        # A record or choice that holds itself (a tree): where it recurs,
        # the schema refers back to it. Its outermost schema carries itself
        # under `$defs` for those references.
        defs[type_name] = True
        return {"$ref": f"#/$defs/{type_name}"}
    for choice in program.choices:
        if choice.name == type_name:
            # A choice is one of its alternatives. `$case` names which, and
            # the alternative's fields sit beside it.
            return _carrying_itself(type_name, defs, {
                "anyOf": [
                    {
                        "type": "object",
                        "properties": {
                            "$case": {"const": alternative.name},
                            **{
                                field.name: _schema_for_type(
                                    program, field.type_name, (*stack, type_name), defs
                                )
                                for field in alternative.fields
                            },
                        },
                        "required": [
                            "$case",
                            *(field.name for field in alternative.fields),
                        ],
                        "additionalProperties": False,
                    }
                    for alternative in choice.alternatives
                ]
            })
    if type_name.endswith("?"):
        return {
            "anyOf": [
                _schema_for_type(program, type_name[:-1], stack, defs),
                {"type": "null"},
            ]
        }
    if type_name.endswith("{}"):
        return {
            "type": "object",
            "additionalProperties": _schema_for_type(program, type_name[:-2], stack, defs),
        }
    if type_name.endswith("[]"):
        return {
            "type": "array",
            "items": _schema_for_type(program, type_name[:-2], stack, defs),
        }
    records = {record.name: record for record in program.records}
    record = records.get(type_name)
    if record is None:
        raise ValueError(f"Unsupported record field type {type_name!r}")
    properties = {
        field.name: _schema_for_type(
            program, field.type_name, (*stack, type_name), defs
        )
        for field in record.fields
    }
    return _carrying_itself(
        type_name,
        defs,
        {
            "type": "object",
            "properties": properties,
            "required": [field.name for field in record.fields],
            "additionalProperties": False,
        },
    )


def _carrying_itself(
    type_name: str, defs: dict[str, Any], schema: dict[str, Any]
) -> dict[str, Any]:
    """A type that recurred inside its own schema carries that schema
    under `$defs`, so each `#/$defs/NAME` below it has something to name.
    A type that never recurs keeps exactly the schema it always had."""

    if not defs.pop(type_name, False):
        return schema
    return {**schema, "$defs": {type_name: schema}}


def schema_for_record(program: Program, record_name: str) -> dict[str, Any]:
    if record_name not in {record.name for record in program.records}:
        raise ValueError(f"Unknown record {record_name!r}")
    return _with_defs(program, record_name)


def _with_defs(program: Program, type_name: str) -> dict[str, Any]:
    return _schema_for_type(program, type_name, (), {})


def schema_for_type(program: Program, type_name: str) -> dict[str, Any]:
    """Return the bounded JSON schema for any checked Kosika value type."""
    return _with_defs(program, type_name)


def implements(program: Program, record_name: str, shape_name: str) -> bool:
    """Whether a record is a shape: it has each of the shape's functions,
    taking the same parameters and giving the same type."""

    shape = next((item for item in program.shapes if item.name == shape_name), None)
    record = next((item for item in program.records if item.name == record_name), None)
    if shape is None or record is None:
        return False
    functions = {function.name: function for function in program.functions}
    for method in shape.methods:
        function = functions.get(f"{record.name}${method.name}")
        if method.name not in record.methods or function is None:
            return False
        if [(p.name, p.type_name) for p in function.parameters[1:]] != [
            (p.name, p.type_name) for p in method.parameters
        ] or function.output_type != method.output_type:
            return False
    return True
