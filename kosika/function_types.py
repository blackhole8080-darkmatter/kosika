"""Canonical named-argument signatures for pure function values."""

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
                fields = tuple(Field(*part.split(":", 1)) for part in parts)
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


def referenced_types(type_name: str) -> set[str]:
    parsed = signature(type_name)
    if parsed:
        parameters, output = parsed
        return set().union(
            referenced_types(output),
            *(referenced_types(p.type_name) for p in parameters),
        )
    return {type_name.removesuffix("[]")}
