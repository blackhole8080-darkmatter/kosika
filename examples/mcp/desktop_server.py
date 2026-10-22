"""A narrowly scoped MCP server for opening a few known desktop apps.

    python desktop_server.py

Grant it to Kosika Studio by name. The only launchable applications are
the identifiers returned by list_apps; arbitrary commands and arguments
are never accepted.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable

TOOLS = [
    {
        "name": "list_apps",
        "description": "List supported desktop applications that can be opened.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "open_app",
        "description": "Open one supported application by its identifier. "
        "This launches an app and should only be used when the user asks.",
        "inputSchema": {
            "type": "object",
            "properties": {"app": {"type": "string"}},
            "required": ["app"],
        },
        "annotations": {"readOnlyHint": False},
    },
]

APP_CANDIDATES = {
    "Windows": {
        "notepad": [("notepad.exe",)],
        "calculator": [("calc.exe",)],
    },
    "Darwin": {
        "text_editor": [("open", "-a", "TextEdit")],
        "calculator": [("open", "-a", "Calculator")],
    },
    "Linux": {
        "text_editor": [("gedit",), ("kate",), ("mousepad",)],
        "calculator": [("gnome-calculator",), ("kcalc",)],
    },
}


def _system_name() -> str:
    if sys.platform.startswith("win"):
        return "Windows"
    if sys.platform == "darwin":
        return "Darwin"
    return "Linux"


def _resolve_app(
    app: str,
    system: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[str, ...]:
    candidates = APP_CANDIDATES.get(system or _system_name(), {})
    if app not in candidates:
        raise ValueError(f"Unsupported app {app!r}; use an identifier from list_apps.")
    for command in candidates[app]:
        if which(command[0]):
            return command
    raise ValueError(f"No installed command is available to open {app!r}.")


def call(
    name: str,
    arguments: dict,
    system: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    popen: Callable[..., object] = subprocess.Popen,
) -> str:
    if name == "list_apps":
        available = []
        for app in APP_CANDIDATES.get(system or _system_name(), {}):
            try:
                _resolve_app(app, system, which)
            except ValueError:
                continue
            available.append(app)
        return "Supported apps: " + (", ".join(available) if available else "none")
    if name == "open_app":
        app = arguments.get("app")
        if not isinstance(app, str):
            raise ValueError("app must be an identifier returned by list_apps")
        command = _resolve_app(app, system, which)
        popen(list(command), shell=False)
        return f"Opened {app}."
    raise ValueError(f"No tool named {name!r}.")


def answer(message: dict) -> dict | None:
    method, identifier = message.get("method"), message.get("id")
    if identifier is None:
        return None
    if method == "initialize":
        result: dict = {
            "protocolVersion": message.get("params", {}).get(
                "protocolVersion", "2025-06-18"
            ),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "desktop", "version": "1.0"},
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = message.get("params", {})
        try:
            text = call(params.get("name", ""), params.get("arguments") or {})
            result = {"content": [{"type": "text", "text": text}]}
        except (OSError, ValueError) as exc:
            result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    else:
        return {
            "jsonrpc": "2.0",
            "id": identifier,
            "error": {"code": -32601, "message": f"unknown method {method}"},
        }
    return {"jsonrpc": "2.0", "id": identifier, "result": result}


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            reply = answer(json.loads(line))
        except (json.JSONDecodeError, AttributeError, TypeError):
            continue
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
