"""Kosika declarations generated from a pinned component contract.

The contract is the authority on shapes. This module renders the record,
enum, and tool declarations a Kosika program needs in order to bind the
component, and fails closed on any schema the language cannot express.
The rendered text is parsed, checked, and compared back to the contract
before it is returned, so a declaration is either exact or refused.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from .components import ComponentContract


class ComponentDeclarationError(ValueError):
    """A component contract cannot be expressed as Kosika declarations."""


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_NON_NAME = re.compile(r"[^A-Za-z0-9]+")
_PRIMITIVES = {
    "string": "Text",
    "number": "Number",
    "boolean": "Bool",
    "null": "Null",
}


def _canonical(schema: Mapping[str, Any]) -> str:
    return json.dumps(schema, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _pascal(text: str) -> str:
    parts = [part for part in _NON_NAME.split(text) if part]
    if not parts:
        raise ComponentDeclarationError(f"Cannot name a declaration from {text!r}")
    return "".join(part[0].upper() + part[1:] for part in parts)


def _keys(schema: Mapping[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(schema) - allowed)
    if unknown:
        raise ComponentDeclarationError(
            f"{where}: schema keys {unknown!r} cannot be declared in Kosika"
        )


def _no_input(schema: Mapping[str, Any]) -> bool:
    """The no-argument input shape: an empty closed object without `required`.

    A tool with no `input:` produces exactly this schema, so generating an
    empty record for it would not compare equal.
    """

    return (
        isinstance(schema, Mapping)
        and schema.get("type") == "object"
        and schema.get("properties") == {}
        and "required" not in schema
        and schema.get("additionalProperties") is False
        and set(schema) == {"type", "properties", "additionalProperties"}
    )


class _Names:
    def __init__(self) -> None:
        self._schemas: dict[str, str] = {}

    def reserve(self, base: str, schema: Mapping[str, Any]) -> tuple[str, bool]:
        """Return the name for this shape and whether it is new."""
        canonical = _canonical(schema)
        name = base
        number = 2
        while name in self._schemas and self._schemas[name] != canonical:
            name = f"{base}{number}"
            number += 1
        if name in self._schemas:
            return name, False
        self._schemas[name] = canonical
        return name, True


class _Builder:
    def __init__(self) -> None:
        self.names = _Names()
        self.enums: list[tuple[str, tuple[str, ...]]] = []
        self.records: list[tuple[str, tuple[tuple[str, str], ...]]] = []

    def root_record(self, schema: Any, name: str, where: str) -> str:
        if (
            not isinstance(schema, Mapping)
            or schema.get("type") != "object"
            or not isinstance(schema.get("properties"), Mapping)
        ):
            raise ComponentDeclarationError(
                f"{where}: a Kosika tool's shape must be a closed object record"
            )
        return self.type_for(schema, name, where)

    def type_for(self, schema: Any, name: str, where: str) -> str:
        if not isinstance(schema, Mapping):
            raise ComponentDeclarationError(f"{where}: schema must be an object")
        if "anyOf" in schema:
            _keys(schema, {"anyOf"}, where)
            options = schema["anyOf"]
            if not isinstance(options, list) or len(options) != 2:
                raise ComponentDeclarationError(
                    f"{where}: only a value or null can be declared as optional"
                )
            null = [option for option in options if option == {"type": "null"}]
            others = [option for option in options if option != {"type": "null"}]
            if len(null) != 1 or len(others) != 1:
                raise ComponentDeclarationError(
                    f"{where}: only a value or null can be declared as optional"
                )
            inner = self.type_for(others[0], name, f"{where} (optional)")
            if inner.endswith("?") or inner == "Null":
                raise ComponentDeclarationError(
                    f"{where}: Kosika allows an optional type at most once, "
                    "and Null already includes null"
                )
            return inner + "?"
        kind = schema.get("type")
        if not isinstance(kind, str):
            raise ComponentDeclarationError(f"{where}: schema type must be a string")
        if kind == "string" and "enum" in schema:
            return self._enum(schema, name, where)
        if kind == "object":
            return self._object(schema, name, where)
        if kind == "array":
            _keys(schema, {"type", "items"}, where)
            items = schema.get("items")
            if not isinstance(items, Mapping):
                raise ComponentDeclarationError(
                    f"{where}: an array needs an item schema"
                )
            return self.type_for(items, name, f"{where} items") + "[]"
        if kind in _PRIMITIVES:
            _keys(schema, {"type"}, where)
            return _PRIMITIVES[kind]
        if kind == "integer":
            raise ComponentDeclarationError(
                f"{where}: the contract says 'integer', but Kosika's Number is "
                "'number'; change the Python annotation to float"
            )
        raise ComponentDeclarationError(f"{where}: unsupported schema type {kind!r}")

    def _enum(self, schema: Mapping[str, Any], name: str, where: str) -> str:
        _keys(schema, {"type", "enum"}, where)
        values = schema["enum"]
        if not isinstance(values, list) or not values:
            raise ComponentDeclarationError(
                f"{where}: an enum needs at least one value"
            )
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
                raise ComponentDeclarationError(
                    f"{where}: enum value {value!r} is not a Kosika identifier; "
                    "Kosika enum values are written as names"
                )
            if value in seen:
                raise ComponentDeclarationError(
                    f"{where}: enum value {value!r} is repeated"
                )
            seen.add(value)
        declared, new = self.names.reserve(_pascal(name), schema)
        if new:
            self.enums.append((declared, tuple(values)))
        return declared

    def _object(self, schema: Mapping[str, Any], name: str, where: str) -> str:
        properties = schema.get("properties")
        if properties is None:
            _keys(schema, {"type", "additionalProperties"}, where)
            values = schema.get("additionalProperties")
            if not isinstance(values, Mapping):
                raise ComponentDeclarationError(f"{where}: a map needs a value schema")
            return self.type_for(values, name, f"{where} values") + "{}"
        _keys(
            schema,
            {"type", "properties", "required", "additionalProperties"},
            where,
        )
        if not isinstance(properties, Mapping):
            raise ComponentDeclarationError(f"{where}: properties must be an object")
        if schema.get("additionalProperties") is not False:
            raise ComponentDeclarationError(
                f"{where}: Kosika records are closed, so additionalProperties "
                "must be false"
            )
        required = schema.get("required", [])
        if not properties and "required" not in schema:
            raise ComponentDeclarationError(
                f"{where}: a Kosika record always includes an empty required "
                'list; add "required": [] to the contract'
            )
        if (
            not isinstance(required, list)
            or not all(isinstance(item, str) for item in required)
            or set(required) != set(properties)
            or len(required) != len(properties)
        ):
            raise ComponentDeclarationError(
                f"{where}: every field must be required; Kosika records cannot "
                "leave a field out"
            )
        declared, new = self.names.reserve(_pascal(name), schema)
        if not new:
            return declared
        fields: list[tuple[str, str]] = []
        for field_name in required:
            field_schema = properties[field_name]
            if not isinstance(field_name, str) or not _IDENTIFIER.fullmatch(field_name):
                raise ComponentDeclarationError(
                    f"{where}: field name {field_name!r} is not a Kosika identifier"
                )
            field_type = self.type_for(
                field_schema,
                f"{declared}{_pascal(field_name)}",
                f"{where}.{field_name}",
            )
            fields.append((field_name, field_type))
        self.records.append((declared, tuple(fields)))
        return declared


def declaration_for_contract(contract: ComponentContract) -> str:
    """Render checked Kosika declarations for one pinned component contract."""

    if not _IDENTIFIER.fullmatch(contract.tool_name):
        raise ComponentDeclarationError(
            f"Tool name {contract.tool_name!r} is not a Kosika identifier; "
            "rename the tool before generating declarations"
        )
    builder = _Builder()
    prefix = _pascal(contract.tool_name)
    input_name = (
        None
        if _no_input(contract.input_schema)
        else builder.root_record(contract.input_schema, f"{prefix}Input", "input")
    )
    output_name = builder.root_record(
        contract.output_schema, f"{prefix}Output", "output"
    )
    lines = [
        f"// Generated from {contract.component_id} {contract.version}.",
        (
            f"// Pinned artifact: {contract.implementation.artifact} "
            f"{contract.implementation.sha256}"
        ),
        (
            "// This declares shapes only. The host still grants the manifest, "
            "its effects and capabilities."
        ),
        "",
        "export ["
        + ", ".join(
            [name for name, _ in builder.enums]
            + [name for name, _ in builder.records]
            + [contract.tool_name]
        )
        + "]",
        "",
    ]
    for enum_name, values in builder.enums:
        lines.append(f"enum {enum_name} {{")
        lines.extend(
            f"  {value}{',' if index < len(values) - 1 else ''}"
            for index, value in enumerate(values)
        )
        lines.extend(["}", ""])
    for record_name, fields in builder.records:
        if not fields:
            lines.extend([f"record {record_name} {{}}", ""])
            continue
        lines.append(f"record {record_name} {{")
        lines.extend(f"  {field}: {field_type}" for field, field_type in fields)
        lines.extend(["}", ""])
    lines.extend(
        [
            f"tool {contract.tool_name} {{",
            f"  source mcp {json.dumps('component:' + contract.component_id)}",
            f"  operation: {json.dumps(contract.operation)}",
            f"  access: {contract.access}",
            *([f"  input: {input_name}"] if input_name is not None else []),
            f"  output: {output_name}",
            "}",
        ]
    )
    text = "\n".join(lines) + "\n"
    _verify(contract, text)
    return text


def _verify(contract: ComponentContract, text: str) -> None:
    # Imported here so this module stays free of the checker's import graph.
    from .checker import CheckError, check
    from .parser import ParseError, parse
    from .schema import schema_for_tool_input, schema_for_type, schemas_equal

    try:
        program = parse(text)
        check(program)
    except (ParseError, CheckError) as exc:
        raise ComponentDeclarationError(
            f"Generated declarations do not check: {exc}"
        ) from exc
    tool = next(item for item in program.tools if item.name == contract.tool_name)
    if not schemas_equal(
        contract.input_schema, schema_for_tool_input(program, tool.input)
    ):
        raise ComponentDeclarationError(
            "Generated input shape does not match the contract"
        )
    if not schemas_equal(contract.output_schema, schema_for_type(program, tool.output)):
        raise ComponentDeclarationError(
            "Generated output shape does not match the contract"
        )
