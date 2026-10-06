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
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable

#: The element type of the receiving array, in a signature.
ELEMENT = "$element"
#: A receiver that is any array.
ANY_ARRAY = "$array"
#: The receiver's own type, as a parameter or result.
RECEIVER = "$receiver"
#: A receiver that is any optional `T?`; ELEMENT is then `T`.
ANY_OPTIONAL = "$optional"
#: A receiver that is any map `T{}`; ELEMENT is then `T`.
ANY_MAP = "$map"
#: A parameter that names a field of the array's record element, written as
#: a text literal and checked against the record: `orders.sort_by(field:
#: "total")`. The part after the colon lists the field types allowed.
FIELD_SORTABLE = "$field:Number|Text"
FIELD_NUMBER = "$field:Number"
FIELD_TEXT = "$field:Text"
#: Building a map with more entries than this fails the run instead.
MAX_MAP_ENTRIES = 100_000
_NUMBER_TEXT = re.compile(r"-?[0-9]+(\.[0-9]+)?\Z")
#: Building an array longer than this fails the run instead.
MAX_ARRAY_LENGTH = 1_000_000
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


def _to_number(text: str) -> int | float | None:
    """Read a decimal number written as Kosika writes them, else nothing."""

    trimmed = text.strip()
    if not _NUMBER_TEXT.match(trimmed):
        return None
    return float(trimmed) if "." in trimmed else int(trimmed)


def _require(value: Any) -> Any:
    if value is None:
        raise RuntimeError("value() was called on an absent value")
    return value


#: A number range longer than this fails the run instead.
MAX_RANGE_LENGTH = 1_000_000


def _range(start: Any, end: Any, inclusive: bool) -> list[int]:
    for bound in (start, end):
        if isinstance(bound, bool) or bound != int(bound):
            raise RuntimeError("a range needs whole numbers")
    first, last = int(start), int(end) + (1 if inclusive else 0)
    if last - first > MAX_RANGE_LENGTH:
        raise RuntimeError(f"range would exceed {MAX_RANGE_LENGTH} numbers")
    return list(range(first, last))


def _array_set(items: list[Any], key: Any, value: Any) -> list[Any]:
    if isinstance(key, bool) or key != int(key) or not 0 <= key < len(items):
        raise RuntimeError(
            f"position {format_number(key)} is outside an array of length {len(items)}"
        )
    updated = list(items)
    updated[int(key)] = value
    return updated


def _map_set(entries: dict[str, Any], key: str, value: Any) -> dict[str, Any]:
    updated = {**entries, key: value}
    if len(updated) > MAX_MAP_ENTRIES:
        raise RuntimeError(f"map would exceed {MAX_MAP_ENTRIES} entries")
    return updated


def _unique(items: list[Any]) -> list[Any]:
    kept: list[Any] = []
    for item in items:
        if not _contains(kept, item):
            kept.append(item)
    return kept


def _best(items: list[Any], field: str, largest: bool) -> Any:
    if not items:
        return None
    # max/min keep the first of equal elements, so ties are deterministic.
    pick = max if largest else min
    return pick(items, key=lambda item: item[field])


