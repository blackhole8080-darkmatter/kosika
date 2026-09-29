from __future__ import annotations

import json
import sys


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def source_for(arguments: dict) -> str:
    name = arguments["name"]
    model = arguments["model"]
    can = arguments["can"]
    cannot = arguments["cannot"]
    max_steps = arguments["max_steps"]
    can_text = ", ".join(can)
    cannot_text = ", ".join(cannot)
    return (
        f"agent {name} {{\n"
        f"  model: {model}\n"
        f"  can: [{can_text}]\n"
        f"  cannot: [{cannot_text}]\n"
        f"  max_steps: {max_steps:g}\n"
        "}\n"
    )


def main() -> None:
    for raw_line in sys.stdin:
        if not raw_line.strip():
            continue
        request = json.loads(raw_line)
        method = request.get("method")
        request_id = request.get("id")
        if method == "initialize":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": request["params"].get("protocolVersion"),
                        "capabilities": {"tools": {}},
                        "serverInfo": {
                            "name": "kosika-agent-factory",
                            "version": "0.1.0",
                        },
                    },
                }
            )
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "tools": [
                            {
                                "name": "write_source",
                                "description": "Render a proposed AgentSpec as reviewable Kosika source.",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "model": {"type": "string"},
                                        "can": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                        "cannot": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                        "max_steps": {"type": "number"},
                                    },
                                    "required": [
                                        "name",
                                        "model",
                                        "can",
                                        "cannot",
                                        "max_steps",
                                    ],
                                    "additionalProperties": False,
                                },
                                "outputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "source": {"type": "string"},
                                        "status": {"type": "string"},
                                        "action": {"type": "string"},
                                    },
                                    "required": ["source", "status", "action"],
                                    "additionalProperties": False,
                                },
                            }
                        ]
                    },
                }
            )
        elif method == "tools/call":
            params = request["params"]
            if params.get("name") != "write_source":
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "isError": True,
                            "content": [{"type": "text", "text": "unknown tool"}],
                        },
                    }
                )
                continue
            arguments = params.get("arguments", {})
            payload = {
                "source": source_for(arguments),
                "status": "proposed; no file was written",
                "action": "Review the generated source before writing it.",
            }
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "isError": False,
                        "structuredContent": payload,
                        "content": [{"type": "text", "text": json.dumps(payload)}],
                    },
                }
            )
        elif request_id is not None:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"unknown method: {method}"},
                }
            )


if __name__ == "__main__":
    main()
