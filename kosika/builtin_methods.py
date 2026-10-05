"""The closed set of built-in methods on Text, Number, and array values.

A method call is ``value.method(name: argument)``. Methods are pure,
deterministic, and total over their checked types, so they need no host
authority and replay like any other pure expression. The checker resolves a
call from the receiver's type and the method name; the runtime evaluates the
same table, so there is no method the checker accepts that cannot run.

Records have no methods. This table is the whole surface: user-defined methods
do not exist.
"""

from __future__ import annotations

import difflib
import math
from dataclasses import dataclass
from typing import Any, Callable, Iterable

#: The element type of the receiving array, in a signature.
ELEMENT = "$element"
#: A receiver that is any array.
ANY_ARRAY = "$array"
#: The receiver's own type, as a parameter or result.
RECEIVER = "$receiver"
#: Building an array longer than this fails the run instead.
MAX_ARRAY_LENGTH = 4096
#: Splitting cannot produce more parts than this; it fails instead.
MAX_SPLIT_PARTS = 512
_SCALARS = ("Text", "Number", "Bool", "Null")


def did_you_mean(name: str, candidates: Iterable[str]) -> str:
    """A trailing hint naming the closest known spelling, or nothing."""

    close = difflib.get_close_matches(name, sorted(set(candidates)), n=1, cutoff=0.7)
    return f" Did you mean {close[0]!r}?" if close and close[0] != name else ""


def format_number(value: float) -> str:
    """Whole numbers print without a decimal point, like `Text + Number`."""

    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


class MethodError(ValueError):
    """A method call is not part of the built-in set or is used incorrectly."""


@dataclass(frozen=True)
class Method:
    receiver: str
    name: str
    parameters: tuple[tuple[str, str], ...]
    returns: str
    apply: Callable[..., Any]


def _split(text: str, separator: str) -> list[str]:
    if separator == "":
        raise RuntimeError("split needs a non-empty separator")
    parts = text.split(separator)
    if len(parts) > MAX_SPLIT_PARTS:
        raise RuntimeError(f"split produced more than {MAX_SPLIT_PARTS} parts")
    return parts


def _whole(value: float, name: str) -> int:
    # round is half-up, so 2.5 rounds to 3 and -2.5 to -2, on every platform.
    if name == "round":
        return math.floor(value + 0.5)
    return int(getattr(math, name)(value))


def _bounded(items: list[Any]) -> list[Any]:
    if len(items) > MAX_ARRAY_LENGTH:
        raise RuntimeError(f"array would exceed {MAX_ARRAY_LENGTH} elements")
    return items


def _slice(value: Any, start: Any, end: Any) -> Any:
    for bound in (start, end):
        if isinstance(bound, bool) or bound != int(bound):
            raise RuntimeError("slice positions must be whole numbers")
    first, last = int(start), int(end)
    if not 0 <= first <= last <= len(value):
        raise RuntimeError(
            f"slice {first} to {last} is outside a value of length {len(value)}"
        )
    return value[first:last]


def _contains(items: list[Any], item: Any) -> bool:
    # Same rule as `==`: true is never equal to 1.
    return any(
        isinstance(candidate, bool) == isinstance(item, bool) and candidate == item
        for candidate in items
    )


METHODS: tuple[Method, ...] = (
    Method("Text", "length", (), "Number", lambda text: len(text)),
    Method("Text", "upper", (), "Text", lambda text: text.upper()),
    Method("Text", "lower", (), "Text", lambda text: text.lower()),
    Method("Text", "trim", (), "Text", lambda text: text.strip()),
    Method(
        "Text", "contains", (("part", "Text"),), "Bool", lambda text, part: part in text
    ),
    Method(
        "Text",
        "starts_with",
        (("prefix", "Text"),),
        "Bool",
        lambda text, prefix: text.startswith(prefix),
    ),
    Method(
        "Text",
        "ends_with",
        (("suffix", "Text"),),
        "Bool",
        lambda text, suffix: text.endswith(suffix),
    ),
    Method("Text", "split", (("separator", "Text"),), "Text[]", _split),
    Method("Number", "floor", (), "Number", lambda value: _whole(value, "floor")),
    Method("Number", "ceil", (), "Number", lambda value: _whole(value, "ceil")),
    Method("Number", "round", (), "Number", lambda value: _whole(value, "round")),
    Method(ANY_ARRAY, "length", (), "Number", lambda items: len(items)),
    Method(
        ANY_ARRAY,
        "append",
        (("item", ELEMENT),),
        RECEIVER,
        lambda items, item: _bounded([*items, item]),
    ),
    Method(
        ANY_ARRAY,
        "concat",
        (("other", RECEIVER),),
        RECEIVER,
        lambda items, other: _bounded([*items, *other]),
    ),
    Method(ANY_ARRAY, "reverse", (), RECEIVER, lambda items: list(reversed(items))),
    Method(
        ANY_ARRAY, "slice", (("start", "Number"), ("end", "Number")), RECEIVER, _slice
    ),
    Method("Text", "slice", (("start", "Number"), ("end", "Number")), "Text", _slice),
    Method("Number", "to_text", (), "Text", lambda value: format_number(value)),
    Method(ANY_ARRAY, "contains", (("item", ELEMENT),), "Bool", _contains),
    Method(
        "Text[]",
        "join",
        (("separator", "Text"),),
        "Text",
        lambda items, separator: separator.join(items),
    ),
)


def _candidates(receiver_type: str) -> list[Method]:
    found = [method for method in METHODS if method.receiver == receiver_type]
    if receiver_type.endswith("[]"):
        found += [method for method in METHODS if method.receiver == ANY_ARRAY]
    return found


def resolve(receiver_type: str, name: str) -> Method:
    """Find the method for a receiver type, or explain what exists instead."""

    candidates = _candidates(receiver_type)
    for method in candidates:
        if method.name == name:
            if (
                method.name == "contains"
                and method.receiver == ANY_ARRAY
                and receiver_type[:-2] not in _SCALARS
            ):
                raise MethodError(
                    "contains works on arrays of Text, Number, or Bool, "
                    f"not {receiver_type!r}"
                )
            return method
    available = ", ".join(sorted({method.name for method in candidates}))
    if not available:
        raise MethodError(
            f"{receiver_type!r} values have no methods; methods exist on Text, "
            "Number, and arrays"
        )
    hint = did_you_mean(name, (method.name for method in candidates))
    raise MethodError(
        f"{receiver_type!r} has no method {name!r}; available: {available}.{hint}"
    )


def _concrete(type_name: str, receiver_type: str) -> str:
    if type_name == RECEIVER:
        return receiver_type
    if type_name == ELEMENT:
        return receiver_type[:-2] if receiver_type.endswith("[]") else receiver_type
    return type_name


def parameter_types(method: Method, receiver_type: str) -> tuple[tuple[str, str], ...]:
    """The method's parameters with receiver and element types filled in."""

    return tuple(
        (name, _concrete(type_name, receiver_type))
        for name, type_name in method.parameters
    )


def return_type(method: Method, receiver_type: str) -> str:
    return _concrete(method.returns, receiver_type)


def call(method: Method, receiver: Any, arguments: dict[str, Any]) -> Any:
    return method.apply(receiver, *(arguments[name] for name, _ in method.parameters))
