from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Protocol

#: Seconds to wait between tries while a model server is restarting: about
#: five minutes in all.
RECONNECT_WAITS = (5, 10, 20, 40, 60, 60, 60, 60)

#: Seconds to wait between tries while a model service says it is too busy
#: (HTTP 429 or 503): a little over three minutes in all.
BUSY_WAITS = (5, 15, 30, 60, 90)

#: How Kosika names itself to a model service. Some services, behind
#: Cloudflare, turn away a request that names no program (HTTP 403, 1010).
USER_AGENT = "Kosika/1.0"

#: Model servers that have answered at least once while this program runs.
#: Only one of these is waited for: a server that was never reached is more
#: likely a wrong address than a restart, and is reported at once.
_ANSWERED: set[str] = set()


def _server_of(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _server_restarting(exc: BaseException, url: str) -> bool:
    """Whether a failed request found a model server that had been working
    gone for now, rather than wrong. A refused connection reached nobody, so
    trying again is safe anywhere; a dropped one may have been answered, so
    it is tried again only on this computer, where a repeat costs nothing."""

    if _server_of(url) not in _ANSWERED:
        return False
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, ConnectionRefusedError):
        return True
    host = urllib.parse.urlsplit(url).hostname or ""
    return isinstance(
        reason, (http.client.RemoteDisconnected, ConnectionResetError)
    ) and host in (
        "localhost",
        "127.0.0.1",
        "::1",
    )


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
    # Set when the reply must be one JSON object, as every model step's is.
    # A provider that can ask its model for JSON does so; the reply is still
    # parsed and checked by Kosika either way. Small models often ignore a
    # written instruction to answer in JSON, and obey this.
    json_object: bool = False


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

    #: Its answers come in the order they are asked for, so it must never
    #: be asked from two places at once.
    ordered = True

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
#: How long Ollama is asked to keep a model loaded after it answers.
OLLAMA_KEEP_ALIVE = "30m"
#: The context windows Ollama is asked for, smallest first. Ollama gives a
#: model 4096 tokens unless told otherwise, and quietly drops the start of a
#: conversation that does not fit, which is where an agent's instructions
#: are. A request is given the smallest window that holds it and its answer;
#: a few sizes only, since Ollama loads a model again when the size changes.
OLLAMA_CONTEXTS = (8192, 16384, 32768)


