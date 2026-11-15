"""Author Python tools for the ``kosika.component/v1`` contract.

The module uses only the standard library, so a tool can vendor this one file
into an environment where Kosika itself is not installed. A tool declares its
record shape with ordinary Python annotations, then serves one JSON request on
standard input::

    from dataclasses import dataclass
    from kosika.component_sdk import ErrorSpec, ToolFailure, component, serve

    @dataclass(frozen=True)
    class Output:
        text: str

    @component(
        id="example.uppercase",
        version="1.0.0",
        access="analyze",
        effects=("cpu:compute",),
        errors=(ErrorSpec("input:invalid"),),
    )
    def uppercase(text: str) -> Output:
        if not text.strip():
            raise ToolFailure("input:invalid", "Text must not be blank")
        return Output(text.upper())

    if __name__ == "__main__":
        serve()

    python uppercase.py --describe          # the pinned contract, as JSON
    kosika component describe uppercase.py  # the same, validated by the host
"""

from __future__ import annotations

import argparse
import collections.abc
import dataclasses
import enum
import hashlib
import inspect
import json
import math
import re
import sys
import types
import typing
from dataclasses import dataclass
from pathlib import Path

COMPONENT_SCHEMA = "kosika.component/v1"
COMPONENT_CALL_SCHEMA = "kosika.component-call/v1"
COMPONENT_RESULT_SCHEMA = "kosika.component-result/v1"
COMPONENT_PROTOCOL = "json-stdio/v1"
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_MESSAGE_BYTES = 1024 * 1024
MAX_TIMEOUT_MS = 600_000

_ACCESS_LEVELS = frozenset({"observe", "analyze", "prepare", "execute"})
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_AUTHORITY_RE = re.compile(r"^[a-z][a-z0-9_.-]*:[a-z][a-z0-9_.-]*$")

_PRIMITIVES: dict[type[typing.Any], str] = {
    str: "string",
    bool: "boolean",
    int: "number",
    float: "number",
}
_MAPPING_ORIGINS = (
    dict,
    collections.abc.Mapping,
    collections.abc.MutableMapping,
)
_SEQUENCE_ORIGINS = (list, collections.abc.Sequence)


class ComponentSchemaError(ValueError):
    """A Python declaration cannot be expressed as a Kosika record."""


class _SchemaError(ValueError):
    pass


