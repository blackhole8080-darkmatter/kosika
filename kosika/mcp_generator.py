"""MCP Server Declaration and Generation System for Kosika.

This system makes it easy to declare and generate MCP (Model Context Protocol) servers
directly from Kosika code. MCP servers expose tools and capabilities to AI agents.

Key features:
- Declarative MCP server definition
- Automatic schema generation
- Type-safe tool declarations
- Server generation for multiple languages (Python, TypeScript)
- Built-in MCP server templates
- Validation and testing
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class MCPToolAccess(Enum):
    """Access levels for MCP tools."""
    OBSERVE = "observe"  # Read-only access
    EXECUTE = "execute"  # Read and write access


class MCPToolKind(Enum):
    """Types of MCP tools."""
    FUNCTION = "function"  # Function call
    RESOURCE = "resource"  # Resource access
    PROMPT = "prompt"  # Prompt template


@dataclass
class MCPToolParameter:
    """A parameter for an MCP tool."""
    name: str
    type: str  # string, number, boolean, array, object
    description: str = ""
    required: bool = True
    default: Any = None
    enum: Optional[List[Any]] = None


@dataclass
class MCPTool:
    """A tool exposed by an MCP server."""
    name: str
    description: str
    kind: MCPToolKind
    access: MCPToolAccess
    parameters: List[MCPToolParameter] = field(default_factory=list)
    returns: str = "string"  # Return type
    implementation: Optional[str] = None  # Code implementation
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_schema(self) -> Dict[str, Any]:
        """Convert to MCP tool schema."""
        properties: Dict[str, Dict[str, Any]] = {}
        required: List[str] = []

        for param in self.parameters:
            prop: Dict[str, Any] = {
                "type": param.type,
                "description": param.description,
            }
            if param.default is not None:
                prop["default"] = param.default
            if param.enum:
                prop["enum"] = param.enum
            properties[param.name] = prop

            if param.required:
                required.append(param.name)

        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        }


@dataclass
class MCPResource:
    """A resource exposed by an MCP server."""
    uri: str
    name: str
    description: str
    mime_type: str = "text/plain"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPServer:
    """An MCP server declaration."""
    id: str
    name: str
    description: str
    version: str = "1.0.0"
    tools: List[MCPTool] = field(default_factory=list)
    resources: List[MCPResource] = field(default_factory=list)
    capabilities: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)

    def add_tool(self, tool: MCPTool):
        """Add a tool to the server."""
        self.tools.append(tool)

    def add_resource(self, resource: MCPResource):
        """Add a resource to the server."""
        self.resources.append(resource)

    def to_manifest(self) -> Dict[str, Any]:
        """Generate MCP server manifest."""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "capabilities": {
                "tools": {
                    "listChanged": True,
                },
                "resources": {
                    "subscribe": True,
                    "listChanged": True,
                },
            },
            "tools": [tool.to_schema() for tool in self.tools],
            "resources": [
                {
                    "uri": r.uri,
                    "name": r.name,
                    "description": r.description,
                    "mimeType": r.mime_type,
                }
                for r in self.resources
            ],
        }

    def generate_python_server(self) -> str:
        """Generate Python MCP server code."""
        tools_code = []

        for tool in self.tools:
            params = []
            for param in tool.parameters:
                param_def = f"    {param.name}: {self._python_type(param.type)}"
                if not param.required:
                    param_def += " = None"
                params.append(param_def)

            params_str = ",\n".join(params)

            tools_code.append(f'''
    @mcp.tool()
    async def {tool.name}(
{params_str}
    ) -> str:
        """{tool.description}"""
        # TODO: Implement {tool.name}
        return "Not implemented"
''')

        code = f'''"""
