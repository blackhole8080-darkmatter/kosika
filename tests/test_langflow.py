"""A Langflow flow as a Kosika model, through `kosika.langflow`."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from kosika import langflow


def _lower(headers: object) -> dict[str, str]:
    """Header names as sent, lowercased, so a test need not guess the casing."""

    return {str(name).lower(): str(value) for name, value in headers.items()}  # type: ignore[attr-defined]


class StubLangflow:
    """A tiny stand-in for Langflow's flow and auto-login endpoints."""

    def __init__(self, *, answer: str = '{"text": "hi", "confidence": 1}', status: int = 200) -> None:
        self.answer = answer
        self.status = status
        self.requests: list[tuple[str, dict[str, str], dict]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                stub.requests.append((self.path, _lower(self.headers), {}))
                body = json.dumps({"access_token": "session-token"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                stub.requests.append((self.path, _lower(self.headers), json.loads(raw or b"{}")))
                if stub.status != 200:
                    body = b'{"detail": "flow failed"}'
                    self.send_response(stub.status)
                else:
                    body = json.dumps(
                        {
                            "session_id": "s",
                            "outputs": [
                                {
                                    "outputs": [
                                        {
                                            "results": {
                                                "message": {"text": stub.answer},
                                            }
                                        }
                                    ]
                                }
                            ],
                        }
                    ).encode()
                    self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_: object) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "StubLangflow":
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.server.shutdown()
        self.server.server_close()


class AnswerTextTests(unittest.TestCase):
    def test_the_chat_output_text_is_read(self) -> None:
        payload = {"outputs": [{"outputs": [{"results": {"message": {"text": "hello"}}}]}]}
        self.assertEqual(langflow.answer_text(payload), "hello")

    def test_a_plain_text_result_is_read(self) -> None:
        payload = {"outputs": [{"outputs": [{"results": {"text": "hello"}}]}]}
        self.assertEqual(langflow.answer_text(payload), "hello")

    def test_an_empty_answer_is_an_error(self) -> None:
        with self.assertRaises(langflow.LangflowError):
            langflow.answer_text({"outputs": [{"outputs": [{"results": {}}]}]})


class RunFlowTests(unittest.TestCase):
    def test_the_prompt_goes_to_the_named_flow_with_the_key(self) -> None:
        with StubLangflow(answer='{"a": 1}') as stub:
            answer = langflow.run_flow("ask this", "my-flow", base_url=stub.url, api_key="secret")
        self.assertEqual(answer, '{"a": 1}')
        path, headers, body = stub.requests[-1]
        self.assertEqual(path, "/api/v1/run/my-flow")
        self.assertEqual(headers.get("x-api-key"), "secret")
        self.assertEqual(body["input_value"], "ask this")
        self.assertEqual(body["input_type"], "chat")
        self.assertEqual(body["output_type"], "chat")

    def test_without_a_key_an_auto_login_session_is_used(self) -> None:
        with StubLangflow() as stub:
            langflow.run_flow("ask this", "my-flow", base_url=stub.url, api_key=None)
        paths = [path for path, _, _ in stub.requests]
        self.assertEqual(paths[0], "/api/v1/auto_login")
        headers = stub.requests[-1][1]
        self.assertEqual(headers.get("authorization"), "Bearer session-token")

    def test_a_failed_flow_names_the_status(self) -> None:
        with StubLangflow(status=500) as stub:
            with self.assertRaises(langflow.LangflowError) as caught:
                langflow.run_flow("ask", "my-flow", base_url=stub.url, api_key="secret")
        self.assertIn("500", str(caught.exception))

    def test_an_empty_prompt_is_refused(self) -> None:
        with self.assertRaises(langflow.LangflowError):
            langflow.run_flow("   ", "my-flow")


class MainTests(unittest.TestCase):
    def test_the_answer_is_printed(self) -> None:
        with StubLangflow(answer="answered") as stub:
            out, err = io.StringIO(), io.StringIO()
            with patch.object(sys, "stdin", io.StringIO("ask")):
                with patch.dict("os.environ", {"LANGFLOW_API_KEY": "k"}):
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                        code = langflow.main(["my-flow", "--url", stub.url])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue().strip(), "answered")
        self.assertEqual(err.getvalue(), "")

    def test_a_failure_is_reported_and_exits_one(self) -> None:
        with StubLangflow(status=500) as stub:
            out, err = io.StringIO(), io.StringIO()
            with patch.object(sys, "stdin", io.StringIO("ask")):
                with patch.dict("os.environ", {"LANGFLOW_API_KEY": "k"}):
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                        code = langflow.main(["my-flow", "--url", stub.url])
        self.assertEqual(code, 1)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("kosika-langflow:", err.getvalue())


if __name__ == "__main__":
    unittest.main()
