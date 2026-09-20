from __future__ import annotations

import json
import sys


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


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
                        "serverInfo": {"name": "kosika-test-mcp", "version": "0.1.0"},
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
                                "name": "get_disk_info",
                                "description": "Return deterministic test disk information.",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {},
                                    "additionalProperties": False,
                                },
                                "outputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "mount": {"type": "string"},
                                        "used_percent": {"type": "number"},
                                    },
                                    "required": ["mount", "used_percent"],
                                    "additionalProperties": False,
                                },
                            }
                        ]
                    },
                }
            )
        elif method == "tools/call":
            params = request["params"]
            if params.get("name") != "get_disk_info":
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
            payload = {"mount": "/", "used_percent": 42.0}
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