def ollama_context(request: ModelRequest) -> int:
    """The context window a request to Ollama needs: its text, at about three
    characters a token, its answer and a margin, rounded up to a size in
    OLLAMA_CONTEXTS."""

    text = 0
    for message in request.messages:
        text += len(message.content or "")
        for call in message.tool_calls:
            text += len(call.name) + len(json.dumps(call.arguments, sort_keys=True))
    for tool in request.tools:
        text += len(tool.name) + len(tool.description)
        text += len(json.dumps(tool.input_schema, sort_keys=True))
    wanted = text // 3 + (request.max_output_tokens or 1024) + 512
    return next(
        (size for size in OLLAMA_CONTEXTS if size >= wanted), OLLAMA_CONTEXTS[-1]
    )


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
        # The largest context window this computer has room for, learned
        # when a larger one did not fit. See OLLAMA_CONTEXTS.
        self._context_limit = OLLAMA_CONTEXTS[-1]
        #: Reasoning models such as qwen3 emit a separate reasoning trace that
        #: consumes the output budget before any answer is produced. Set this
        #: to False when the caller needs a direct answer, for example a
        #: machine-readable patch. Left as None the server default applies.
        self.think = think
        #: Set by a host that wants to show an answer while it is written.
        #: When set, the reply is asked for as a stream and each piece of
        #: text is handed over as it arrives. The response returned at the
        #: end is the same as without it, so nothing downstream can tell.
        self.on_text: Callable[[str], None] | None = None

    def _streams(self, request: ModelRequest) -> bool:
        return (
            self.on_text is not None and self._transport is None and not request.tools
        )

    def _tell(self, piece: str) -> None:
        if piece and self.on_text is not None:
            try:
                self.on_text(piece)
            except Exception:  # noqa: BLE001 - a display must not break a run
                pass

    def _lines(self, request: urllib.request.Request) -> Iterator[bytes]:
        with self._open(request) as response:
            yield from response

    def _exchange_stream(self, payload: dict[str, Any], *, sse: bool) -> Any:
        """Ask for a streamed reply and put it back together as one reply."""

        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request_object = urllib.request.Request(
            self.endpoint, data=body, headers=headers, method="POST"
        )
        text: list[str] = []
        thought: list[str] = []
        last: dict[str, Any] = {}
        finish: Any = None
        usage: Any = None
        try:
            for raw in self._lines(request_object):
                line = raw.decode("utf-8").strip()
                if sse:
                    if not line.startswith("data:"):
                        continue
                    line = line[5:].strip()
                    if line == "[DONE]":
                        break
                if not line:
                    continue
                chunk = json.loads(line)
                if not isinstance(chunk, dict):
                    raise ModelProviderError("Model provider returned invalid JSON")
                if isinstance(chunk.get("error"), (str, dict)):
                    raise ModelProviderError(
                        f"Model provider reported an error: {chunk['error']}"
                    )
                if sse:
                    usage = chunk.get("usage") or usage
                    choices = chunk.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue
                    choice = choices[0] if isinstance(choices[0], dict) else {}
                    delta = choice.get("delta")
                    delta = delta if isinstance(delta, dict) else {}
                    finish = choice.get("finish_reason") or finish
                    reasoning = delta.get("reasoning")
                else:
                    last = chunk
                    delta = chunk.get("message")
                    delta = delta if isinstance(delta, dict) else {}
                    reasoning = delta.get("thinking")
                piece = delta.get("content")
                if isinstance(piece, str) and piece:
                    text.append(piece)
                    self._tell(piece)
                if isinstance(reasoning, str):
                    thought.append(reasoning)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ModelProviderError(
                f"Model provider returned HTTP {exc.code}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ModelProviderError(f"Model provider request failed: {exc}") from exc
        except TimeoutError as exc:
            raise ModelProviderError(
                f"Model did not answer within {self.timeout:g} seconds"
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelProviderError("Model provider returned invalid JSON") from exc
        message: dict[str, Any] = {"role": "assistant", "content": "".join(text)}
        if sse:
            if thought:
                message["reasoning"] = "".join(thought)
            return {
                "choices": [{"message": message, "finish_reason": finish or "stop"}],
                "usage": usage,
            }
        if thought:
            message["thinking"] = "".join(thought)
        return {**last, "message": message}

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
        if request.json_object and not request.tools:
            payload["response_format"] = {"type": "json_object"}
        if self.think is not None:
            payload["think"] = self.think
        if self._streams(request):
            return self._parse_response(
                self._exchange_stream(
                    {
                        **payload,
                        "stream": True,
                        "stream_options": {"include_usage": True},
                    },
                    sse=True,
                )
            )
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
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
        options["num_ctx"] = min(ollama_context(request), self._context_limit)

        payload: dict[str, Any] = {
            "model": request.model or self.model,
            "messages": [self._ollama_message(message) for message in request.messages],
            "stream": False,
            # Ollama unloads a model five minutes after its last answer,
            # and loading it again costs ten to twenty seconds on a slow
            # machine. Asked to, it keeps the model ready for half an hour.
            "keep_alive": OLLAMA_KEEP_ALIVE,
        }
        if options:
            payload["options"] = options
        if request.tools:
            # Ollama takes the same tool descriptions as the OpenAI shape. A
            # model that was not trained to choose tools answers in words and
            # the server says so with an HTTP error.
            payload["tools"] = [self._tool_to_dict(tool) for tool in request.tools]
        if request.json_object:
            payload["format"] = "json"
        if self.think is not None:
            payload["think"] = self.think
        while True:
            try:
                if self._streams(request):
                    raw = self._exchange_stream({**payload, "stream": True}, sse=False)
                else:
                    raw = self._exchange(payload)
                return self._parse_response(raw, "ollama")
            except ModelProviderError as exc:
                # A computer without the memory for a large window says so;
                # the same request is asked again with half the window, and
                # this provider asks for no more than that from then on.
                said = str(exc).lower()
                if options["num_ctx"] <= OLLAMA_CONTEXTS[0] or not (
                    "out-of-memory" in said
                    or "out of memory" in said
                    or "failed to allocate" in said
                ):
                    raise
                options["num_ctx"] //= 2
                self._context_limit = options["num_ctx"]

    def _exchange(self, payload: dict[str, Any]) -> Any:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
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
        with self._open(request) as response:
            return response.read()

    def _open(self, request: urllib.request.Request) -> Any:
        waits = list(RECONNECT_WAITS)
        busy = list(BUSY_WAITS)
        while True:
            try:
                response = urllib.request.urlopen(request, timeout=self.timeout)
                _ANSWERED.add(_server_of(request.full_url))
                return response
            except urllib.error.HTTPError as exc:
                # A service that says it is too busy, or that the rate is
                # used up for the moment, is asked again after a while; it
                # did not take the request, so asking again costs nothing.
                if exc.code not in (429, 502, 503, 504) or not busy:
                    raise
                pause = busy.pop(0)
                said = exc.headers.get("Retry-After") if exc.headers else None
                if said and said.strip().isdigit():
                    pause = min(int(said.strip()), 120)
                exc.close()
                time.sleep(pause)
            except (
                urllib.error.URLError,
                http.client.RemoteDisconnected,
                ConnectionResetError,
            ) as exc:
                if not waits or not _server_restarting(exc, request.full_url):
                    raise
                # A model server that is restarting, as Ollama does when it
                # updates itself, is waited for rather than given up on.
                time.sleep(waits.pop(0))

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
    def _ollama_message(message: ChatMessage) -> dict[str, Any]:
        """Ollama wants a tool call's arguments as an object, not as text."""

        payload: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_calls:
            payload["tool_calls"] = [
                {"function": {"name": call.name, "arguments": call.arguments}}
                for call in message.tool_calls
            ]
        if message.role == "tool" and message.name is not None:
            payload["tool_name"] = message.name
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
        raw_calls = message.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise ModelProviderError("Model response tool_calls must be a list")
        tool_calls: list[ModelToolCall] = []
        for raw_call in raw_calls:
            function = raw_call.get("function") if isinstance(raw_call, dict) else None
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
            # Each call needs an id of its own across the whole exchange, and
            # Ollama does not always give one.
            given = raw_call.get("id")
            call_id = (
                given if isinstance(given, str) and given else uuid.uuid4().hex[:12]
            )
            tool_calls.append(ModelToolCall(call_id, function["name"], arguments))
        return ModelResponse(
            content=content,
            tool_calls=tuple(tool_calls),
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