class ToolFailure(Exception):
    """Raised inside a tool to return one of its declared error codes."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ErrorSpec:
    code: str
    retryable: bool = False


@dataclass(frozen=True)
class _Registered:
    component_id: str
    version: str
    tool_name: str
    operation: str
    access: str
    effects: tuple[str, ...]
    capabilities: tuple[str, ...]
    errors: tuple[ErrorSpec, ...]
    timeout_ms: int
    function: collections.abc.Callable[..., typing.Any]
    parameters: tuple[inspect.Parameter, ...]
    parameter_hints: dict[str, typing.Any]
    return_hint: typing.Any
    input_schema: dict[str, typing.Any]
    output_schema: dict[str, typing.Any]


_COMPONENTS: dict[str, _Registered] = {}


def _reject_constant(value: str) -> typing.Any:
    raise ValueError(f"Non-finite JSON number {value!r}")


def _unique_pairs(items: list[tuple[str, typing.Any]]) -> dict[str, typing.Any]:
    result: dict[str, typing.Any] = {}
    for name, value in items:
        if name in result:
            raise ValueError(f"Duplicate JSON key {name!r}")
        result[name] = value
    return result


def _strict_loads(text: str) -> typing.Any:
    result = json.loads(
        text,
        object_pairs_hook=_unique_pairs,
        parse_constant=_reject_constant,
    )
    json.dumps(result, allow_nan=False)
    return result


def _type_matches(value: typing.Any, expected: str) -> bool:
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
            and (not isinstance(value, float) or math.isfinite(value))
        )
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def _validate(
    value: typing.Any, schema: dict[str, typing.Any], path: str = "$"
) -> None:
    alternatives = schema.get("anyOf")
    if isinstance(alternatives, list):
        for alternative in alternatives:
            try:
                _validate(value, alternative, path)
                return
            except _SchemaError:
                pass
        raise _SchemaError(f"{path}: matches no allowed form")
    if "enum" in schema and not any(value == item for item in schema["enum"]):
        raise _SchemaError(f"{path}: value {value!r} is not in enum")
    expected = schema.get("type")
    if isinstance(expected, str) and not _type_matches(value, expected):
        raise _SchemaError(f"{path}: expected {expected}, got {type(value).__name__}")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                raise _SchemaError(f"{path}: missing required property {key!r}")
        for key, child in properties.items():
            if key in value:
                _validate(value[key], child, f"{path}.{key}")
        additional = schema.get("additionalProperties")
        if additional is False:
            unknown = set(value) - set(properties)
            if unknown:
                raise _SchemaError(f"{path}: unknown properties {sorted(unknown)!r}")
        elif isinstance(additional, dict):
            for key, item in value.items():
                if key not in properties:
                    _validate(item, additional, f"{path}.{key}")
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            _validate(item, schema["items"], f"{path}[{index}]")


def _union_args(annotation: typing.Any) -> tuple[typing.Any, ...] | None:
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        return typing.get_args(annotation)
    return None


def _optional_inner(annotation: typing.Any) -> typing.Any | None:
    members = _union_args(annotation)
    if members is None:
        return None
    remaining = tuple(item for item in members if item is not type(None))
    if len(remaining) == 1 and len(remaining) != len(members):
        return remaining[0]
    return None


def _hints(owner: typing.Any) -> dict[str, typing.Any]:
    try:
        return typing.get_type_hints(owner)
    except (NameError, TypeError) as exc:
        name = getattr(owner, "__qualname__", repr(owner))
        raise ComponentSchemaError(
            f"Cannot resolve the type annotations of {name}: {exc}"
        ) from exc


def _object_schema(
    properties: dict[str, typing.Any],
    required: list[str],
    *,
    include_required: bool = True,
) -> dict[str, typing.Any]:
    schema: dict[str, typing.Any] = {
        "type": "object",
        "properties": properties,
    }
    if include_required:
        schema["required"] = required
    schema["additionalProperties"] = False
    return schema


def _schema_for_record(
    record: type[typing.Any], stack: tuple[type[typing.Any], ...]
) -> dict[str, typing.Any]:
    if record in stack:
        raise ComponentSchemaError(
            f"Recursive record {record.__name__!r} is not supported in Kosika v0.1"
        )
    if dataclasses.is_dataclass(record):
        hints = _hints(record)
        fields = [field for field in dataclasses.fields(record) if field.init]
        properties = {
            field.name: _schema_for_annotation(
                hints.get(field.name, field.type), (*stack, record)
            )
            for field in fields
        }
        return _object_schema(properties, [field.name for field in fields])
    hints = _hints(record)
    optional: frozenset[str] = getattr(record, "__optional_keys__", frozenset())
    if optional:
        raise ComponentSchemaError(
            f"TypedDict {record.__name__!r} has optional keys "
            f"{sorted(optional)!r}; Kosika records require every field"
        )
    properties = {
        name: _schema_for_annotation(annotation, (*stack, record))
        for name, annotation in hints.items()
    }
    return _object_schema(properties, list(hints))


def _schema_for_annotation(
    annotation: typing.Any, stack: tuple[type[typing.Any], ...] = ()
) -> dict[str, typing.Any]:
    if annotation is type(None):
        return {"type": "null"}
    inner = _optional_inner(annotation)
    if inner is not None:
        return {
            "anyOf": [
                _schema_for_annotation(inner, stack),
                {"type": "null"},
            ]
        }
    members = _union_args(annotation)
    if members is not None:
        raise ComponentSchemaError(
            "Kosika has no general union type; use an optional field or a record"
        )
    origin = typing.get_origin(annotation)
    if origin is typing.Literal:
        literal_values = typing.get_args(annotation)
        if not literal_values or not all(
            isinstance(value, str) for value in literal_values
        ):
            raise ComponentSchemaError("Literal values must be non-empty strings")
        return {"type": "string", "enum": list(literal_values)}
    if origin in _SEQUENCE_ORIGINS:
        arguments = typing.get_args(annotation)
        if len(arguments) != 1:
            raise ComponentSchemaError("A list annotation needs one item type")
        return {
            "type": "array",
            "items": _schema_for_annotation(arguments[0], stack),
        }
    if origin in _MAPPING_ORIGINS:
        arguments = typing.get_args(annotation)
        if len(arguments) != 2 or arguments[0] is not str:
            raise ComponentSchemaError(
                "A map annotation must be dict[str, T] or Mapping[str, T]"
            )
        return {
            "type": "object",
            "additionalProperties": _schema_for_annotation(arguments[1], stack),
        }
    if origin is not None:
        raise ComponentSchemaError(f"Unsupported annotation {annotation!r}")
    if isinstance(annotation, type):
        primitive = _PRIMITIVES.get(annotation)
        if primitive is not None:
            return {"type": primitive}
        if issubclass(annotation, enum.Enum):
            enum_values = [member.value for member in annotation]
            if not enum_values or not all(
                isinstance(value, str) for value in enum_values
            ):
                raise ComponentSchemaError(
                    f"Enum {annotation.__name__!r} needs non-empty string values"
                )
            return {"type": "string", "enum": enum_values}
        if dataclasses.is_dataclass(annotation) or typing.is_typeddict(annotation):
            return _schema_for_record(annotation, stack)
    raise ComponentSchemaError(
        f"Annotation {annotation!r} has no bounded Kosika record form; "
        "use a primitive, dataclass, TypedDict, list, dict, enum, optional, "
        "or Literal of strings"
    )


def _authority_list(values: typing.Iterable[str], label: str) -> tuple[str, ...]:
    items = tuple(values)
    for item in items:
        if not isinstance(item, str) or not _AUTHORITY_RE.fullmatch(item):
            raise ComponentSchemaError(
                f"{label} must use namespace:action names, got {item!r}"
            )
    if len(set(items)) != len(items):
        raise ComponentSchemaError(f"{label} must not contain duplicates")
    return tuple(sorted(items))


def _error_list(errors: typing.Iterable[ErrorSpec]) -> tuple[ErrorSpec, ...]:
    items = tuple(errors)
    seen: set[str] = set()
    for error in items:
        if not isinstance(error, ErrorSpec):
            raise ComponentSchemaError("errors must be ErrorSpec instances")
        if not _AUTHORITY_RE.fullmatch(error.code):
            raise ComponentSchemaError(
                f"Error code {error.code!r} must use a namespace:action name"
            )
        if not isinstance(error.retryable, bool):
            raise ComponentSchemaError(f"{error.code!r}.retryable must be boolean")
        if error.code in seen:
            raise ComponentSchemaError(f"errors repeat {error.code!r}")
        seen.add(error.code)
    return tuple(sorted(items, key=lambda error: error.code))


def _check_function(
    function: collections.abc.Callable[..., typing.Any],
) -> tuple[
    tuple[inspect.Parameter, ...],
    dict[str, typing.Any],
    typing.Any,
]:
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError) as exc:
        raise ComponentSchemaError(f"Cannot inspect {function!r}: {exc}") from exc
    parameters = tuple(signature.parameters.values())
    for parameter in parameters:
        if parameter.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
            inspect.Parameter.POSITIONAL_ONLY,
        ):
            raise ComponentSchemaError(
                f"Parameter {parameter.name!r} must be callable by keyword"
            )
    hints = _hints(function)
    for parameter in parameters:
        if parameter.name not in hints:
            raise ComponentSchemaError(
                f"Parameter {parameter.name!r} needs a type annotation"
            )
    if "return" not in hints:
        raise ComponentSchemaError("The tool function needs a return annotation")
    return parameters, hints, hints["return"]


def component(
    *,
    id: str,
    version: str,
    access: str,
    name: str | None = None,
    operation: str | None = None,
    effects: typing.Iterable[str] = (),
    capabilities: typing.Iterable[str] = (),
    errors: typing.Iterable[ErrorSpec] = (),
    timeout_ms: int = 30_000,
) -> collections.abc.Callable[
    [collections.abc.Callable[..., typing.Any]],
    collections.abc.Callable[..., typing.Any],
]:
    """Register one function as a component tool; returns the function unchanged."""

    def decorate(
        function: collections.abc.Callable[..., typing.Any],
    ) -> collections.abc.Callable[..., typing.Any]:
        tool_name = name or function.__name__
        tool_operation = operation or tool_name
        if not isinstance(id, str) or not _NAME_RE.fullmatch(id):
            raise ComponentSchemaError(f"Invalid component id {id!r}")
        if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
            raise ComponentSchemaError(f"Invalid semantic version {version!r}")
        if access not in _ACCESS_LEVELS:
            raise ComponentSchemaError(
                f"access must be one of {sorted(_ACCESS_LEVELS)!r}"
            )
        if not isinstance(tool_name, str) or not _NAME_RE.fullmatch(tool_name):
            raise ComponentSchemaError(f"Invalid tool name {tool_name!r}")
        if not isinstance(tool_operation, str) or not _NAME_RE.fullmatch(
            tool_operation
        ):
            raise ComponentSchemaError(f"Invalid operation {tool_operation!r}")
        if (
            isinstance(timeout_ms, bool)
            or not isinstance(timeout_ms, int)
            or not 1 <= timeout_ms <= MAX_TIMEOUT_MS
        ):
            raise ComponentSchemaError(
                f"timeout_ms must be an integer from 1 to {MAX_TIMEOUT_MS}"
            )
        parameters, hints, return_hint = _check_function(function)
        input_schema = _object_schema(
            {
                parameter.name: _schema_for_annotation(hints[parameter.name])
                for parameter in parameters
            },
            [parameter.name for parameter in parameters],
            include_required=bool(parameters),
        )
        if return_hint is type(None):
            output_schema = _object_schema({}, [])
        else:
            output_schema = _schema_for_annotation(return_hint)
            if output_schema.get("type") != "object":
                raise ComponentSchemaError(
                    "The return annotation must be an object record, "
                    "dict[str, T], or None"
                )
        registered = _Registered(
            component_id=id,
            version=version,
            tool_name=tool_name,
            operation=tool_operation,
            access=access,
            effects=_authority_list(effects, "effects"),
            capabilities=_authority_list(capabilities, "capabilities"),
            errors=_error_list(errors),
            timeout_ms=timeout_ms,
            function=function,
            parameters=parameters,
            parameter_hints=hints,
            return_hint=return_hint,
            input_schema=input_schema,
            output_schema=output_schema,
        )
        for existing in _COMPONENTS.values():
            if existing.operation == tool_operation:
                raise ComponentSchemaError(
                    f"Another component already uses this operation: {tool_operation!r}"
                )
            if existing.tool_name == tool_name:
                raise ComponentSchemaError(
                    f"Another component already uses this tool name: {tool_name!r}"
                )
            if existing.component_id == id:
                raise ComponentSchemaError(
                    f"Another component already uses this id: {id!r}"
                )
        _COMPONENTS[tool_operation] = registered
        return function

    return decorate


def _select(tool: str | None) -> _Registered:
    if tool is not None:
        for registered in _COMPONENTS.values():
            if registered.tool_name == tool or registered.operation == tool:
                return registered
        available = sorted(item.tool_name for item in _COMPONENTS.values())
        raise ComponentSchemaError(
            f"No component tool named {tool!r}; this artifact defines {available!r}"
        )
    if not _COMPONENTS:
        raise ComponentSchemaError("This artifact defines no component tools")
    if len(_COMPONENTS) > 1:
        available = sorted(item.tool_name for item in _COMPONENTS.values())
        raise ComponentSchemaError(
            f"This artifact defines several tools {available!r}; pass --tool"
        )
    return next(iter(_COMPONENTS.values()))


def _artifact_name(path: Path) -> str:
    name = path.name
    if not name or name in {".", ".."} or "\\" in name or ":" in name:
        raise ComponentSchemaError(
            "The artifact must have a relative file name without ':' or '\\'"
        )
    return name


def component_manifest(
    artifact: str | Path, *, tool: str | None = None
) -> dict[str, typing.Any]:
    """Build the pinned contract for one tool in this artifact."""

    path = Path(artifact).absolute()
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise OSError(f"Could not inspect component artifact {path}: {exc}") from exc
    if size > MAX_ARTIFACT_BYTES:
        raise ComponentSchemaError(
            f"Component artifact exceeds {MAX_ARTIFACT_BYTES} bytes"
        )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    registered = _select(tool)
    return {
        "schema": COMPONENT_SCHEMA,
        "id": registered.component_id,
        "version": registered.version,
        "tool": {
            "name": registered.tool_name,
            "operation": registered.operation,
        },
        "implementation": {
            "artifact": _artifact_name(path),
            "sha256": digest,
            "protocol": COMPONENT_PROTOCOL,
        },
        "inputSchema": registered.input_schema,
        "outputSchema": registered.output_schema,
        "access": registered.access,
        "effects": list(registered.effects),
        "capabilities": list(registered.capabilities),
        "errors": [
            {"code": error.code, "retryable": error.retryable}
            for error in registered.errors
        ],
        "cancellation": {
            "mode": "terminate",
            "timeoutMs": registered.timeout_ms,
        },
    }


def _from_json(value: typing.Any, annotation: typing.Any) -> typing.Any:
    inner = _optional_inner(annotation)
    if inner is not None:
        if value is None:
            return None
        return _from_json(value, inner)
    origin = typing.get_origin(annotation)
    if origin in _SEQUENCE_ORIGINS:
        item_type = typing.get_args(annotation)[0]
        return [_from_json(item, item_type) for item in value]
    if origin in _MAPPING_ORIGINS:
        item_type = typing.get_args(annotation)[1]
        return {key: _from_json(item, item_type) for key, item in value.items()}
    if origin is typing.Literal or origin is not None:
        return value
    if isinstance(annotation, type):
        if issubclass(annotation, enum.Enum):
            return annotation(value)
        if dataclasses.is_dataclass(annotation):
            hints = _hints(annotation)
            return annotation(
                **{
                    field.name: _from_json(
                        value[field.name], hints.get(field.name, field.type)
                    )
                    for field in dataclasses.fields(annotation)
                    if field.init
                }
            )
        if typing.is_typeddict(annotation):
            hints = _hints(annotation)
            return {key: _from_json(item, hints[key]) for key, item in value.items()}
    return value


def _to_json(value: typing.Any, annotation: typing.Any) -> typing.Any:
    inner = _optional_inner(annotation)
    if inner is not None:
        if value is None:
            return None
        return _to_json(value, inner)
    origin = typing.get_origin(annotation)
    if origin in _SEQUENCE_ORIGINS:
        item_type = typing.get_args(annotation)[0]
        return [_to_json(item, item_type) for item in value]
    if origin in _MAPPING_ORIGINS:
        item_type = typing.get_args(annotation)[1]
        return {key: _to_json(item, item_type) for key, item in value.items()}
    if origin is typing.Literal or origin is not None:
        return value
    if isinstance(annotation, type):
        if issubclass(annotation, enum.Enum):
            return value.value
        if dataclasses.is_dataclass(annotation):
            hints = _hints(annotation)
            return {
                field.name: _to_json(
                    getattr(value, field.name), hints.get(field.name, field.type)
                )
                for field in dataclasses.fields(annotation)
                if field.init
            }
        if typing.is_typeddict(annotation):
            hints = _hints(annotation)
            return {key: _to_json(item, hints[key]) for key, item in value.items()}
    return value


def _write(payload: dict[str, typing.Any]) -> None:
    sys.stdout.write(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )
    sys.stdout.flush()


def _read_request() -> dict[str, typing.Any]:
    raw = sys.stdin.buffer.read(MAX_MESSAGE_BYTES + 1)
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ValueError(f"Component request exceeds {MAX_MESSAGE_BYTES} bytes")
    request = _strict_loads(raw.decode("utf-8"))
    expected = {"schema", "component", "version", "operation", "input"}
    if not isinstance(request, dict) or set(request) != expected:
        raise ValueError("Component request has an invalid shape")
    if request["schema"] != COMPONENT_CALL_SCHEMA:
        raise ValueError("Component request has an unsupported schema")
    for key in ("component", "version", "operation"):
        if not isinstance(request[key], str):
            raise ValueError(f"Component request {key} must be a string")
    if not isinstance(request["input"], dict):
        raise ValueError("Component request input must be an object")
    return request


def _serve() -> int:
    try:
        request = _read_request()
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"Component request rejected: {exc}", file=sys.stderr)
        return 1
    registered = _COMPONENTS.get(request["operation"])
    if registered is None:
        print(
            f"Component request names unknown operation {request['operation']!r}",
            file=sys.stderr,
        )
        return 1
    if (
        request["component"] != registered.component_id
        or request["version"] != registered.version
    ):
        print("Component request identity does not match this tool", file=sys.stderr)
        return 1
    try:
        _validate(request["input"], registered.input_schema)
    except _SchemaError as exc:
        print(f"Component input rejected: {exc}", file=sys.stderr)
        return 1
    arguments = {
        parameter.name: _from_json(
            request["input"][parameter.name],
            registered.parameter_hints[parameter.name],
        )
        for parameter in registered.parameters
    }
    try:
        result = registered.function(**arguments)
    except ToolFailure as exc:
        declared = next(
            (error for error in registered.errors if error.code == exc.code),
            None,
        )
        if declared is None:
            print(
                f"Tool failed with undeclared error code {exc.code!r}",
                file=sys.stderr,
            )
            return 1
        if not isinstance(exc.message, str) or not exc.message:
            print("Tool failure message must be a non-empty string", file=sys.stderr)
            return 1
        _write(
            {
                "schema": COMPONENT_RESULT_SCHEMA,
                "ok": False,
                "error": {"code": declared.code, "message": exc.message},
            }
        )
        return 0
    output = (
        {}
        if registered.return_hint is type(None)
        else _to_json(result, registered.return_hint)
    )
    try:
        _validate(output, registered.output_schema)
    except _SchemaError as exc:
        print(f"Component output does not match its contract: {exc}", file=sys.stderr)
        return 1
    _write(
        {
            "schema": COMPONENT_RESULT_SCHEMA,
            "ok": True,
            "output": output,
        }
    )
    return 0


def _describe(artifact: Path, tool: str | None, output: Path | None) -> int:
    try:
        manifest = component_manifest(artifact, tool=tool)
    except (ComponentSchemaError, OSError) as exc:
        print(f"Component describe failed: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(manifest, indent=2) + "\n"
    if output is not None:
        try:
            output.write_text(text, encoding="utf-8")
        except OSError as exc:
            print(f"Component describe failed: {exc}", file=sys.stderr)
            return 2
        print(f"Wrote component contract: {output}")
    else:
        sys.stdout.write(text)
    return 0


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=Path(sys.argv[0]).name,
        description="Serve one kosika.component/v1 call on stdin, or --describe.",
    )
    parser.add_argument(
        "--describe",
        action="store_true",
        help="Print the pinned component contract instead of serving a call.",
    )
    parser.add_argument("--tool", help="Tool name when several are defined.")
    parser.add_argument("--output", type=Path, help="Write --describe to this file.")
    return parser


def _main(argv: typing.Sequence[str] | None = None) -> int:
    parser = _argument_parser()
    args = parser.parse_args(argv)
    if not args.describe:
        if args.tool is not None or args.output is not None:
            parser.error("--tool and --output require --describe")
        return _serve()
    return _describe(
        Path(sys.argv[0]).absolute(),
        args.tool,
        args.output,
    )


def serve(argv: typing.Sequence[str] | None = None) -> typing.NoReturn:
    """Serve a call or describe the artifact; exits with the command's status."""

    raise SystemExit(_main(argv))