def _group_by(items: list[Any], field: str) -> dict[str, list[Any]]:
    groups: dict[str, list[Any]] = {}
    for item in items:
        groups.setdefault(item[field], []).append(item)
    return groups


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
    Method(ANY_ARRAY, "unique", (), RECEIVER, _unique),
    Method("Number[]", "sort", (), RECEIVER, lambda items: sorted(items)),
    Method("Text[]", "sort", (), RECEIVER, lambda items: sorted(items)),
    Method("Number[]", "sum", (), "Number", lambda items: sum(items)),
    Method("Number[]", "min", (), "Number?", lambda items: min(items, default=None)),
    Method("Number[]", "max", (), "Number?", lambda items: max(items, default=None)),
    Method(
        "Number[]",
        "average",
        (),
        "Number?",
        lambda items: sum(items) / len(items) if items else None,
    ),
    Method(
        ANY_ARRAY,
        "sort_by",
        (("field", FIELD_SORTABLE),),
        RECEIVER,
        lambda items, field: sorted(items, key=lambda item: item[field]),
    ),
    Method(
        ANY_ARRAY,
        "sum_by",
        (("field", FIELD_NUMBER),),
        "Number",
        lambda items, field: sum(item[field] for item in items),
    ),
    Method(
        ANY_ARRAY,
        "min_by",
        (("field", FIELD_SORTABLE),),
        ELEMENT + "?",
        lambda items, field: _best(items, field, False),
    ),
    Method(
        ANY_ARRAY,
        "max_by",
        (("field", FIELD_SORTABLE),),
        ELEMENT + "?",
        lambda items, field: _best(items, field, True),
    ),
    Method(ANY_ARRAY, "group_by", (("field", FIELD_TEXT),), RECEIVER + "{}", _group_by),
    Method(
        ANY_ARRAY, "slice", (("start", "Number"), ("end", "Number")), RECEIVER, _slice
    ),
    Method("Text", "slice", (("start", "Number"), ("end", "Number")), "Text", _slice),
    Method("Number", "to_text", (), "Text", lambda value: format_number(value)),
    Method(
        "Number",
        "to",
        (("end", "Number"),),
        "Number[]",
        lambda start, end: _range(start, end, True),
    ),
    Method(
        "Number",
        "until",
        (("end", "Number"),),
        "Number[]",
        lambda start, end: _range(start, end, False),
    ),
    Method(
        ANY_ARRAY,
        "set",
        (("key", "Number"), ("value", ELEMENT)),
        RECEIVER,
        _array_set,
    ),
    Method(
        "Number",
        "min",
        (("other", "Number"),),
        "Number",
        lambda a, b: b if b < a else a,
    ),
    Method(
        "Number",
        "max",
        (("other", "Number"),),
        "Number",
        lambda a, b: b if b > a else a,
    ),
    Method("Text", "to_number", (), "Number?", _to_number),
    Method(
        ANY_ARRAY, "first", (), ELEMENT + "?", lambda items: items[0] if items else None
    ),
    Method(
        ANY_ARRAY, "last", (), ELEMENT + "?", lambda items: items[-1] if items else None
    ),
    Method(
        ANY_OPTIONAL,
        "or",
        (("default", ELEMENT),),
        ELEMENT,
        lambda value, default: default if value is None else value,
    ),
    Method(ANY_OPTIONAL, "exists", (), "Bool", lambda value: value is not None),
    Method(ANY_OPTIONAL, "value", (), ELEMENT, _require),
    Method(
        ANY_MAP,
        "get",
        (("key", "Text"),),
        ELEMENT + "?",
        lambda entries, key: entries.get(key),
    ),
    Method(
        ANY_MAP, "has", (("key", "Text"),), "Bool", lambda entries, key: key in entries
    ),
    Method(ANY_MAP, "set", (("key", "Text"), ("value", ELEMENT)), RECEIVER, _map_set),
    Method(
        ANY_MAP,
        "remove",
        (("key", "Text"),),
        RECEIVER,
        lambda entries, key: {k: v for k, v in entries.items() if k != key},
    ),
    Method(ANY_MAP, "keys", (), "Text[]", lambda entries: list(entries)),
    Method(
        ANY_MAP, "values", (), ELEMENT + "[]", lambda entries: list(entries.values())
    ),
    Method(ANY_MAP, "length", (), "Number", lambda entries: len(entries)),
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
    # The outermost suffix decides: `Text[]?` is an optional, not an array.
    if receiver_type.endswith("?"):
        return [method for method in METHODS if method.receiver == ANY_OPTIONAL]
    if receiver_type.endswith("{}"):
        return [method for method in METHODS if method.receiver == ANY_MAP]
    found = [method for method in METHODS if method.receiver == receiver_type]
    if receiver_type.endswith("[]"):
        found += [method for method in METHODS if method.receiver == ANY_ARRAY]
    return found


def resolve_for_value(value: Any, name: str) -> Method:
    """Find the method a checked call meant, from the runtime value."""

    optional = next(
        (m for m in METHODS if m.receiver == ANY_OPTIONAL and m.name == name), None
    )
    if optional is not None:
        return optional
    if isinstance(value, dict):
        return resolve("Any{}", name)
    if isinstance(value, list):
        # The checker already matched the element type; at run time an empty
        # array has none to inspect, so array methods are found by name alone.
        for method in METHODS:
            if method.name == name and method.receiver.endswith(("[]", ANY_ARRAY)):
                return method
    return resolve(_value_type(value), name)


def _value_type(value: Any) -> str:
    if isinstance(value, str):
        return "Text"
    if isinstance(value, bool):
        return "Bool"
    if isinstance(value, (int, float)):
        return "Number"
    if isinstance(value, list):
        # An empty array has no element to inspect; every array method the
        # checker allows on it behaves the same whatever the element type.
        return f"{_value_type(value[0])}[]" if value else "Text[]"
    return "Record"


def resolve(receiver_type: str, name: str) -> Method:
    """Find the method for a receiver type, or explain what exists instead."""

    candidates = _candidates(receiver_type)
    for method in candidates:
        if method.name == name:
            if (
                method.name in ("contains", "unique")
                and method.receiver == ANY_ARRAY
                and receiver_type[:-2] not in _SCALARS
            ):
                raise MethodError(
                    f"{method.name} works on arrays of Text, Number, or Bool, "
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
    if receiver_type.endswith("?"):
        hint += (
            " The value may be absent, so reach its own methods through "
            ".or(default: ...) or .value() first."
        )
    raise MethodError(
        f"{receiver_type!r} has no method {name!r}; available: {available}.{hint}"
    )


def _concrete(type_name: str, receiver_type: str) -> str:
    if receiver_type.endswith("?"):
        element = receiver_type[:-1]
    elif receiver_type.endswith(("[]", "{}")):
        element = receiver_type[:-2]
    else:
        element = receiver_type
    concrete = type_name.replace(RECEIVER, receiver_type).replace(ELEMENT, element)
    # `first()` on an array of optionals is still optional once, not twice.
    return concrete[:-1] if concrete.endswith("??") else concrete


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
