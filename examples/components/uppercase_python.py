"""A Python tool behind the kosika.component/v1 boundary.

python uppercase_python.py --describe
kosika component describe uppercase_python.py --output uppercase_python.component.json
"""

from __future__ import annotations

from dataclasses import dataclass

from kosika.component_sdk import ErrorSpec, ToolFailure, component, serve


@dataclass(frozen=True)
class TransformOutput:
    text: str


@component(
    id="example.uppercase-python",
    version="1.0.0",
    access="analyze",
    effects=("cpu:compute",),
    capabilities=("text:transform",),
    errors=(ErrorSpec("input:invalid"),),
    timeout_ms=5_000,
)
def uppercase_python(text: str) -> TransformOutput:
    if not text.strip():
        raise ToolFailure("input:invalid", "Text must not be blank")
    return TransformOutput(text=text.upper())


if __name__ == "__main__":
    serve()
