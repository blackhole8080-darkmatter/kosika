"""Host-granted MCP demo integration. No browser-supplied process commands."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .ast import Program, ToolDecl
from .manifest import (
    MCPManifest,
    PinnedTool,
    manifest_to_dict,
    schema_hash,
    verify_server,
)
from .mcp_stdio import MCP_PROTOCOL_VERSION, MCPStdioClient
from .schema import schema_for_record, schema_for_tool_input, schemas_equal

INPUT = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}
OUTPUT = {
    "type": "object",
    "properties": {"words": {"type": "number"}, "characters": {"type": "number"}},
    "required": ["words", "characters"],
    "additionalProperties": False,
}


class DemoMCP:
    source = "workbench_demo"

    def __init__(self) -> None:
        self.command = (
            sys.executable,
            "-B",
            str(Path(__file__).with_name("workbench_mcp_server.py")),
        )
        self.manifest = MCPManifest(
            self.source,
            MCP_PROTOCOL_VERSION,
            "kosika-text-lab",
            "1.0",
            {
                "inspect_text": PinnedTool(
                    "inspect_text", INPUT, OUTPUT, schema_hash(INPUT, OUTPUT)
                )
            },
        )

    def permits(self, program: Program, tool: ToolDecl) -> bool:
        pin = self.manifest.tools.get(tool.operation)
        return (
            tool.source == self.source
            and tool.access == "observe"
            and pin is not None
            and schemas_equal(
                schema_for_tool_input(program, tool.input), pin.input_schema
            )
            and schemas_equal(
                schema_for_record(program, tool.output), pin.output_schema
            )
        )

    def _verify(self, client: MCPStdioClient) -> None:
        verify_server(client, self.manifest)
        # Enforce exact discovery, including output schemas and duplicate tool names.
        tools = client.list_tools()
        if len(tools) != len(self.manifest.tools) or len(
            {t.name for t in tools}
        ) != len(tools):
            raise ValueError("MCP discovery contains duplicate or unexpected tools.")
        for tool in tools:
            pin = self.manifest.tools.get(tool.name)
            if (
                pin is None
                or tool.input_schema != pin.input_schema
                or tool.output_schema != pin.output_schema
            ):
                raise ValueError("MCP manifest drift detected. Execution denied.")

    def inspect(self) -> dict[str, Any]:
        client = MCPStdioClient(self.command, timeout=3)
        try:
            client.start()
            self._verify(client)
            return {
                "status": "verified",
                "transport": "stdio",
                "manifest": manifest_to_dict(self.manifest),
            }
        finally:
            client.close()

    def execute(self, tool: ToolDecl, arguments: dict[str, Any]) -> Any:
        if (
            tool.source != self.source
            or tool.access != "observe"
            or tool.operation not in self.manifest.tools
        ):
            raise PermissionError("MCP tool is outside the host grant.")
        pin = self.manifest.tools[tool.operation]
        client = MCPStdioClient(self.command, timeout=3)
        try:
            client.start()
            self._verify(client)
            return client.call_tool(
                tool.operation,
                arguments,
                input_schema=pin.input_schema,
                output_schema=pin.output_schema,
            )
        finally:
            client.close()

    def public(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "name": "Text lab",
            "transport": "stdio",
            "status": "configured",
            "manifest": manifest_to_dict(self.manifest),
        }


DEMO_SAMPLE = {
    "id": "mcp-text",
    "name": "MCP text inspector",
    "description": "Call a pinned local MCP server over stdio. Only supplied text is inspected.",
    "entry": "main.kos",
    "workflow": "main",
    "files": {
        "main.kos": """record Document { text: Text }
record Statistics { words: Number characters: Number }
tool inspect_text {
  source mcp "workbench_demo"
  operation: "inspect_text"
  access: observe
  input: Document
  output: Statistics
}
agent inspector {
  can: [inspect_text]
  cannot: [arbitrary_shell, write_files]
}
workflow main(document: Document) -> Statistics {
  step measure {
    let result: Statistics = execute inspector.inspect_text(text: document.text)
    return result
  }
}
"""
    },
    "inputs": {"document": {"text": "Small programs. Clear boundaries."}},
    "fixtures": {},
}
