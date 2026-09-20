from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .schema import SchemaValidationError, validate

MCP_PROTOCOL_VERSION = "2025-11-25"


class MCPProtocolError(RuntimeError):
    """Raised when an MCP server violates the expected JSON-RPC exchange."""


class MCPToolError(RuntimeError):
    """Raised when an MCP tool reports an execution error."""


@dataclass(frozen=True)
class MCPTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


class MCPStdioClient:
    """Small synchronous MCP stdio client for Kosika v0.1.

    The client launches a local server process, speaks newline-delimited JSON-RPC,
    performs the MCP initialize handshake, discovers tools, validates a declared
    tool by name, and calls it. It intentionally does not invoke a shell.
    """

    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        protocol_version: str = MCP_PROTOCOL_VERSION,
        timeout: float = 10.0,
    ) -> None:
        if not command:
            raise ValueError("MCP command cannot be empty")
        self.command = list(command)
        self.cwd = str(cwd) if cwd is not None else None
        self.env = dict(env) if env is not None else None
        self.protocol_version = protocol_version
        self.timeout = timeout
        self.process: subprocess.Popen[str] | None = None
        self._messages: queue.Queue[dict[str, Any] | BaseException] = queue.Queue()
        self._next_id = 1
        self.server_info: dict[str, Any] = {}
        self.server_capabilities: dict[str, Any] = {}
        self.protocol_version_negotiated: str | None = None
        self._reader_threads: list[threading.Thread] = []

    def __enter__(self) -> "MCPStdioClient":
        self.start()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def start(self) -> None:
        if self.process is not None:
            raise RuntimeError("MCP client is already started")
        child_env = os.environ.copy()
        if self.env is not None:
            child_env.update(self.env)
        self.process = subprocess.Popen(
            self.command,
            cwd=self.cwd,
            env=child_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._start_reader(self.process.stdout, "stdout")
        self._start_reader(self.process.stderr, "stderr")
        response = self.request(
            "initialize",
            {
                "protocolVersion": self.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "kosika", "version": "0.1.0"},
            },
        )
        result = response.get("result")
        if not isinstance(result, dict):
            raise MCPProtocolError(
                "initialize response did not contain a result object"
            )
        self.protocol_version_negotiated = result.get("protocolVersion")
        self.server_info = result.get("serverInfo", {})
        self.server_capabilities = result.get("capabilities", {})
        self.notify("notifications/initialized")

    def _start_reader(self, stream: Any, label: str) -> None:
        def read_stream() -> None:
            try:
                for line in stream:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    if label == "stderr":
                        continue
                    try:
                        message = json.loads(stripped)
                    except json.JSONDecodeError as exc:
                        self._messages.put(
                            MCPProtocolError(f"Invalid JSON from MCP stdout: {exc}")
                        )
                        continue
                    if isinstance(message, dict):
                        self._messages.put(message)
                    else:
                        self._messages.put(
                            MCPProtocolError("MCP message must be a JSON object")
                        )
                if label == "stdout":
                    self._messages.put(MCPProtocolError("MCP server closed stdout"))
            except BaseException as exc:  # pragma: no cover - defensive thread boundary
                if label == "stdout":
                    self._messages.put(exc)

        thread = threading.Thread(
            target=read_stream, name=f"kosika-mcp-{label}", daemon=True
        )
        thread.start()
        self._reader_threads.append(thread)

    def _send(self, message: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("MCP client is not started")
        self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    def request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)
        while True:
            try:
                incoming = self._messages.get(timeout=self.timeout)
            except queue.Empty as exc:
                raise TimeoutError(
                    f"Timed out waiting for MCP response to {method!r}"
                ) from exc
            if isinstance(incoming, BaseException):
                raise incoming
            if incoming.get("id") != request_id:
                continue
            if "error" in incoming:
                raise MCPProtocolError(f"MCP {method} failed: {incoming['error']}")
            return incoming

    def list_tools(self) -> list[MCPTool]:
        response = self.request("tools/list", {})
        result = response.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            raise MCPProtocolError("tools/list response did not contain a tools list")
        tools: list[MCPTool] = []
        for item in result["tools"]:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise MCPProtocolError("tools/list returned an invalid tool definition")
            tools.append(
                MCPTool(
                    name=item["name"],
                    description=str(item.get("description", "")),
                    input_schema=item.get("inputSchema", {}),
                    output_schema=item.get("outputSchema", {}),
                )
            )
        return tools

    def require_tool(self, name: str) -> MCPTool:
        for tool in self.list_tools():
            if tool.name == name:
                return tool
        raise MCPProtocolError(
            f"Declared MCP tool {name!r} was not advertised by the server"
        )

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        input_schema: Mapping[str, Any] | None = None,
        output_schema: Mapping[str, Any] | None = None,
    ) -> Any:
        self.require_tool(name)
        if input_schema is not None:
            try:
                validate(arguments, input_schema)
            except SchemaValidationError as exc:
                raise MCPProtocolError(
                    f"Invalid arguments for MCP tool {name!r}: {exc}"
                ) from exc
        response = self.request("tools/call", {"name": name, "arguments": arguments})
        result = response.get("result")
        if not isinstance(result, dict):
            raise MCPProtocolError(
                "tools/call response did not contain a result object"
            )
        if result.get("isError") is True:
            raise MCPToolError(
                f"MCP tool {name!r} returned an error: {result.get('content')}"
            )
        if "structuredContent" in result:
            value = result["structuredContent"]
        else:
            content = result.get("content", [])
            if len(content) == 1 and content[0].get("type") == "text":
                text = content[0].get("text", "")
                try:
                    value = json.loads(text)
                except json.JSONDecodeError:
                    value = text
            else:
                value = content
        if output_schema is not None:
            try:
                validate(value, output_schema)
            except SchemaValidationError as exc:
                raise MCPProtocolError(
                    f"Invalid result from MCP tool {name!r}: {exc}"
                ) from exc
        return value

    def close(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return
        if process.stdin is not None:
            process.stdin.close()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
