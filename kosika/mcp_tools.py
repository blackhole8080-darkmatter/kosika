"""MCP servers as tools, granted by the person running a program.

A program asks for a tool on an MCP server by naming the server:

    tool search_notes {
      source mcp "notes"
      operation: "search"            // the server's own name for the tool
      access: observe
      input: SearchIn                // fields the server's tool takes
      output: SearchOut              // {text: Text}, or the tool's own shape
    }

Naming a server reaches nothing. The person running the program says which
command each server name stands for (`--mcp notes="python notes_server.py"`),
and only then is the tool bound. This is the same rule as for files, the
shell and the web: a declaration asks, the host grants.

What holds once a server is granted:

* Kosika starts the server's command itself, with its arguments as a list
  and never through a shell, and speaks MCP to it over standard input and
  output. One process per server is kept for the life of the host.
* A tool may be declared `access: observe` only when the server itself marks
  it read-only. Anything else must be `access: execute`, so that every call
  waits for approval. A server's word is all there is to go on here: a
  server that lies about being read-only is trusted by granting it.
* What comes back is checked against the declared output record. A tool
  that answers in plain text fits a record with one Text field, and long
  text is cut.

This is not a sandbox. A granted server is a program running with the
authority of the person who granted it.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import threading
from typing import Any, Callable, Mapping, Sequence

from .ast import Program, ToolDecl
from .mcp_stdio import MCPProtocolError, MCPStdioClient, MCPToolError
from .schema import SchemaValidationError, schema_for_record, validate

MAX_TEXT_CHARACTERS = 20_000
MAX_SERVERS = 16
START_TIMEOUT_SECONDS = 60.0


class MCPGrantError(ValueError):
    """An MCP server is granted, declared, or answered in a way that is refused."""


def parse_grant(text: str) -> tuple[str, list[str]]:
    """`NAME=COMMAND` as a server name and the command's words."""

    name, found, command = text.partition("=")
    name = name.strip()
    if not found or not name.replace("_", "").replace("-", "").isalnum():
        raise MCPGrantError(
            f"MCP grant {text!r} must look like NAME=COMMAND, for example "
            'notes="python notes_server.py"'
        )
    if name in ("system", "local_mock") or name.startswith(("memory", "board")):
        raise MCPGrantError(f"{name!r} cannot be used as an MCP server name")
    words = [
        word.strip('"') for word in shlex.split(command.strip(), posix=os.name != "nt")
    ]
    if not words:
        raise MCPGrantError(f"MCP grant {text!r} names no command")
    return name, words


def _text_of(value: Any) -> str:
    """The words in a tool result, whatever shape it came in."""

    if isinstance(value, str):
        text = value
    elif isinstance(value, list) and all(isinstance(item, dict) for item in value):
        text = "\n".join(
            str(item.get("text", "")) for item in value if item.get("type") == "text"
        )
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(text) > MAX_TEXT_CHARACTERS:
        text = text[:MAX_TEXT_CHARACTERS] + "\n[cut]"
    return text


def shape(value: Any, output_schema: Mapping[str, Any], tool: str) -> dict[str, Any]:
    """A tool's answer as the record its declaration promised."""

    if isinstance(value, dict):
        try:
            validate(value, dict(output_schema))
            return value
        except SchemaValidationError:
            pass
    properties = output_schema.get("properties", {})
    if len(properties) == 1:
        ((field, kind),) = properties.items()
        if kind.get("type") == "string":
            return {field: _text_of(value)}
    raise MCPGrantError(
        f"MCP tool {tool!r} answered with something that is not its declared "
        "output record. A tool that answers in text needs an output record "
        "with exactly one Text field."
    )


