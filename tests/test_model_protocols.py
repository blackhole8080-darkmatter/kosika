"""Tests for the two model wire formats the local provider can speak.

The reason a second protocol exists is measured, not theoretical. Ollama's
OpenAI-compatible ``/v1`` endpoint accepts a ``think`` field and ignores it, so a
reasoning model spends its whole output budget on the trace and returns empty
content. The native ``/api/chat`` endpoint honours it. These tests pin both the
payload each protocol sends and the reply each one parses, so a server upgrade
that changes either shape fails here rather than as an empty patch.
"""

from __future__ import annotations

import http.client
import io
import json
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from unittest import mock

from kosika import model as model_module
from kosika.model import (
    MODEL_PROTOCOLS,
    ChatMessage,
    ModelProviderError,
    ModelRequest,
    ModelTool,
    OpenAICompatibleProvider,
    ollama_context,
)

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"

OLLAMA_HOST = "http://localhost:11434"
OPENAI_HOST = "http://localhost:11434/v1"


class Capture:
    """A transport that records the request and replays a canned reply."""

    def __init__(self, reply: bytes) -> None:
        self.reply = reply
        self.url = ""
        self.body: dict[str, Any] = {}
        self.headers: dict[str, str] = {}

    def __call__(self, request: Any, timeout: float) -> bytes:
        self.url = request.full_url
        self.headers = dict(request.headers)
        self.body = json.loads(request.data.decode("utf-8"))
        return self.reply


def broken_program(directory: str) -> Path:
    """A program that does not check, so the authoring loop needs a proposal."""

    path = Path(directory) / "broken.kos"
    path.write_text("record R {\n  a: Text\n", encoding="utf-8")
    return path


def ollama_reply(**overrides: Any) -> bytes:
    payload: dict[str, Any] = {
        "model": "qwen3:1.7b",
        "message": {"role": "assistant", "content": "OK"},
        "done": True,
        "eval_count": 2,
        "prompt_eval_count": 15,
    }
    payload.update(overrides)
    return json.dumps(payload).encode("utf-8")


class ProtocolSelectionTests(unittest.TestCase):
    def test_supported_protocols_are_explicit(self) -> None:
        self.assertEqual(MODEL_PROTOCOLS, ("openai", "ollama"))

    def test_unknown_protocol_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Model protocol must be one of"):
            OpenAICompatibleProvider(OLLAMA_HOST, "m", protocol="grpc")

    def test_openai_endpoint_is_unchanged_by_default(self) -> None:
        self.assertEqual(
            OpenAICompatibleProvider(OPENAI_HOST, "m").endpoint,
            "http://localhost:11434/v1/chat/completions",
        )
        self.assertEqual(OpenAICompatibleProvider(OPENAI_HOST, "m").protocol, "openai")

    def test_ollama_endpoint_targets_the_native_api(self) -> None:
        provider = OpenAICompatibleProvider(OLLAMA_HOST, "m", protocol="ollama")
        self.assertEqual(provider.endpoint, "http://localhost:11434/api/chat")

    def test_an_explicit_endpoint_suffix_is_not_duplicated(self) -> None:
        self.assertEqual(
            OpenAICompatibleProvider(
                "http://localhost:11434/v1/chat/completions", "m"
            ).endpoint,
            "http://localhost:11434/v1/chat/completions",
        )
        self.assertEqual(
            OpenAICompatibleProvider(
                "http://localhost:11434/api/chat", "m", protocol="ollama"
            ).endpoint,
            "http://localhost:11434/api/chat",
        )

    def test_a_trailing_slash_does_not_double_up(self) -> None:
        self.assertEqual(
            OpenAICompatibleProvider(
                OLLAMA_HOST + "/", "m", protocol="ollama"
            ).endpoint,
            "http://localhost:11434/api/chat",
        )


