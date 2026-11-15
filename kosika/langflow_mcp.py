"""A Langflow project's flows, offered to Kosika as MCP tools.

Kosika reaches the outside world through models and MCP tools, and it speaks
MCP over a server's standard input and output. Langflow offers a project's
flows over Streamable HTTP instead. This is the bridge between the two:

    tool brief {
      source mcp "flows"
      operation: "kosika_brief"
      access: execute
      input: BriefIn
      output: BriefOut
    }

The declaration only asks. The person running the program grants the server:

    kosika run main.kos --input in.json --models models.json \\
      --mcp-server kosika/langflow_mcp.py \\
      --mcp-manifest flows.manifest.json --mcp-source flows

and names the project in the environment, because Kosika starts an MCP server
with no arguments of its own:

| Variable | Meaning | Default |
| --- | --- | --- |
| `LANGFLOW_MCP_PROJECT` | The project whose flows become tools | required |
| `LANGFLOW_MCP_URL` | The whole Streamable HTTP endpoint, if not `/api/v1/mcp/project/ID/streamable` | unset |
| `LANGFLOW_URL` | Where Langflow listens | `http://127.0.0.1:7860` |
| `LANGFLOW_API_KEY` | Sent as `x-api-key`; without one, auto-login is tried | unset |
| `LANGFLOW_MCP_TIMEOUT` | Seconds to wait for Langflow | `55` |
| `LANGFLOW_MCP_SCHEMAS` | `kosika` or `langflow` tool schemas | `kosika` |

Run by hand, the environment takes flags instead:

    kosika-langflow-mcp --project 36dea484-... [--url URL] [--timeout 30]

Only tools a flow has enabled for MCP appear; Langflow marks none of them
read-only, so each call is an `access: execute` tool and asks the person
first. A flow that runs a model takes as long as that model takes, and a host
waits a fixed time for each request: ten seconds in `kosika agent`, sixty in
`kosika run`. A slow flow belongs on the longer budget.

The schemas are where Langflow and Kosika disagree. Langflow describes each
flow's tool as `input_value` and an optional `session_id`, with prose on both
and no `additionalProperties`, and advertises no output schema. Kosika pins a
tool's schema, then refuses a declaration whose record is not that schema
exactly, and a Kosika record always closes its fields and requires them all.
The two cannot be equal, so by default the bridge offers each flow the way a
Kosika declaration can be written -- one required `input_value` in, one
`text` out -- and forwards only that text on. `--schemas langflow` relays
Langflow's own schemas for other MCP clients, which Kosika cannot pin.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable, TextIO

#: Where Langflow listens unless told otherwise.
DEFAULT_URL = "http://127.0.0.1:7860"
#: The longest one request may wait for Langflow, under a host's own budget.
DEFAULT_TIMEOUT = 55.0
#: What a Kosika declaration can say: one text in, one text out, both closed.
KOSIKA_INPUT = {
    "type": "object",
    "properties": {"input_value": {"type": "string"}},
    "required": ["input_value"],
    "additionalProperties": False,
}
KOSIKA_OUTPUT = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}


class LangflowMCPError(Exception):
    """Langflow could not be reached, or answered with something else."""


def endpoint_for(base_url: str, project: str) -> str:
    """The Streamable HTTP endpoint of one project's MCP server."""

    base = base_url.rstrip("/")
    return f"{base}/api/v1/mcp/project/{urllib.parse.quote(project, safe='')}/streamable"


def _get_token(base_url: str, timeout: float) -> str:
    """A session token from a Langflow that allows auto-login, such as Desktop."""

    url = f"{base_url.rstrip('/')}/api/v1/auto_login"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raise LangflowMCPError(
            f"Langflow answered {exc.code} for {url}; set LANGFLOW_API_KEY"
        ) from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise LangflowMCPError(f"Langflow did not give an auto-login session: {exc}") from exc
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token:
        raise LangflowMCPError("Langflow did not give an auto-login session; set LANGFLOW_API_KEY")
    return token


def event_messages(body: str) -> list[Any]:
    """The JSON messages in a response body, plain or server-sent events.

    Langflow answers with `text/event-stream`, sends a comment line as a
    keep-alive before the message, and ends the stream when the request is
    done. Comments and `event:` lines carry no message and are skipped.
    """

    stripped = body.strip()
    if not stripped:
        return []
    if not stripped.startswith(("event:", "data:", ":", "id:", "retry:")):
        try:
            payload = json.loads(stripped)
        except ValueError as exc:
            raise LangflowMCPError(f"Langflow answered with something other than JSON: {exc}") from exc
        return payload if isinstance(payload, list) else [payload]
    messages: list[Any] = []
    data: list[str] = []
    for line in stripped.splitlines():
        line = line.rstrip("\r")
        if not line:
            if data:
                messages.append(_json_or_error("\n".join(data)))
                data = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
        # `event:`, `id:` and `retry:` say how to read the data, not what it is.
    if data:
        messages.append(_json_or_error("\n".join(data)))
    return messages


