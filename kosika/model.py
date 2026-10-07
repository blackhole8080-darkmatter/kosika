from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Protocol


class ModelProviderError(RuntimeError):
    """Raised when a model provider cannot produce a valid response."""


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str = ""
    tool_calls: tuple[ModelToolCall, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class ModelTool:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ModelToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ModelUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class ModelRequest:
    messages: tuple[ChatMessage, ...]
    tools: tuple[ModelTool, ...] = ()
    model: str | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    top_p: float | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    stop: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelResponse:
    content: str | None = None
    tool_calls: tuple[ModelToolCall, ...] = ()
    finish_reason: str = "stop"
    usage: ModelUsage | None = None
    #: Reasoning trace from a model that separates thinking from its answer.
    #: Retained rather than discarded so a caller can see that a response was
    #: spent on reasoning, which is otherwise indistinguishable from the model
    #: having produced nothing.
    reasoning: str | None = None


class ModelProvider(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse:
        """Return one model response for a complete request."""


class ScriptedModelProvider:
    """Deterministic provider for tests, fixtures, and exact replay experiments."""

    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._responses:
            raise ModelProviderError("Scripted model provider has no response left")
        return self._responses.pop(0)


Transport = Callable[[urllib.request.Request, float], bytes]

#: Wire formats this adapter can speak. ``openai`` is the OpenAI-compatible
#: ``/chat/completions`` shape most gateways expose. ``ollama`` is Ollama's native
#: ``/api/chat`` shape, which is the only one of the two that honours ``think``.
MODEL_PROTOCOLS = ("openai", "ollama")


class OpenAICompatibleProvider:
    """Small stdlib-only adapter for local OpenAI-compatible model servers.

    It supports servers such as a local gateway or an Ollama-compatible endpoint.
    The adapter performs no tool execution; it only translates model messages and
    parses a response into Kosika's provider-neutral types.

    Two wire formats are available through ``protocol``. ``openai`` targets
    ``/chat/completions`` and is the default. ``ollama`` targets Ollama's native
    ``/api/chat``, which is the only one of the two that honours ``think``:
    Ollama's OpenAI-compatible shim accepts the field and ignores it, so a
    reasoning model spends its entire output budget on the trace and returns
    empty content.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str | None = None,
        timeout: float = 60.0,
        transport: Transport | None = None,
        think: bool | None = None,
        protocol: str = "openai",
    ) -> None:
        if not base_url:
            raise ValueError("Model base URL cannot be empty")
        if not model:
            raise ValueError("Model name cannot be empty")
        if timeout <= 0:
            raise ValueError("Model timeout must be positive")
        if think is not None and type(think) is not bool:
            raise ValueError("think must be a boolean or None")
        if protocol not in MODEL_PROTOCOLS:
            raise ValueError(
                f"Model protocol must be one of {', '.join(MODEL_PROTOCOLS)}"
            )
        self.protocol = protocol
        trimmed = base_url.rstrip("/")
        if protocol == "ollama":
            # Ollama's OpenAI-compatible /v1 shim silently drops `think`, so a
            # reasoning model burns its whole budget on the trace and returns
            # empty content. The native endpoint honours the flag, which is the
            # difference between a usable patch call and an unusable one.
            self.endpoint = (
                trimmed if trimmed.endswith("/api/chat") else trimmed + "/api/chat"
            )
        else:
            self.endpoint = (
                base_url
                if trimmed.endswith("/chat/completions")
                else trimmed + "/chat/completions"
            )
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self._transport = transport
        #: Reasoning models such as qwen3 emit a separate reasoning trace that
        #: consumes the output budget before any answer is produced. Set this
        #: to False when the caller needs a direct answer, for example a
        #: machine-readable patch. Left as None the server default applies.
        self.think = think

    def complete(self, request: ModelRequest) -> ModelResponse:
        if self.protocol == "ollama":
            return self._complete_ollama(request)
        return self._complete_openai(request)

    def _complete_openai(self, request: ModelRequest) -> ModelResponse:
        payload: dict[str, Any] = {
            "model": request.model or self.model,
            "messages": [
                self._message_to_dict(message) for message in request.messages
            ],
        }
        if request.tools:
            payload["tools"] = [self._tool_to_dict(tool) for tool in request.tools]
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        for name, value, lower, upper in (
            ("top_p", request.top_p, 0, 1),
            ("frequency_penalty", request.frequency_penalty, -2, 2),
            ("presence_penalty", request.presence_penalty, -2, 2),
        ):
            if value is not None and (
                type(value) not in (int, float) or not lower <= value <= upper
            ):
                raise ModelProviderError(f"{name} must be between {lower} and {upper}")
            if value is not None:
                payload[name] = value
        if request.stop:
            if len(request.stop) > 4 or any(
                type(value) is not str or not 1 <= len(value) <= 256
                for value in request.stop
            ):
                raise ModelProviderError(
                    "stop must contain up to four non-empty strings of at most 256 characters"
                )
            payload["stop"] = list(request.stop)
        if request.max_output_tokens is not None:
            if (
                type(request.max_output_tokens) is not int
                or request.max_output_tokens < 1
            ):
                raise ModelProviderError("max_output_tokens must be a positive integer")
            payload["max_tokens"] = request.max_output_tokens
        if self.think is not None:
            payload["think"] = self.think
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request_object = urllib.request.Request(
            self.endpoint,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            raw = self._send(request_object)
            decoded = json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ModelProviderError(
                f"Model provider returned HTTP {exc.code}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ModelProviderError(f"Model provider request failed: {exc}") from exc
        except TimeoutError as exc:
            # A slow model must fail the step like any provider failure, not
            # escape as a bare socket error.
            raise ModelProviderError(
                f"Model did not answer within {self.timeout:g} seconds"
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelProviderError("Model provider returned invalid JSON") from exc
        return self._parse_response(decoded)

    def _complete_ollama(self, request: ModelRequest) -> ModelResponse:
        """Speak Ollama's native ``/api/chat`` shape.

        Ollama streams by default, so ``stream`` must be sent as false or the
        body arrives as newline-delimited JSON and fails to decode. Sampling
        parameters live under ``options`` rather than at the top level.
        """

        options: dict[str, Any] = {}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        for name, value, lower, upper in (
            ("top_p", request.top_p, 0, 1),
            ("frequency_penalty", request.frequency_penalty, -2, 2),
            ("presence_penalty", request.presence_penalty, -2, 2),
        ):
            if value is not None and (
                type(value) not in (int, float) or not lower <= value <= upper
            ):
                raise ModelProviderError(f"{name} must be between {lower} and {upper}")
            if value is not None:
                options[name] = value
        if request.stop:
            if len(request.stop) > 4 or any(
                type(value) is not str or not 1 <= len(value) <= 256
                for value in request.stop
            ):
                raise ModelProviderError(
                    "stop must contain up to four non-empty strings of at most 256 characters"
                )
            options["stop"] = list(request.stop)
        if request.max_output_tokens is not None:
            if (
                type(request.max_output_tokens) is not int
                or request.max_output_tokens < 1
            ):
                raise ModelProviderError("max_output_tokens must be a positive integer")
            options["num_predict"] = request.max_output_tokens

        payload: dict[str, Any] = {
            "model": request.model or self.model,
            "messages": [
                self._message_to_dict(message) for message in request.messages
            ],
            "stream": False,
        }
        if options:
            payload["options"] = options
        if request.tools:
            raise ModelProviderError(
                "The ollama protocol does not support tool calling; use the "
                "openai protocol for tool-enabled requests"
            )
        if self.think is not None:
            payload["think"] = self.think
        raw = self._exchange(payload)
        return self._parse_response(raw, "ollama")

    def _exchange(self, payload: dict[str, Any]) -> Any:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request_object = urllib.request.Request(
            self.endpoint,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            raw = self._send(request_object)
            return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ModelProviderError(
                f"Model provider returned HTTP {exc.code}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ModelProviderError(f"Model provider request failed: {exc}") from exc
        except TimeoutError as exc:
            # A slow model must fail the step like any provider failure, not
            # escape as a bare socket error.
            raise ModelProviderError(
                f"Model did not answer within {self.timeout:g} seconds"
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelProviderError("Model provider returned invalid JSON") from exc

    def _send(self, request: urllib.request.Request) -> bytes:
        if self._transport is not None:
            return self._transport(request, self.timeout)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.read()

    @staticmethod
    def _message_to_dict(message: ChatMessage) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(
                            call.arguments, separators=(",", ":"), sort_keys=True
                        ),
                    },
                }
                for call in message.tool_calls
            ]
        if message.tool_call_id is not None:
            payload["tool_call_id"] = message.tool_call_id
        if message.name is not None:
            payload["name"] = message.name
        return payload

    @staticmethod
    def _tool_to_dict(tool: ModelTool) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }

    @classmethod
    def _parse_ollama_response(cls, payload: Any) -> ModelResponse:
        """Read Ollama's native ``/api/chat`` reply.

        The message is top level rather than wrapped in ``choices``, the
        reasoning trace is ``thinking`` rather than ``reasoning`` or
        ``reasoning_content``, and token counts are ``eval_count`` and
        ``prompt_eval_count``. The trace is preserved rather than discarded so
        a response spent on reasoning stays distinguishable from one that
        produced nothing.
        """

        if not isinstance(payload, dict):
            raise ModelProviderError("Model response must be a JSON object")
        message = payload.get("message")
        if not isinstance(message, dict):
            raise ModelProviderError("Model response did not contain a message")
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise ModelProviderError("Model response content must be text or null")
        thinking = message.get("thinking")
        if thinking is not None and not isinstance(thinking, str):
            thinking = None
        completion_tokens = payload.get("eval_count")
        prompt_tokens = payload.get("prompt_eval_count")
        usage = None
        if isinstance(completion_tokens, int) or isinstance(prompt_tokens, int):
            prompt = prompt_tokens if isinstance(prompt_tokens, int) else 0
            completion = completion_tokens if isinstance(completion_tokens, int) else 0
            usage = ModelUsage(
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=prompt + completion,
            )
        done = payload.get("done")
        finish_reason = "stop" if done is not False else "length"
        return ModelResponse(
            content=content,
            tool_calls=(),
            finish_reason=finish_reason,
            usage=usage,
            reasoning=thinking,
        )

    @classmethod
    def _parse_response(cls, payload: Any, protocol: str = "openai") -> ModelResponse:
        if protocol == "ollama":
            return cls._parse_ollama_response(payload)
        if not isinstance(payload, dict):
            raise ModelProviderError("Model response must be a JSON object")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelProviderError("Model response did not contain choices")
        choice = choices[0]
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
            raise ModelProviderError("Model response choice did not contain a message")
        message = choice["message"]
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise ModelProviderError("Model response content must be text or null")
        raw_calls = message.get("tool_calls", [])
        if not isinstance(raw_calls, list):
            raise ModelProviderError("Model response tool_calls must be a list")
        tool_calls: list[ModelToolCall] = []
        for index, raw_call in enumerate(raw_calls):
            if not isinstance(raw_call, dict):
                raise ModelProviderError(
                    "Model response contained an invalid tool call"
                )
            function = raw_call.get("function")
            if not isinstance(function, dict) or not isinstance(
                function.get("name"), str
            ):
                raise ModelProviderError("Model tool call lacked a function name")
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError as exc:
                    raise ModelProviderError(
                        f"Model tool call {function['name']!r} had invalid JSON arguments"
                    ) from exc
            if not isinstance(arguments, dict):
                raise ModelProviderError(
                    f"Model tool call {function['name']!r} arguments must be an object"
                )
            call_id = raw_call.get("id", f"call_{index + 1}")
            if not isinstance(call_id, str):
                raise ModelProviderError("Model tool call id must be text")
            tool_calls.append(ModelToolCall(call_id, function["name"], arguments))
        usage = cls._parse_usage(payload.get("usage"))
        finish_reason = choice.get("finish_reason", "stop")
        if not isinstance(finish_reason, str):
            finish_reason = "stop"
        reasoning = message.get("reasoning")
        if reasoning is not None and not isinstance(reasoning, str):
            reasoning = None
        return ModelResponse(
            content=content,
            tool_calls=tuple(tool_calls),
            finish_reason=finish_reason,
            usage=usage,
            reasoning=reasoning,
        )

    @staticmethod
    def _parse_usage(value: Any) -> ModelUsage | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ModelProviderError("Model usage must be an object")

        def integer(name: str) -> int | None:
            item = value.get(name)
            if item is None:
                return None
            if not isinstance(item, int) or isinstance(item, bool):
                raise ModelProviderError(
                    f"Model usage field {name!r} must be an integer"
                )
            return item

        return ModelUsage(
            prompt_tokens=integer("prompt_tokens"),
            completion_tokens=integer("completion_tokens"),
            total_tokens=integer("total_tokens"),
        )