class OllamaRequestTests(unittest.TestCase):
    def complete(self, capture: Capture, **kwargs: Any) -> Any:
        provider = OpenAICompatibleProvider(
            OLLAMA_HOST, "qwen3:1.7b", protocol="ollama", transport=capture, **kwargs
        )
        return provider.complete(
            ModelRequest(
                messages=(ChatMessage(role="user", content="Reply OK"),),
                max_output_tokens=400,
                temperature=0.2,
                top_p=0.9,
                stop=["END"],
            )
        )

    def test_streaming_is_disabled_explicitly(self) -> None:
        capture = Capture(ollama_reply())
        self.complete(capture)
        self.assertIs(capture.body["stream"], False)

    def test_sampling_parameters_are_nested_under_options(self) -> None:
        capture = Capture(ollama_reply())
        self.complete(capture)
        options = capture.body["options"]
        self.assertEqual(options["temperature"], 0.2)
        self.assertEqual(options["top_p"], 0.9)
        self.assertEqual(options["stop"], ["END"])
        self.assertEqual(options["num_predict"], 400)
        self.assertNotIn("max_tokens", capture.body)
        self.assertNotIn("temperature", capture.body)

    def test_the_context_window_holds_the_request_and_its_answer(self) -> None:
        # Ollama's own default is 4096 tokens, and it drops the start of a
        # conversation that does not fit.
        capture = Capture(ollama_reply())
        self.complete(capture)
        self.assertEqual(capture.body["options"]["num_ctx"], 8192)
        long = ModelRequest(
            messages=(ChatMessage(role="user", content="x" * 30_000),),
            max_output_tokens=4000,
        )
        self.assertEqual(ollama_context(long), 16384)
        huge = ModelRequest(messages=(ChatMessage(role="user", content="x" * 400_000),))
        self.assertEqual(ollama_context(huge), 32768)

    def test_a_window_too_large_for_the_computer_is_halved(self) -> None:
        sent: list[int] = []

        def transport(request: Any, timeout: float) -> bytes:
            size = json.loads(request.data.decode("utf-8"))["options"]["num_ctx"]
            sent.append(size)
            if size > 16384:
                raise urllib.error.HTTPError(
                    request.full_url,
                    500,
                    "error",
                    None,  # type: ignore[arg-type]
                    io.BytesIO(
                        b'{"error":"llama-server reported out-of-memory during '
                        b'startup: failed to allocate buffer for kv cache"}'
                    ),
                )
            return ollama_reply()

        provider = OpenAICompatibleProvider(
            OLLAMA_HOST, "m", protocol="ollama", transport=transport
        )
        huge = ModelRequest(messages=(ChatMessage(role="user", content="x" * 60_000),))
        self.assertEqual(provider.complete(huge).content, "OK")
        self.assertEqual(provider.complete(huge).content, "OK")
        # Halved once, and remembered: the second request asks for less at once.
        self.assertEqual(sent, [32768, 16384, 16384])

    def test_think_false_reaches_the_wire(self) -> None:
        capture = Capture(ollama_reply())
        self.complete(capture, think=False)
        self.assertIs(capture.body["think"], False)

    def test_think_absent_means_server_default(self) -> None:
        capture = Capture(ollama_reply())
        self.complete(capture)
        self.assertNotIn("think", capture.body)

    def test_only_the_context_window_is_sent_when_nothing_is_requested(self) -> None:
        capture = Capture(ollama_reply())
        provider = OpenAICompatibleProvider(
            OLLAMA_HOST, "qwen3:1.7b", protocol="ollama", transport=capture
        )
        provider.complete(ModelRequest(messages=()))
        self.assertEqual(capture.body["options"], {"num_ctx": 8192})

    def test_tools_are_offered_to_the_model(self) -> None:
        capture = Capture(ollama_reply())
        provider = OpenAICompatibleProvider(
            OLLAMA_HOST, "qwen3:1.7b", protocol="ollama", transport=capture
        )
        provider.complete(
            ModelRequest(
                messages=(),
                tools=(
                    ModelTool(
                        name="read",
                        description="Read a file.",
                        input_schema={"type": "object"},
                    ),
                ),
            )
        )
        self.assertEqual(
            capture.body["tools"],
            [
                {
                    "type": "function",
                    "function": {
                        "name": "read",
                        "description": "Read a file.",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        )

    def test_out_of_range_parameters_fail_before_any_request(self) -> None:
        capture = Capture(ollama_reply())
        provider = OpenAICompatibleProvider(
            OLLAMA_HOST, "qwen3:1.7b", protocol="ollama", transport=capture
        )
        with self.assertRaisesRegex(ModelProviderError, "top_p must be between"):
            provider.complete(ModelRequest(messages=(), top_p=4.0))
        self.assertEqual(capture.url, "")

    def test_oversized_stop_sequences_fail_closed(self) -> None:
        capture = Capture(ollama_reply())
        provider = OpenAICompatibleProvider(
            OLLAMA_HOST, "qwen3:1.7b", protocol="ollama", transport=capture
        )
        with self.assertRaisesRegex(ModelProviderError, "stop must contain"):
            provider.complete(ModelRequest(messages=(), stop=["a", "b", "c", "d", "e"]))


class OllamaResponseTests(unittest.TestCase):
    def parse(self, payload: Any) -> Any:
        return OpenAICompatibleProvider._parse_response(payload, "ollama")

    def test_content_and_usage_are_read_from_the_native_shape(self) -> None:
        response = self.parse(json.loads(ollama_reply()))
        self.assertEqual(response.content, "OK")
        assert response.usage is not None
        self.assertEqual(response.usage.prompt_tokens, 15)
        self.assertEqual(response.usage.completion_tokens, 2)
        self.assertEqual(response.usage.total_tokens, 17)
        self.assertEqual(response.finish_reason, "stop")

    def test_thinking_is_preserved_as_reasoning(self) -> None:
        response = self.parse(
            json.loads(
                ollama_reply(
                    message={"role": "assistant", "content": "", "thinking": "because"}
                )
            )
        )
        self.assertEqual(response.content, "")
        self.assertEqual(response.reasoning, "because")

    def test_a_reasoning_only_reply_is_distinguishable_from_no_reply(self) -> None:
        response = self.parse(
            json.loads(
                ollama_reply(
                    message={"role": "assistant", "content": "", "thinking": "trace"}
                )
            )
        )
        self.assertEqual(response.content, "")
        self.assertIsNotNone(response.reasoning)

    def test_missing_token_counts_leave_usage_absent(self) -> None:
        response = self.parse(
            json.loads(json.dumps({"message": {"role": "assistant", "content": "OK"}}))
        )
        self.assertIsNone(response.usage)
        self.assertEqual(response.content, "OK")

    def test_unfinished_generation_reports_length(self) -> None:
        response = self.parse(
            json.loads(ollama_reply(done=False, message={"content": "partial"}))
        )
        self.assertEqual(response.finish_reason, "length")

    def test_malformed_replies_fail_closed(self) -> None:
        cases = (
            ([], "must be a JSON object"),
            ({}, "did not contain a message"),
            ({"message": []}, "did not contain a message"),
            ({"message": {"content": 4}}, "content must be text or null"),
            ({"message": {"content": "a", "thinking": 7}}, None),
        )
        for payload, message in cases:
            with self.subTest(payload=payload):
                if message is None:
                    response = self.parse(payload)
                    self.assertIsNone(response.reasoning)
                    continue
                with self.assertRaisesRegex(ModelProviderError, message):
                    self.parse(payload)


class RestartingServerTests(unittest.TestCase):
    """A model server that is restarting is waited for, not given up on."""

    def setUp(self) -> None:
        answered = set(model_module._ANSWERED)
        self.addCleanup(
            lambda: (
                model_module._ANSWERED.clear()
                or model_module._ANSWERED.update(answered)
            )
        )

    def tries(
        self, url: str, failures: list[BaseException], *, answered_before: bool = True
    ) -> tuple[list[float], Any]:
        if answered_before:
            model_module._ANSWERED.add(model_module._server_of(url))
        slept: list[float] = []
        calls = iter(failures)

        def opener(request: Any, timeout: float) -> Any:
            failure = next(calls, None)
            if failure is not None:
                raise failure
            return io.BytesIO(b"ok")

        provider = OpenAICompatibleProvider(url, "m", protocol="ollama")
        request = urllib.request.Request(url, data=b"{}", method="POST")
        with (
            mock.patch("urllib.request.urlopen", opener),
            mock.patch("time.sleep", slept.append),
        ):
            try:
                return slept, provider._send(request)
            except BaseException as exc:  # noqa: BLE001
                return slept, exc

    def test_a_refused_connection_is_tried_again(self) -> None:
        refused = urllib.error.URLError(ConnectionRefusedError(10061, "refused"))
        slept, got = self.tries(
            "https://models.example.com/api/chat", [refused, refused]
        )
        self.assertEqual(got, b"ok")
        self.assertEqual(slept, [5, 10])

    def test_a_dropped_answer_is_tried_again_only_on_this_computer(self) -> None:
        dropped = http.client.RemoteDisconnected("closed")
        slept, got = self.tries("http://localhost:11434/api/chat", [dropped])
        self.assertEqual((slept, got), ([5], b"ok"))
        slept, got = self.tries("https://models.example.com/api/chat", [dropped])
        self.assertEqual(slept, [])
        self.assertIsInstance(got, http.client.RemoteDisconnected)

    def test_it_gives_up_after_about_five_minutes(self) -> None:
        refused = urllib.error.URLError(ConnectionRefusedError(10061, "refused"))
        slept, got = self.tries("http://localhost:11434/api/chat", [refused] * 20)
        self.assertIsInstance(got, urllib.error.URLError)
        self.assertEqual(sum(slept), 315)

    def test_a_busy_service_is_asked_again_later(self) -> None:
        def busy(code: int) -> urllib.error.HTTPError:
            return urllib.error.HTTPError(
                "https://models.example.com/v1/chat",
                code,
                "busy",
                None,
                io.BytesIO(b""),  # type: ignore[arg-type]
            )

        slept, got = self.tries(
            "https://models.example.com/v1/chat",
            [busy(503), busy(429)],
            answered_before=False,
        )
        self.assertEqual((slept, got), ([5, 15], b"ok"))
        slept, got = self.tries(
            "https://models.example.com/v1/chat", [busy(400)], answered_before=False
        )
        self.assertEqual(slept, [])
        self.assertIsInstance(got, urllib.error.HTTPError)

    def test_a_server_never_reached_is_reported_at_once(self) -> None:
        refused = urllib.error.URLError(ConnectionRefusedError(10061, "refused"))
        slept, got = self.tries(
            "http://localhost:9/api/chat", [refused], answered_before=False
        )
        self.assertEqual(slept, [])
        self.assertIsInstance(got, urllib.error.URLError)

    def test_other_failures_are_not_waited_on(self) -> None:
        slept, got = self.tries(
            "http://localhost:11434/api/chat", [urllib.error.URLError("offline")]
        )
        self.assertEqual(slept, [])
        self.assertIsInstance(got, urllib.error.URLError)


class OpenAiRegressionTests(unittest.TestCase):
    """The default protocol must behave exactly as it did before."""

    def test_default_protocol_still_parses_openai_replies(self) -> None:
        response = OpenAICompatibleProvider._parse_response(
            {
                "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 14},
            }
        )
        self.assertEqual(response.content, "OK")
        assert response.usage is not None
        self.assertEqual(response.usage.total_tokens, 14)

    def test_default_protocol_still_uses_max_tokens_at_the_top_level(self) -> None:
        capture = Capture(b'{"choices":[{"message":{"content":"OK"}}]}')
        provider = OpenAICompatibleProvider(
            OPENAI_HOST, "local-model", transport=capture
        )
        provider.complete(ModelRequest(messages=(), max_output_tokens=64))
        self.assertEqual(capture.url, "http://localhost:11434/v1/chat/completions")
        self.assertEqual(capture.body["max_tokens"], 64)
        self.assertNotIn("options", capture.body)
        self.assertNotIn("stream", capture.body)

    def test_openai_tool_calls_still_work(self) -> None:
        capture = Capture(
            json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "c1",
                                        "function": {
                                            "name": "read",
                                            "arguments": '{"path":"/"}',
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            ).encode("utf-8")
        )
        provider = OpenAICompatibleProvider(
            OPENAI_HOST, "local-model", transport=capture
        )
        response = provider.complete(ModelRequest(messages=()))
        self.assertEqual(response.tool_calls[0].name, "read")
        self.assertEqual(response.tool_calls[0].arguments, {"path": "/"})


if __name__ == "__main__":
    unittest.main()
