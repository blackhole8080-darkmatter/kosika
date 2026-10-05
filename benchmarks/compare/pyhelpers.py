"""Shared helpers for the checked Python solutions.

A Python developer who wants the guarantees Kosika gives by default has to
write or install something like this. It is counted once, separately from the
per-task line counts. A library such as pydantic would replace `check`.
"""

import json


def check(value, shape, where="value"):
    """Raise unless `value` has exactly the given shape."""
    if isinstance(shape, dict) and set(shape) == {"*"}:
        if not isinstance(value, dict):
            raise TypeError(f"{where} must be a mapping")
        for key, item in value.items():
            check(item, shape["*"], f"{where}.{key}")
    elif isinstance(shape, dict):
        if not isinstance(value, dict) or set(value) != set(shape):
            raise TypeError(f"{where} must have exactly the fields {sorted(shape)}")
        for key, item_shape in shape.items():
            check(value[key], item_shape, f"{where}.{key}")
    elif isinstance(shape, list):
        if not isinstance(value, list):
            raise TypeError(f"{where} must be a list")
        for index, item in enumerate(value):
            check(item, shape[0], f"{where}[{index}]")
    elif shape is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{where} must be a number")
    elif not isinstance(value, shape) or (
        shape is not bool and isinstance(value, bool)
    ):
        raise TypeError(f"{where} must be {shape.__name__}")
    return value


def ask_json(complete, instruction, payload, shape):
    """Ask the model for JSON and refuse anything of the wrong shape."""
    reply = complete(instruction, json.dumps(payload, sort_keys=True))
    return check(json.loads(reply), shape, "model answer")