class MCPHub:
    """The MCP servers a host has granted, started when first needed."""

    def __init__(
        self,
        servers: Mapping[str, Sequence[str]],
        *,
        client: Callable[..., Any] = MCPStdioClient,
        timeout: float = START_TIMEOUT_SECONDS,
    ) -> None:
        if len(servers) > MAX_SERVERS:
            raise MCPGrantError(f"At most {MAX_SERVERS} MCP servers can be granted")
        self.servers = {name: list(command) for name, command in servers.items()}
        self._client_factory = client
        self._timeout = timeout
        self._clients: dict[str, Any] = {}
        self._tools: dict[str, list[dict[str, Any]]] = {}
        self._lock = threading.RLock()

    @property
    def names(self) -> list[str]:
        return list(self.servers)

    def _client(self, name: str) -> Any:
        if name not in self.servers:
            raise MCPGrantError(f"MCP server {name!r} was not granted")
        if name not in self._clients:
            command = list(self.servers[name])
            # `npx` and friends are .cmd files on Windows, which a process
            # cannot be started from by bare name.
            command[0] = shutil.which(command[0]) or command[0]
            client = self._client_factory(command, timeout=self._timeout)
            try:
                client.start()
            except (OSError, MCPProtocolError, TimeoutError) as exc:
                client.close()
                raise MCPGrantError(
                    f"MCP server {name!r} could not be started: {exc}"
                ) from exc
            self._clients[name] = client
        return self._clients[name]

    def tools(self, name: str) -> list[dict[str, Any]]:
        """What the server offers: name, description, input schema, and
        whether the server itself says the tool only reads."""

        with self._lock:
            if name not in self._tools:
                try:
                    response = self._client(name).request("tools/list", {})
                except (MCPProtocolError, TimeoutError, OSError) as exc:
                    self._drop(name)
                    raise MCPGrantError(
                        f"MCP server {name!r} did not list its tools: {exc}"
                    ) from exc
                listed = (response.get("result") or {}).get("tools")
                if not isinstance(listed, list):
                    raise MCPGrantError(f"MCP server {name!r} listed no tools")
                found = []
                for item in listed:
                    if not isinstance(item, dict) or not isinstance(
                        item.get("name"), str
                    ):
                        continue
                    hints = item.get("annotations")
                    found.append(
                        {
                            "name": item["name"],
                            "description": str(item.get("description", "")),
                            "input_schema": item.get("inputSchema") or {},
                            "read_only": isinstance(hints, dict)
                            and hints.get("readOnlyHint") is True,
                        }
                    )
                self._tools[name] = found
            return self._tools[name]

    def tool(self, name: str, operation: str) -> dict[str, Any]:
        for tool in self.tools(name):
            if tool["name"] == operation:
                return tool
        raise MCPGrantError(f"MCP server {name!r} has no tool named {operation!r}")

    def call(self, name: str, operation: str, arguments: dict[str, Any]) -> Any:
        with self._lock:
            self.tool(name, operation)
            # An absent optional field is left out, not sent as null.
            sent = {key: value for key, value in arguments.items() if value is not None}
            try:
                response = self._client(name).request(
                    "tools/call", {"name": operation, "arguments": sent}
                )
            except (MCPProtocolError, TimeoutError, OSError) as exc:
                self._drop(name)
                raise MCPToolError(
                    f"MCP tool {operation!r} on {name!r} failed: {exc}"
                ) from exc
        result = response.get("result")
        if not isinstance(result, dict):
            raise MCPToolError(f"MCP tool {operation!r} gave no result")
        if result.get("isError") is True:
            raise MCPToolError(
                f"MCP tool {operation!r} reported an error: "
                f"{_text_of(result.get('content'))[:500]}"
            )
        if "structuredContent" in result:
            return result["structuredContent"]
        content = result.get("content", [])
        if (
            isinstance(content, list)
            and len(content) == 1
            and isinstance(content[0], dict)
            and content[0].get("type") == "text"
        ):
            text = content[0].get("text", "")
            try:
                return json.loads(text)
            except (TypeError, json.JSONDecodeError):
                return text
        return content

    def _drop(self, name: str) -> None:
        client = self._clients.pop(name, None)
        self._tools.pop(name, None)
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001 - it is already being discarded
                pass

    def close(self) -> None:
        with self._lock:
            for name in list(self._clients):
                self._drop(name)