def _json_or_error(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError as exc:
        raise LangflowMCPError(f"Langflow sent a message that is not JSON: {exc}") from exc


class LangflowToolServer:
    """One Langflow project's MCP server, reached over Streamable HTTP."""

    def __init__(
        self,
        url: str,
        *,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        schemas: str = "kosika",
    ) -> None:
        self.url = url
        self.api_key = api_key
        self.timeout = timeout
        self.schemas = schemas
        self.session_id: str | None = None
        self._token: str | None = None

    def _authorization(self) -> dict[str, str]:
        if self.api_key:
            return {"x-api-key": self.api_key}
        if self._token is None:
            base = self.url.split("/api/", 1)[0]
            self._token = _get_token(base, self.timeout)
        return {"Authorization": f"Bearer {self._token}"}

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self._authorization(),
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def post(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Send one JSON-RPC message and return its reply, if it has one."""

        body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(self.url, data=body, headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                session = response.headers.get("Mcp-Session-Id")
                if session:
                    self.session_id = session
                content_type = response.headers.get("Content-Type", "")
                text = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300].strip()
            raise LangflowMCPError(
                f"Langflow answered {exc.code} for {self.url}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise LangflowMCPError(f"Cannot reach Langflow at {self.url}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise LangflowMCPError(
                f"Langflow did not answer {self.url} within {self.timeout:g} seconds"
            ) from exc
        wanted = message.get("id")
        messages = event_messages(text)
        if wanted is None:
            return None
        for reply in messages:
            if isinstance(reply, dict) and reply.get("id") == wanted:
                return reply
        if len(messages) == 1 and isinstance(messages[0], dict) and "id" not in messages[0]:
            return messages[0]
        raise LangflowMCPError(
            f"Langflow did not answer request {wanted!r} ({content_type.split(';')[0]})"
        )


def handle(message: dict[str, Any], server: LangflowToolServer) -> dict[str, Any] | None:
    """One JSON-RPC message in, one reply out, or none for a notification."""

    method = message.get("method")
    identifier = message.get("id")
    if not isinstance(method, str):
        if identifier is None:
            return None
        return _error(identifier, -32600, "Not a JSON-RPC request: it names no method")
    if identifier is None:
        # A notification: relay it and say nothing back.
        try:
            server.post(message)
        except LangflowMCPError as exc:
            print(f"kosika-langflow-mcp: {exc}", file=sys.stderr)
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": identifier, "result": {}}
    if method not in {"initialize", "tools/list", "tools/call"}:
        return _error(identifier, -32601, f"This bridge does not offer {method!r}")
    try:
        reply = server.post(message)
    except LangflowMCPError as exc:
        return _error(identifier, -32603, str(exc))
    if reply is None:
        return _error(identifier, -32603, f"Langflow answered {method!r} with nothing")
    if "error" in reply and "result" not in reply:
        error = reply.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        data = error.get("message") if isinstance(error, dict) else error
        return _error(
            identifier, code if isinstance(code, int) else -32603, str(data)
        )
    result = reply.get("result")
    if method == "tools/call" and isinstance(result, dict):
        result = one_answer(result)
        if server.schemas == "kosika":
            result = kosika_answer(result)
    if method == "tools/list" and server.schemas == "kosika" and isinstance(result, dict):
        result = kosika_tools(result)
    return {"jsonrpc": "2.0", "id": identifier, "result": result}


def kosika_answer(result: dict[str, Any]) -> dict[str, Any]:
    """A flow's answer as the `{text}` record a Kosika tool promised.

    An MCP client validates the answer against the schema it was offered,
    before any declaration sees it, so the text is also sent as
    `structuredContent`.
    """

    if result.get("isError") is True:
        return result
    content = result.get("content")
    if not isinstance(content, list):
        return result
    texts = [
        item["text"]
        for item in content
        if isinstance(item, dict)
        and item.get("type") == "text"
        and isinstance(item.get("text"), str)
    ]
    if not texts:
        return result
    answer = texts[0] if len(texts) == 1 else "\n".join(texts)
    return {
        "content": [{"type": "text", "text": answer}],
        "structuredContent": {"text": answer},
        "isError": False,
    }


def kosika_tools(result: dict[str, Any]) -> dict[str, Any]:
    """The flows as tools Kosika can declare and pin.

    Names and descriptions are Langflow's own; the schemas are the shape a
    Kosika record can take, because a pinned manifest has to agree with that
    record exactly.
    """

    tools = result.get("tools")
    if not isinstance(tools, list):
        return result
    offered = [
        {
            "name": tool["name"],
            "description": str(tool.get("description", "")),
            "inputSchema": dict(KOSIKA_INPUT),
            "outputSchema": dict(KOSIKA_OUTPUT),
        }
        for tool in tools
        if isinstance(tool, dict) and isinstance(tool.get("name"), str)
    ]
    tidied = dict(result)
    tidied["tools"] = offered
    return tidied


def one_answer(result: dict[str, Any]) -> dict[str, Any]:
    """A flow's answer once, when Langflow reports it twice.

    A flow that ends in Chat Output answers with its message and with that
    message's text as well; the two differ only in blank lines. Kosika fits a
    tool's answer into one text field, so both would be joined and the answer
    read doubled. When every item is text and they all say the same words,
    the first is kept.
    """

    content = result.get("content")
    if not isinstance(content, list) or len(content) < 2:
        return result
    if any(not isinstance(item, dict) or item.get("type") != "text" for item in content):
        return result
    texts = [item.get("text") for item in content]
    if any(not isinstance(text, str) for text in texts):
        return result
    if len({re.sub(r"\s+", " ", text).strip() for text in texts}) != 1:
        return result
    tidied = dict(result)
    tidied["content"] = [content[0]]
    return tidied


def _error(identifier: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": identifier,
        "error": {"code": code, "message": message},
    }


def serve(
    server: LangflowToolServer,
    stdin: Iterable[str],
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Answer MCP messages until standard input closes."""

    for line in stdin:
        text = line.strip()
        if not text:
            continue
        try:
            message = json.loads(text)
        except ValueError:
            print(f"kosika-langflow-mcp: not a JSON message: {text[:200]}", file=stderr)
            continue
        if not isinstance(message, dict):
            print("kosika-langflow-mcp: a message must be a JSON object", file=stderr)
            continue
        reply = handle(message, server)
        if reply is None:
            continue
        stdout.write(json.dumps(reply, ensure_ascii=False, separators=(",", ":")) + "\n")
        stdout.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kosika-langflow-mcp",
        description="Offer a Langflow project's flows to an MCP host, over stdin and stdout.",
    )
    parser.add_argument(
        "--project",
        default=os.environ.get("LANGFLOW_MCP_PROJECT"),
        help="The project whose MCP-enabled flows become tools (LANGFLOW_MCP_PROJECT).",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("LANGFLOW_URL", DEFAULT_URL),
        help=f"Where Langflow listens (default {DEFAULT_URL}).",
    )
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("LANGFLOW_MCP_URL"),
        help="The whole Streamable HTTP endpoint, instead of --project (LANGFLOW_MCP_URL).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("LANGFLOW_MCP_TIMEOUT", DEFAULT_TIMEOUT)),
        help=f"Seconds to wait for Langflow (default {DEFAULT_TIMEOUT:g}).",
    )
    parser.add_argument(
        "--schemas",
        choices=("kosika", "langflow"),
        default=os.environ.get("LANGFLOW_MCP_SCHEMAS", "kosika"),
        help=(
            "What the tools' schemas say: 'kosika' (the default) offers one "
            "required input_value and one text answer, which a pinned manifest "
            "can agree with; 'langflow' relays Langflow's own schemas."
        ),
    )
    args = parser.parse_args(argv)
    # A host reads and writes UTF-8 whatever the console's code page is, and
    # a model's answer is not ASCII.
    for stream in (sys.stdin, sys.stdout):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    if not args.endpoint and not args.project:
        print(
            "kosika-langflow-mcp: name the project to expose: --project ID, or "
            "LANGFLOW_MCP_PROJECT=ID. Langflow lists project ids under Projects.",
            file=sys.stderr,
        )
        return 2
    url = args.endpoint or endpoint_for(args.url, args.project)
    server = LangflowToolServer(
        url,
        api_key=os.environ.get("LANGFLOW_API_KEY") or None,
        timeout=args.timeout,
        schemas=args.schemas,
    )
    return serve(server, sys.stdin, sys.stdout, sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
