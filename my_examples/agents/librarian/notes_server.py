"""A small MCP server: notes kept in one JSON file.

It offers three tools: search_notes and list_notes only read, add_note
changes the file. It speaks the Model Context Protocol over standard input
and output, using nothing outside Python's standard library.

    python notes_server.py NOTES.json

Grant it to Kosika Studio with:

    --mcp notes="python PATH/notes_server.py PATH/notes.json"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

STORE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("notes.json")

TOOLS = [
    {
        "name": "search_notes",
        "description": "Find notes that contain all the words in `query`. "
        "Returns each matching note with its number.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "list_notes",
        "description": "List every note with its number, newest last.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "add_note",
        "description": "Save a new note. `text` is the note; `tag` is an "
        "optional single word to file it under.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "tag": {"type": "string"}},
            "required": ["text"],
        },
        "annotations": {"readOnlyHint": False},
    },
]


def load() -> list[dict[str, str]]:
    try:
        notes = json.loads(STORE.read_text(encoding="utf-8"))
        return notes if isinstance(notes, list) else []
    except (OSError, ValueError):
        return []


def shown(numbered: list[tuple[int, dict[str, str]]]) -> str:
    if not numbered:
        return "No notes."
    return "\n".join(
        f"{number}. {note.get('text', '')}"
        + (f" [{note['tag']}]" if note.get("tag") else "")
        for number, note in numbered
    )


def call(name: str, arguments: dict) -> str:
    notes = load()
    numbered = list(enumerate(notes, 1))
    if name == "list_notes":
        return shown(numbered)
    if name == "search_notes":
        words = str(arguments.get("query", "")).lower().split()
        return shown(
            [
                (number, note)
                for number, note in numbered
                if all(
                    word in (note.get("text", "") + " " + note.get("tag", "")).lower()
                    for word in words
                )
            ]
        )
    if name == "add_note":
        text = str(arguments.get("text", "")).strip()
        if not text:
            raise ValueError("a note needs some text")
        note = {"text": text}
        if arguments.get("tag"):
            note["tag"] = str(arguments["tag"]).strip()
        notes.append(note)
        STORE.write_text(json.dumps(notes, indent=1), encoding="utf-8")
        return f"Saved as note {len(notes)}."
    raise ValueError(f"no tool named {name}")


def answer(message: dict) -> dict | None:
    method, identifier = message.get("method"), message.get("id")
    if identifier is None:
        return None  # a notification needs no answer
    if method == "initialize":
        result: dict = {
            "protocolVersion": message.get("params", {}).get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "notes", "version": "1.0"},
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = message.get("params", {})
        try:
            text = call(params.get("name", ""), params.get("arguments") or {})
            result = {"content": [{"type": "text", "text": text}]}
        except ValueError as exc:
            result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    else:
        return {
            "jsonrpc": "2.0",
            "id": identifier,
            "error": {"code": -32601, "message": f"unknown method {method}"},
        }
    return {"jsonrpc": "2.0", "id": identifier, "result": result}


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        reply = answer(json.loads(line))
    except ValueError:
        continue
    if reply is not None:
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()