class MCPToolCall:
    """One declared tool, bound to one granted server."""

    def __init__(self, hub: MCPHub, program: Program, tool: ToolDecl) -> None:
        self.hub, self.tool = hub, tool
        self.output_schema = schema_for_record(program, tool.output)

    def describe(self) -> str:
        """The server's own words for the tool, for a model that chooses."""

        offered = self.hub.tool(self.tool.source, self.tool.operation)
        asks = (
            " The person is asked before it happens."
            if self.tool.access == "execute"
            else ""
        )
        return (offered["description"].strip() or f"Tool {offered['name']}.")[
            :600
        ] + asks

    def __call__(self, _tool: ToolDecl, arguments: dict[str, Any]) -> dict[str, Any]:
        offered = self.hub.tool(self.tool.source, self.tool.operation)
        if self.tool.access != "execute" and not offered["read_only"]:
            raise MCPGrantError(
                f"Tool {self.tool.name!r} is declared access: observe, but MCP "
                f"server {self.tool.source!r} does not mark "
                f"{self.tool.operation!r} as read-only. Declare it access: "
                "execute, so that every call waits for approval."
            )
        value = self.hub.call(self.tool.source, self.tool.operation, arguments)
        return shape(value, self.output_schema, self.tool.name)


def bind_mcp_tools(program: Program, hub: MCPHub | None) -> dict[str, MCPToolCall]:
    """Bind the program's tools whose source is a granted MCP server."""

    if hub is None:
        return {}
    return {
        tool.name: MCPToolCall(hub, program, tool)
        for tool in program.tools
        if tool.source in hub.servers
    }


_KINDS = {"string": "Text", "number": "Number", "integer": "Number", "boolean": "Bool"}


def _camel(name: str) -> str:
    words = "".join(ch if ch.isalnum() else " " for ch in name).split()
    return "".join(word[:1].upper() + word[1:] for word in words) or "Tool"


def _snake(name: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in name).strip("_")
    return cleaned if cleaned[:1].isalpha() else "tool_" + cleaned


def declaration(server: str, tool: Mapping[str, Any]) -> str:
    """Kosika text that declares this server tool, ready to paste.

    Fields whose type Kosika cannot express are left out and named in a
    comment, so the text always checks.
    """

    base = _camel(tool["name"])
    schema = tool.get("input_schema") or {}
    required = set(schema.get("required") or ())
    fields, skipped = [], []
    for name, kind in (schema.get("properties") or {}).items():
        if not isinstance(kind, dict) or not name.replace("_", "").isalnum():
            skipped.append(name)
            continue
        if kind.get("type") in _KINDS:
            written = _KINDS[kind["type"]]
        elif (
            kind.get("type") == "array"
            and isinstance(kind.get("items"), dict)
            and kind["items"].get("type") in _KINDS
        ):
            written = _KINDS[kind["items"]["type"]] + "[]"
        else:
            skipped.append(name)
            continue
        fields.append(f"{name}: {written}{'' if name in required else '?'}")
    lines = []
    if skipped:
        lines.append(
            "// Left out, because Kosika has no type for them: " + ", ".join(skipped)
        )
    lines += [
        f"record {base}In {{ {' '.join(fields)} }}",
        f"record {base}Out {{ text: Text }}",
        "",
        f"tool {_snake(tool['name'])} {{",
        f'  source mcp "{server}"',
        f'  operation: "{tool["name"]}"',
        f"  access: {'observe' if tool.get('read_only') else 'execute'}",
        f"  input: {base}In",
        f"  output: {base}Out",
        "}",
    ]
    return "\n".join(lines)
