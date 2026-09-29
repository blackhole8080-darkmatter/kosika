"""Small deterministic MCP server. Reads supplied text; never files or network."""

import json
import sys

PROTOCOL = "2025-11-25"
TOOL = {
    "name": "inspect_text",
    "description": "Count whitespace-separated words and Unicode characters in supplied text.",
    "inputSchema": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    },
    "outputSchema": {
        "type": "object",
        "properties": {"words": {"type": "number"}, "characters": {"type": "number"}},
        "required": ["words", "characters"],
        "additionalProperties": False,
    },
}


def handle(message):
    method = message.get("method")
    if method == "initialize":
        return {
            "protocolVersion": PROTOCOL,
            "serverInfo": {"name": "kosika-text-lab", "version": "1.0"},
            "capabilities": {"tools": {}},
        }
    if method == "tools/list":
        return {"tools": [TOOL]}
    if method == "tools/call":
        params = message.get("params", {})
        args = params.get("arguments", {})
        if (
            params.get("name") != "inspect_text"
            or not isinstance(args, dict)
            or set(args) != {"text"}
            or not isinstance(args["text"], str)
            or len(args["text"]) > 65536
        ):
            raise ValueError(
                "Expected inspect_text with a text argument under 64 KiB characters."
            )
        result = {"words": len(args["text"].split()), "characters": len(args["text"])}
        return {
            "structuredContent": result,
            "content": [{"type": "text", "text": json.dumps(result)}],
            "isError": False,
        }
    raise ValueError("Unknown MCP method.")


def main():
    while True:
        line = sys.stdin.readline(1048577)
        if not line:
            return
        if len(line) > 1048576:
            return
        message = json.loads(line)
        if not isinstance(message, dict) or "id" not in message:
            continue
        response = {"jsonrpc": "2.0", "id": message["id"]}
        try:
            response["result"] = handle(message)
        except (ValueError, TypeError, KeyError) as exc:
            response["error"] = {"code": -32602, "message": str(exc)}
        print(json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
