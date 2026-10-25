"""A Langflow project's flows as MCP tools, through `kosika.langflow_mcp`."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from unittest.mock import patch

from kosika import langflow_mcp

PROJECT = "36dea484-a9df-4535-b706-d1f6d0851e7c"
ANSWER = "Langflow draws flows as nodes on a laptop."

TOOLS = [
    {
        "name": "kosika_brief",
        "description": "Writes a one-sentence brief from the text it is given.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "input_value": {"type": "string", "description": "Message to be passed as input."},
                "session_id": {"type": "string", "description": "Optional session."},
            },
            "required": [],
        },
    }
]


def _lower(headers: Any) -> dict[str, str]:
    return {str(name).lower(): str(value) for name, value in headers.items()}


def sse(message: dict[str, Any]) -> bytes:
    """What Langflow writes: a keep-alive comment, then one event."""

    return (
        ": ping - now\r\n\r\n"
        f"event: message\r\ndata: {json.dumps(message)}\r\n\r\n"
    ).encode("utf-8")


class StubLangflowMCP:
    """A stand-in for a project's Streamable HTTP MCP endpoint."""

    def __init__(self, *, error: dict[str, Any] | None = None) -> None:
        self.error = error
        self.requests: list[tuple[str, dict[str, str], dict[str, Any]]] = []
        self.session = "session-1"
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
                message = json.loads(self.rfile.read(length) or b"{}")
                stub.requests.append((self.path, _lower(self.headers), message))
                if "id" not in message:
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                method = message.get("method")
                if stub.error is not None:
                    reply: dict[str, Any] = {"jsonrpc": "2.0", "id": message["id"], "error": stub.error}
                elif method == "tools/list":
                    reply = {"jsonrpc": "2.0", "id": message["id"], "result": {"tools": TOOLS}}
                elif method == "tools/call":
                    # The answer comes twice: as the message, and as its text.
                    reply = {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "result": {
                            "content": [
                                {"type": "text", "text": ANSWER},
                                {"type": "text", "text": ANSWER + "\n"},
                            ],
                            "isError": False,
                        },
                    }
                else:
                    reply = {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "result": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "langflow-mcp-project-" + PROJECT, "version": "1.30.0"},
                        },
                    }
                body = sse(reply)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Mcp-Session-Id", stub.session)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_: object) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "StubLangflowMCP":
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.server.shutdown()
        self.server.server_close()

    def endpoint(self) -> str:
        return langflow_mcp.endpoint_for(self.url, PROJECT)


