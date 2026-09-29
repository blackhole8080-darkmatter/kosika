"""MCP stdio server exposing a bounded document search tool.

Serves the ``search`` operation for the ``documents`` source: a read-only
keyword search over a bounded documents directory. It defaults to the local
``docs`` fixture and accepts ``KOSIKA_DOCUMENTS_DIR`` for another host-granted
fixture directory.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DOCUMENTS_DIR = Path(
    os.environ.get("KOSIKA_DOCUMENTS_DIR", str(Path(__file__).parent / "docs"))
)
MAX_HITS = 8
MAX_EXCERPT_CHARS = 200


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def search_documents(query: str) -> dict:
    """Return bounded search hits over the documents directory."""

    terms = [term for term in query.lower().split() if term]
    hits: list[dict] = []
    if terms and DOCUMENTS_DIR.is_dir():
        for path in sorted(DOCUMENTS_DIR.glob("*.md")):
            text = path.read_text(encoding="utf-8", errors="replace")
            lowered = text.lower()
            if any(term in lowered for term in terms):
                start = 0
                for term in terms:
                    index = lowered.find(term)
                    if index != -1:
                        start = max(0, index - 40)
                        break
                excerpt = text[start : start + MAX_EXCERPT_CHARS].strip()
                hits.append({"source": path.name, "excerpt": excerpt})
                if len(hits) >= MAX_HITS:
                    break
    return {"hits": hits}


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
                            "name": "kosika-documents-mcp",
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
                                "name": "search",
                                "description": "Search the documents directory by keyword.",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "query": {"type": "string"},
                                    },
                                    "required": ["query"],
                                    "additionalProperties": False,
                                },
                                "outputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "hits": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "source": {"type": "string"},
                                                    "excerpt": {"type": "string"},
                                                },
                                                "required": ["source", "excerpt"],
                                                "additionalProperties": False,
                                            },
                                        }
                                    },
                                    "required": ["hits"],
                                    "additionalProperties": False,
                                },
                            }
                        ]
                    },
                }
            )
        elif method == "tools/call":
            name = request["params"]["name"]
            if name != "search":
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32601, "message": f"Unknown tool {name!r}"},
                    }
                )
                continue
            arguments = request["params"].get("arguments", {})
            query = arguments.get("query", "")
            result = search_documents(query)
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"content": [], "structuredContent": result},
                }
            )
        else:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"Unknown method {method!r}"},
                }
            )


if __name__ == "__main__":
    main()
