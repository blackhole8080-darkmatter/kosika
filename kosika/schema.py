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
) -> None:
    """Validate the JSON Schema subset needed by Kosika v0.1.

    Supported keywords: type, properties, required, additionalProperties, items,
    enum, const, and $ref to local #/$defs/... definitions.
    """
    if root is None:
        root = schema
    if "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
            raise SchemaValidationError(f"{path}: unsupported schema reference {ref!r}")
        definitions = root.get("$defs", {})
        name = ref.removeprefix("#/$defs/")
        if name not in definitions:
            raise SchemaValidationError(f"{path}: unknown schema reference {ref!r}")
        validate(value, definitions[name], path=path, root=root)
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
                    validate(value[key], child_schema, path=f"{path}.{key}", root=root)
        if schema.get("additionalProperties") is False:
            unknown = set(value) - set(properties)
            if unknown:
                raise SchemaValidationError(
                    f"{path}: unknown properties {sorted(unknown)!r}"
                )

    if isinstance(value, list) and isinstance(schema.get("items"), Mapping):
        for index, item in enumerate(value):
            validate(item, schema["items"], path=f"{path}[{index}]", root=root)


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
    program: Program, type_name: str, stack: tuple[str, ...]
) -> dict[str, Any]:
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
    if type_name.endswith("[]"):
        return {
            "type": "array",
            "items": _schema_for_type(program, type_name[:-2], stack),
        }
    if type_name in stack:
        raise ValueError(
            f"Recursive record schema {type_name!r} is not supported in v0.1"
        )
    records = {record.name: record for record in program.records}
    record = records.get(type_name)
    if record is None:
        raise ValueError(f"Unsupported record field type {type_name!r}")
    properties = {
        field.name: _schema_for_type(program, field.type_name, (*stack, type_name))
        for field in record.fields
    }
    return {
        "type": "object",
        "properties": properties,
        "required": [field.name for field in record.fields],
        "additionalProperties": False,
    }


def schema_for_record(program: Program, record_name: str) -> dict[str, Any]:
    if record_name not in {record.name for record in program.records}:
        raise ValueError(f"Unknown record {record_name!r}")
    return _schema_for_type(program, record_name, ())


def schema_for_type(program: Program, type_name: str) -> dict[str, Any]:
    """Return the bounded JSON schema for any checked Kosika value type."""
    return _schema_for_type(program, type_name, ())
