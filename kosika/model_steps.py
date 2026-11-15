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


def _accepts_null(schema: Any) -> bool:
    if not isinstance(schema, dict):
        return False
    if schema.get("type") == "null":
        return True
    return any(_accepts_null(option) for option in schema.get("anyOf", ()))


def fill_absent(value: Any, schema: Any) -> Any:
    """Read a field the model left out as null, where null is allowed.

    Models often omit a field they have nothing for instead of writing
    `null`. For an optional field the two mean the same, so the answer is
    completed rather than rejected. A field that must hold a value is never
    filled in: leaving that out is still an invalid answer.
    """

    if not isinstance(schema, dict):
        return value
    if isinstance(value, list):
        return [fill_absent(item, schema.get("items")) for item in value]
    if not isinstance(value, dict):
        return value
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        # An optional record is "this record, or null": look inside the record.
        for option in schema.get("anyOf", ()):
            if isinstance(option, dict) and isinstance(option.get("properties"), dict):
                return fill_absent(value, option)
        return value
    filled = dict(value)
    for name, field in properties.items():
        if name in filled:
            filled[name] = fill_absent(filled[name], field)
        elif _accepts_null(field):
            filled[name] = None
    return filled


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
    plain: str | None = None,
):
    """Ask once. `rejected` holds earlier (answer, reason) pairs of this step.
    `plain` names the one Text field of a model that answers in plain text."""

    if plain is not None:
        return _call_plain(
            provider,
            arguments,
            plain,
            model=model,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            top_p=top_p,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            stop=stop,
            instruction=instruction,
            rejected=rejected,
        )

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
            json_object=True,
        )
    )
    if response.tool_calls or response.finish_reason != "stop":
        raise ModelProviderError(
            "Model step requires a completed response without tool calls"
        )
    if not isinstance(response.content, str):
        raise ModelProviderError("Model step requires JSON text")
    try:
        result = fill_absent(strict_json(response.content), output_schema)
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


def unfenced(text: str) -> str:
    """A plain answer as the text it gives: the lines inside the one fenced
    block it holds, or else all of it, without the blank lines around."""

    lines = text.strip().splitlines()
    fences = [index for index, line in enumerate(lines) if line.lstrip().startswith("```")]
    if len(fences) == 2:
        return "\n".join(lines[fences[0] + 1 : fences[1]])
    return text.strip()


def _call_plain(
    provider: ModelProvider,
    arguments: dict[str, Any],
    field: str,
    *,
    model: str | None,
    temperature: float | None,
    max_output_tokens: int | None,
    top_p: float | None,
    frequency_penalty: float | None,
    presence_penalty: float | None,
    stop: tuple[str, ...],
    instruction: str | None,
    rejected: tuple[tuple[str, str], ...],
):
    """Ask for plain text, and put it in the record's one Text field."""

    task = f" Task: {instruction}" if instruction else ""
    earlier: list[ChatMessage] = []
    for answer, reason in rejected:
        earlier.append(ChatMessage(role="assistant", content=answer))
        earlier.append(
            ChatMessage(
                role="user",
                content="That answer was rejected: " + reason + " Answer again, corrected.",
            )
        )
    asked = arguments.get("text") if len(arguments) == 1 else None
    response = provider.complete(
        ModelRequest(
            messages=(
                ChatMessage(
                    role="system",
                    content="Answer with only the text asked for, in plain text: no JSON "
                    "and nothing before or after it. When the answer is a file, give "
                    "the whole file, every line, in one fenced code block." + task,
                ),
                ChatMessage(
                    role="user",
                    content=asked
                    if isinstance(asked, str)
                    else json.dumps(arguments, sort_keys=True, allow_nan=False),
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
    if not isinstance(response.content, str) or not response.content.strip():
        raise RejectedAnswer("The model gave no text", response.content or "")
    return {field: unfenced(response.content)}, {
        "content": response.content,
        "finish_reason": response.finish_reason,
        "plain": field,
        "usage": asdict(response.usage) if response.usage is not None else None,
    }
