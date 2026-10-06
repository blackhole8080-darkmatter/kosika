"""Typed, single-response model calls. No agent loop or tool execution."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from .model import ChatMessage, ModelProvider, ModelProviderError, ModelRequest
from .schema import validate


def strict_json(text: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in items:
            if name in result:
                raise ValueError(f"Duplicate JSON key {name!r}")
            result[name] = value
        return result

    def constant(value: str) -> Any:
        raise ValueError(f"Non-finite JSON number {value!r}")

    result = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    # Also catches overflowing exponents such as 1e999.
    json.dumps(result, allow_nan=False)
    return result


class RejectedAnswer(ModelProviderError):
    """The model replied, but its answer is not valid for the step.

    Carries the reply so that a step with `retry` can show it back to the
    model together with the reason it was rejected.
    """

    def __init__(self, message: str, content: str):
        super().__init__(message)
        self.content = content


def call_model(
    provider: ModelProvider,
    arguments: dict[str, Any],
    output_schema: dict[str, Any],
    *,
    model: str | None = None,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
    top_p: float | None = None,
    frequency_penalty: float | None = None,
    presence_penalty: float | None = None,
    stop: tuple[str, ...] = (),
    instruction: str | None = None,
    rejected: tuple[tuple[str, str], ...] = (),
):
    """Ask once. `rejected` holds earlier (answer, reason) pairs of this step."""

    task = f" Task: {instruction}" if instruction else ""
    earlier: list[ChatMessage] = []
    for answer, reason in rejected:
        earlier.append(ChatMessage(role="assistant", content=answer))
        earlier.append(
            ChatMessage(
                role="user",
                content="That answer was rejected: "
                + reason
                + " Return one corrected JSON object.",
            )
        )
    response = provider.complete(
        ModelRequest(
            messages=(
                ChatMessage(
                    role="system",
                    content="Return only one JSON object matching this JSON Schema. "
                    "Do not invoke tools or include Markdown. Schema: "
                    + json.dumps(output_schema, sort_keys=True, allow_nan=False)
                    + task,
                ),
                ChatMessage(
                    role="user",
                    content=json.dumps(arguments, sort_keys=True, allow_nan=False),
                ),
                *earlier,
            ),
            model=model,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            top_p=top_p,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            stop=stop,
        )
    )
    if response.tool_calls or response.finish_reason != "stop":
        raise ModelProviderError(
            "Model step requires a completed response without tool calls"
        )
    if not isinstance(response.content, str):
        raise ModelProviderError("Model step requires JSON text")
    try:
        result = strict_json(response.content)
        validate(result, output_schema)
    except (ValueError, TypeError) as exc:
        raise RejectedAnswer(
            f"Invalid structured model output: {exc}", response.content
        ) from exc
    if not isinstance(result, dict):
        raise RejectedAnswer("Model step output must be a record", response.content)
    return result, {
        "content": response.content,
        "finish_reason": response.finish_reason,
        "usage": asdict(response.usage) if response.usage is not None else None,
    }
