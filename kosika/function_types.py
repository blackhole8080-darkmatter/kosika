"""Canonical named-argument signatures for pure function values."""

from contextvars import ContextVar

from .ast import Field, FunctionDecl


def function_type(parameters: tuple[Field, ...], output: str) -> str:
    return (
        "fn(" + ",".join(f"{p.name}:{p.type_name}" for p in parameters) + ")->" + output
    )


def signature(type_name: str) -> tuple[tuple[Field, ...], str] | None:
    if not type_name.startswith("fn("):
        return None
    depth = 1
    start = 3
    parts: list[str] = []
    for index in range(3, len(type_name)):
        char = type_name[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                if type_name[start:index]:
                    parts.append(type_name[start:index])
                if type_name[index + 1 : index + 3] != "->":
                    raise ValueError("Invalid function type")
                fields = tuple(
                    Field(name, kind)
                    for name, kind in (part.split(":", 1) for part in parts)
                )
                return fields, type_name[index + 3 :]
        elif char == "," and depth == 1:
            parts.append(type_name[start:index])
            start = index + 1
    raise ValueError("Invalid function type")


def public_type(function: FunctionDecl) -> str:
    return function_type(
        tuple(p for p in function.parameters if not p.name.startswith("$capture:")),
        function.output_type,
    )


#: Type suffixes: `T[]` is a list, `T{}` a map from Text to T, `T?` an
#: optional T. They may be combined, as in `Number[]?` or `Text[]{}`.
TYPE_SUFFIXES = ("[]", "{}", "?")


def base_type(type_name: str) -> str:
    """The named type left after every list, map, and optional suffix."""

    while type_name.endswith(TYPE_SUFFIXES):
        type_name = type_name[:-1] if type_name.endswith("?") else type_name[:-2]
    return type_name


def is_optional(type_name: str) -> bool:
    return type_name.endswith("?") and signature(type_name) is None


def is_map(type_name: str) -> bool:
    return type_name.endswith("{}") and signature(type_name) is None


def is_array(type_name: str) -> bool:
    return type_name.endswith("[]") and signature(type_name) is None


#: Which records each shape of the program being checked is: shape name to
#: record names. Set by the checker for the program it checks.
SHAPES: ContextVar[dict[str, frozenset[str]]] = ContextVar("SHAPES", default={})


def assignable(actual: str, expected: str) -> bool:
    """Whether a value of type ``actual`` may be stored where ``expected`` is.

    Types match exactly, with two widenings: an optional `T?` also accepts a
    plain `T` and `null`, and a shape accepts every record that is it, alone,
    in a list or optional.
    """

    if actual == expected:
        return True
    shapes = SHAPES.get()
    if actual in shapes.get(expected, ()):
        return True
    if (
        shapes
        and is_array(actual)
        and is_array(expected)
        and expected[:-2] in shapes
        and assignable(actual[:-2], expected[:-2])
    ):
        return True
    return is_optional(expected) and (
        actual in ("Null", expected[:-1]) or assignable(actual, expected[:-1])
    )


#: The type of `break` and `continue`, which leave the loop instead of
#: producing a value. It is never the type of a stored value.
NEVER = "Never"


def unify(left: str, right: str) -> str | None:
    """The one type both branches of a choice produce, or None."""

    if left == right:
        return left
    if NEVER in (left, right):
        return right if left == NEVER else left
    for first, second in ((left, right), (right, left)):
        if assignable(first, second):
            return second
        if first == "Null" and second != "Null" and signature(second) is None:
            return second if is_optional(second) else second + "?"
    return None


def referenced_types(type_name: str) -> set[str]:
    parsed = signature(type_name)
    if parsed:
        parameters, output = parsed
        return set().union(
            referenced_types(output),
            *(referenced_types(p.type_name) for p in parameters),
        )
    return {base_type(type_name)}
