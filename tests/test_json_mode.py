"""A model step asks the provider for JSON, not only the model in words."""

from __future__ import annotations

import json
import unittest
import urllib.request
from typing import Any

from kosika.model import ChatMessage, ModelRequest, OpenAICompatibleProvider
from kosika.model_steps import call_model

SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}


def capture(protocol: str) -> tuple[OpenAICompatibleProvider, list[dict[str, Any]]]:
    sent: list[dict[str, Any]] = []

    def transport(request: urllib.request.Request, timeout: float) -> bytes:
        sent.append(json.loads(request.data.decode("utf-8")))  # type: ignore[union-attr]
        answer = json.dumps({"text": "Paris."})
        if protocol == "ollama":
            return json.dumps(
                {
                    "message": {"role": "assistant", "content": answer},
                    "done_reason": "stop",
                }
            ).encode("utf-8")
        return json.dumps(
            {"choices": [{"message": {"content": answer}, "finish_reason": "stop"}]}
        ).encode("utf-8")

    provider = OpenAICompatibleProvider(
        "http://localhost:1", "m", transport=transport, protocol=protocol
    )
    return provider, sent


class JsonModeTests(unittest.TestCase):
    def test_a_model_step_asks_for_a_json_object(self) -> None:
        provider, sent = capture("openai")
        result, _ = call_model(provider, {"text": "Capital of France?"}, SCHEMA)
        self.assertEqual(result, {"text": "Paris."})
        self.assertEqual(sent[0]["response_format"], {"type": "json_object"})

    def test_the_ollama_protocol_uses_its_own_spelling(self) -> None:
        provider, sent = capture("ollama")
        call_model(provider, {"text": "x"}, SCHEMA)
        self.assertEqual(sent[0]["format"], "json")

    def test_an_ordinary_request_is_left_alone(self) -> None:
        provider, sent = capture("openai")
        provider.complete(
            ModelRequest(messages=(ChatMessage(role="user", content="hi"),))
        )
        self.assertNotIn("response_format", sent[0])


if __name__ == "__main__":
    unittest.main()