MCP Server: {self.name}
Generated from Kosika MCP declaration
"""

from mcp import FastMCP

mcp = FastMCP("{self.name}")

{''.join(tools_code)}

if __name__ == "__main__":
    mcp.run()
'''
        return code

    def generate_typescript_server(self) -> str:
        """Generate TypeScript MCP server code."""
        tools_code = []

        for tool in self.tools:
            params = []
            for param in tool.parameters:
                param_def = f"    {param.name}: {self._ts_type(param.type)}"
                if not param.required:
                    param_def += "?"
                params.append(param_def)

            tools_code.append(f'''
  server.addTool({{
    name: "{tool.name}",
    description: "{tool.description}",
    inputSchema: {{
      type: "object",
      properties: {{
{self._ts_properties(tool.parameters)}
      }},
      required: {self._ts_required(tool.parameters)},
    }},
  }}, async (input: any) => {{
    // TODO: Implement {tool.name}
    return {{ content: [{{ type: "text", text: "Not implemented" }}] }};
  }});
''')

        code = f'''/**
 * MCP Server: {self.name}
 * Generated from Kosika MCP declaration
 */

import {{ Server }} from "@modelcontextprotocol/sdk/server/index.js";
import {{ StdioServerTransport }} from "@modelcontextprotocol/sdk/server/stdio.js";

const server = new Server({{
  name: "{self.name}",
  version: "{self.version}",
}}, {{
  capabilities: {{
    tools: {{}},
  }},
}},

{''.join(tools_code)}

async function main() {{
  const transport = new StdioServerTransport();
  await server.connect(transport);
}}

main().catch(console.error);
'''
        return code

    def _python_type(self, mcp_type: str) -> str:
        """Map MCP type to Python type."""
        type_map = {
            "string": "str",
            "number": "float",
            "boolean": "bool",
            "array": "list",
            "object": "dict",
        }
        return type_map.get(mcp_type, "Any")

    def _ts_type(self, mcp_type: str) -> str:
        """Map MCP type to TypeScript type."""
        type_map = {
            "string": "string",
            "number": "number",
            "boolean": "boolean",
            "array": "any[]",
            "object": "Record<string, any>",
        }
        return type_map.get(mcp_type, "any")

    def _ts_properties(self, parameters: List[MCPToolParameter]) -> str:
        """Generate TypeScript properties schema."""
        props = []
        for param in parameters:
            prop = f'        "{param.name}": {{ type: "{param.type}"'
            if param.description:
                prop += f', description: "{param.description}"'
            if param.default is not None:
                prop += f', default: {json.dumps(param.default)}'
            if param.enum:
                prop += f', enum: {json.dumps(param.enum)}'
            prop += " },"
            props.append(prop)
        return "\n".join(props)

    def _ts_required(self, parameters: List[MCPToolParameter]) -> str:
        """Generate TypeScript required array."""
        required = [p.name for p in parameters if p.required]
        return json.dumps(required)


class MCPGenerator:
    """Generator for MCP servers from Kosika declarations."""

    def __init__(self):
        self.servers: Dict[str, MCPServer] = {}

    def create_server(
        self,
        id: str,
        name: str,
        description: str,
        version: str = "1.0.0",
    ) -> MCPServer:
        """Create a new MCP server."""
        server = MCPServer(
            id=id,
            name=name,
            description=description,
            version=version,
        )
        self.servers[id] = server
        return server

    def get_server(self, server_id: str) -> Optional[MCPServer]:
        """Get an MCP server by ID."""
        return self.servers.get(server_id)

    def generate_manifest(self, server_id: str) -> str:
        """Generate MCP manifest for a server."""
        server = self.get_server(server_id)
        if not server:
            raise ValueError(f"Server not found: {server_id}")
        return json.dumps(server.to_manifest(), indent=2)

    def generate_python(self, server_id: str) -> str:
        """Generate Python server code."""
        server = self.get_server(server_id)
        if not server:
            raise ValueError(f"Server not found: {server_id}")
        return server.generate_python_server()

    def generate_typescript(self, server_id: str) -> str:
        """Generate TypeScript server code."""
        server = self.get_server(server_id)
        if not server:
            raise ValueError(f"Server not found: {server_id}")
        return server.generate_typescript_server()

    def save_server(self, server_id: str, output_dir: str, language: str = "python"):
        """Save generated server code to file."""
        server = self.get_server(server_id)
        if not server:
            raise ValueError(f"Server not found: {server_id}")

        import os

        os.makedirs(output_dir, exist_ok=True)

        if language == "python":
            code = self.generate_python(server_id)
            filename = f"{server.name.lower().replace(' ', '_')}.py"
            filepath = os.path.join(output_dir, filename)
            with open(filepath, "w") as f:
                f.write(code)
        elif language == "typescript":
            code = self.generate_typescript(server_id)
            filename = f"{server.name.lower().replace(' ', '_')}.ts"
            filepath = os.path.join(output_dir, filename)
            with open(filepath, "w") as f:
                f.write(code)
        else:
            raise ValueError(f"Unsupported language: {language}")

        return filepath


# Global MCP generator
_global_generator: Optional[MCPGenerator] = None


def get_mcp_generator() -> MCPGenerator:
    """Get the global MCP generator."""
    global _global_generator
    if _global_generator is None:
        _global_generator = MCPGenerator()
    return _global_generator


def create_mcp_tool(
    name: str,
    description: str,
    access: MCPToolAccess = MCPToolAccess.OBSERVE,
    parameters: Optional[List[Dict[str, Any]]] = None,
    returns: str = "string",
) -> MCPTool:
    """Helper function to create an MCP tool."""
    params = []
    if parameters:
        for p in parameters:
            params.append(
                MCPToolParameter(
                    name=p["name"],
                    type=p["type"],
                    description=p.get("description", ""),
                    required=p.get("required", True),
                    default=p.get("default"),
                    enum=p.get("enum"),
                )
            )

    return MCPTool(
        name=name,
        description=description,
        kind=MCPToolKind.FUNCTION,
        access=access,
        parameters=params,
        returns=returns,
    )
