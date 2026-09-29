from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

SINK_ENV = "KOSIKA_MUTATION_SINK"


def send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def apply_change(arguments: dict[str, Any]) -> dict[str, str]:
    change = arguments["change"]
    sink_name = os.environ.get(SINK_ENV, "mutation_sink.json")
    sink = Path(sink_name)
    if sink.name != sink_name or sink.suffix != ".json":
        raise ValueError("Mutation sink must be a relative .json filename")
    sink.write_text(
        json.dumps({"change": change, "status": "applied"}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"status": "applied", "change": change, "sink": sink_name}


def tool_definition() -> dict[str, Any]:
    return {
        "name": "apply_change",
        "description": "Apply one reviewed change to the deterministic local sink.",
        "inputSchema": {
            "type": "object",
            "properties": {"change": {"type": "string"}},
            "required": ["change"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "change": {"type": "string"},
                "sink": {"type": "string"},
            },
            "required": ["status", "change", "sink"],
            "additionalProperties": False,
        },
    }


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
                            "name": "kosika-approval-mutation",
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
                    "result": {"tools": [tool_definition()]},
                }
            )
        elif method == "tools/call":
            params = request["params"]
            if params.get("name") != "apply_change":
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
            try:
                payload = apply_change(params.get("arguments", {}))
            except (KeyError, OSError, ValueError) as exc:
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "isError": True,
                            "content": [{"type": "text", "text": str(exc)}],
                        },
                    }
                )
                continue
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