def ask(server: langflow_mcp.LangflowToolServer, *messages: dict[str, Any]) -> list[dict[str, Any]]:
    """Run messages through the bridge and read the replies it wrote."""

    stdin = io.StringIO("".join(json.dumps(message) + "\n" for message in messages))
    out, err = io.StringIO(), io.StringIO()
    langflow_mcp.serve(server, stdin, out, err)
    assert err.getvalue() == "", err.getvalue()
    return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def request(identifier: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": identifier, "method": method}
    if params is not None:
        message["params"] = params
    return message


class EventMessageTests(unittest.TestCase):
    def test_a_keep_alive_comment_is_not_a_message(self) -> None:
        self.assertEqual(
            langflow_mcp.event_messages(": ping\r\n\r\nevent: message\r\ndata: {\"id\": 1}\r\n\r\n"),
            [{"id": 1}],
        )

    def test_a_plain_json_body_is_a_message(self) -> None:
        self.assertEqual(langflow_mcp.event_messages('{"id": 2}'), [{"id": 2}])

    def test_text_that_is_not_json_is_an_error(self) -> None:
        with self.assertRaises(langflow_mcp.LangflowMCPError):
            langflow_mcp.event_messages("event: message\ndata: nope\n\n")

    def test_nothing_is_no_message(self) -> None:
        self.assertEqual(langflow_mcp.event_messages("  \r\n"), [])


class EndpointTests(unittest.TestCase):
    def test_the_endpoint_is_the_projects_streamable_route(self) -> None:
        self.assertEqual(
            langflow_mcp.endpoint_for("http://127.0.0.1:7860/", PROJECT),
            f"http://127.0.0.1:7860/api/v1/mcp/project/{PROJECT}/streamable",
        )


class ToolListTests(unittest.TestCase):
    def test_a_flow_is_offered_as_a_tool_kosika_can_declare(self) -> None:
        with StubLangflowMCP() as stub:
            server = langflow_mcp.LangflowToolServer(stub.endpoint(), api_key="k")
            (reply,) = ask(server, request(2, "tools/list", {}))
        tool = reply["result"]["tools"][0]
        self.assertEqual(tool["name"], "kosika_brief")
        self.assertEqual(tool["description"], TOOLS[0]["description"])
        self.assertEqual(tool["inputSchema"], langflow_mcp.KOSIKA_INPUT)
        self.assertEqual(tool["outputSchema"], langflow_mcp.KOSIKA_OUTPUT)

    def test_langflows_own_schemas_are_relayed_when_asked_for(self) -> None:
        with StubLangflowMCP() as stub:
            server = langflow_mcp.LangflowToolServer(stub.endpoint(), api_key="k", schemas="langflow")
            (reply,) = ask(server, request(2, "tools/list", {}))
        self.assertEqual(reply["result"]["tools"][0]["inputSchema"], TOOLS[0]["inputSchema"])

    def test_the_project_route_is_the_one_called(self) -> None:
        with StubLangflowMCP() as stub:
            server = langflow_mcp.LangflowToolServer(stub.endpoint(), api_key="k")
            ask(server, request(2, "tools/list", {}))
        self.assertEqual(stub.requests[-1][0], f"/api/v1/mcp/project/{PROJECT}/streamable")

    def test_the_key_is_sent_as_x_api_key(self) -> None:
        with StubLangflowMCP() as stub:
            server = langflow_mcp.LangflowToolServer(stub.endpoint(), api_key="secret")
            ask(server, request(2, "tools/list", {}))
        self.assertEqual(stub.requests[-1][1].get("x-api-key"), "secret")


class ToolCallTests(unittest.TestCase):
    def test_the_answer_comes_back_as_the_text_record(self) -> None:
        with StubLangflowMCP() as stub:
            server = langflow_mcp.LangflowToolServer(stub.endpoint(), api_key="k")
            (reply,) = ask(
                server,
                request(3, "tools/call", {"name": "kosika_brief", "arguments": {"input_value": "x"}}),
            )
        self.assertEqual(reply["result"]["structuredContent"], {"text": ANSWER})
        self.assertEqual(reply["result"]["content"], [{"type": "text", "text": ANSWER}])

    def test_the_arguments_reach_langflow_unchanged(self) -> None:
        with StubLangflowMCP() as stub:
            server = langflow_mcp.LangflowToolServer(stub.endpoint(), api_key="k")
            ask(
                server,
                request(3, "tools/call", {"name": "kosika_brief", "arguments": {"input_value": "x"}}),
            )
        self.assertEqual(
            stub.requests[-1][2]["params"],
            {"name": "kosika_brief", "arguments": {"input_value": "x"}},
        )

    def test_answers_that_differ_are_both_kept(self) -> None:
        result = {
            "content": [
                {"type": "text", "text": "one"},
                {"type": "text", "text": "two"},
            ]
        }
        self.assertEqual(langflow_mcp.kosika_answer(result)["structuredContent"], {"text": "one\ntwo"})

    def test_an_error_answer_is_left_alone(self) -> None:
        result = {"content": [{"type": "text", "text": "bad"}], "isError": True}
        self.assertEqual(langflow_mcp.kosika_answer(result), result)


class SessionAndAuthTests(unittest.TestCase):
    def test_the_session_langflow_names_is_sent_back(self) -> None:
        with StubLangflowMCP() as stub:
            server = langflow_mcp.LangflowToolServer(stub.endpoint(), api_key="k")
            ask(server, request(1, "initialize", {}), request(2, "tools/list", {}))
        self.assertEqual(stub.requests[-1][1].get("mcp-session-id"), "session-1")

    def test_without_a_key_an_auto_login_session_is_used(self) -> None:
        with StubLangflowMCP() as stub:
            server = langflow_mcp.LangflowToolServer(stub.endpoint(), api_key=None)
            ask(server, request(2, "tools/list", {}))
        self.assertEqual(stub.requests[0][0], "/api/v1/auto_login")
        self.assertEqual(stub.requests[-1][1].get("authorization"), "Bearer session-token")


class ReplyTests(unittest.TestCase):
    def test_a_notification_gets_no_reply(self) -> None:
        with StubLangflowMCP() as stub:
            server = langflow_mcp.LangflowToolServer(stub.endpoint(), api_key="k")
            self.assertEqual(ask(server, {"jsonrpc": "2.0", "method": "notifications/initialized"}), [])
        self.assertEqual(stub.requests[-1][2]["method"], "notifications/initialized")

    def test_a_method_the_bridge_does_not_offer_is_refused(self) -> None:
        with StubLangflowMCP() as stub:
            server = langflow_mcp.LangflowToolServer(stub.endpoint(), api_key="k")
            (reply,) = ask(server, request(4, "resources/list", {}))
        self.assertEqual(reply["error"]["code"], -32601)

    def test_ping_is_answered_without_langflow(self) -> None:
        with StubLangflowMCP() as stub:
            server = langflow_mcp.LangflowToolServer(stub.endpoint(), api_key="k")
            (reply,) = ask(server, request(5, "ping"))
            self.assertEqual(stub.requests, [])
        self.assertEqual(reply["result"], {})

    def test_an_error_from_langflow_keeps_its_code(self) -> None:
        with StubLangflowMCP(error={"code": -32602, "message": "Unknown tool"}) as stub:
            server = langflow_mcp.LangflowToolServer(stub.endpoint(), api_key="k")
            (reply,) = ask(server, request(3, "tools/call", {"name": "nope", "arguments": {}}))
        self.assertEqual(reply["error"], {"code": -32602, "message": "Unknown tool"})

    def test_langflow_being_unreachable_is_reported(self) -> None:
        server = langflow_mcp.LangflowToolServer("http://127.0.0.1:9/api/v1/mcp/project/x/streamable", api_key="k")
        (reply,) = ask(server, request(6, "tools/list", {}))
        self.assertEqual(reply["error"]["code"], -32603)
        self.assertIn("Cannot reach Langflow", reply["error"]["message"])

    def test_a_line_that_is_not_json_is_reported_and_skipped(self) -> None:
        with StubLangflowMCP() as stub:
            server = langflow_mcp.LangflowToolServer(stub.endpoint(), api_key="k")
            out, err = io.StringIO(), io.StringIO()
            langflow_mcp.serve(server, io.StringIO("not json\n"), out, err)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("not a JSON message", err.getvalue())


class MainTests(unittest.TestCase):
    def run_main(self, arguments: list[str], stdin: str, environment: dict[str, str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, "stdin", io.StringIO(stdin)):
            with patch.dict("os.environ", environment):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = langflow_mcp.main(arguments)
        return code, out.getvalue(), err.getvalue()

    def test_without_a_project_the_bridge_says_what_is_missing(self) -> None:
        code, out, err = self.run_main(
            [], "", {"LANGFLOW_MCP_PROJECT": "", "LANGFLOW_MCP_URL": ""}
        )
        self.assertEqual(code, 2)
        self.assertIn("LANGFLOW_MCP_PROJECT", err)
        self.assertEqual(out, "")

    def test_the_project_comes_from_the_environment(self) -> None:
        with StubLangflowMCP() as stub:
            code, out, err = self.run_main(
                [],
                json.dumps(request(2, "tools/list", {})) + "\n",
                {
                    "LANGFLOW_MCP_PROJECT": PROJECT,
                    "LANGFLOW_URL": stub.url,
                    "LANGFLOW_API_KEY": "k",
                },
            )
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(json.loads(out)["result"]["tools"][0]["name"], "kosika_brief")

    def test_an_endpoint_can_be_named_instead_of_a_project(self) -> None:
        with StubLangflowMCP() as stub:
            code, out, _ = self.run_main(
                ["--endpoint", stub.endpoint()],
                json.dumps(request(2, "tools/list", {})) + "\n",
                {"LANGFLOW_MCP_PROJECT": "", "LANGFLOW_API_KEY": "k"},
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["result"]["tools"][0]["name"], "kosika_brief")


if __name__ == "__main__":
    unittest.main()
