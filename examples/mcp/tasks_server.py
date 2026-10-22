"""A small MCP task list stored in one JSON file.

    python tasks_server.py PATH_TO_TASKS.json

Reading tasks is safe. Adding or completing a task changes the file and
must be declared access: execute in Kosika so Studio requires approval.
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

MAX_TASKS = 500
MAX_TITLE_LENGTH = 500
MAX_RESPONSE_LENGTH = 20_000
STORE = Path(sys.argv[1]) if len(sys.argv) > 1 else None

TOOLS = [
    {
        "name": "list_tasks",
        "description": "List saved tasks, including completed tasks.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "search_tasks",
        "description": "Find tasks containing all words in the query.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "add_task",
        "description": "Add a task with an optional due date in YYYY-MM-DD form.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "due": {"type": "string"},
            },
            "required": ["title"],
        },
        "annotations": {"readOnlyHint": False},
    },
    {
        "name": "complete_task",
        "description": "Mark one task complete by its task id.",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
        "annotations": {"readOnlyHint": False},
    },
]


def load_tasks() -> list[dict[str, object]]:
    if STORE is None:
        raise ValueError("start this server with a path to its tasks JSON file")
    try:
        content = STORE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    try:
        tasks = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"tasks file is not valid JSON: {STORE}") from exc
    if not isinstance(tasks, list) or len(tasks) > MAX_TASKS:
        raise ValueError(f"tasks file must be a list of at most {MAX_TASKS} tasks")
    for task in tasks:
        if (
            not isinstance(task, dict)
            or not isinstance(task.get("id"), str)
            or not isinstance(task.get("title"), str)
            or not isinstance(task.get("completed"), bool)
        ):
            raise ValueError("tasks file contains an invalid task record")
    return tasks


def save_tasks(tasks: list[dict[str, object]]) -> None:
    if STORE is None:
        raise ValueError("start this server with a path to its tasks JSON file")
    STORE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STORE.with_name(f".{STORE.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(tasks, indent=2) + "\n", encoding="utf-8")
        temporary.replace(STORE)
    finally:
        temporary.unlink(missing_ok=True)


def shown(tasks: list[dict[str, object]]) -> str:
    if not tasks:
        return "No tasks."
    rows = []
    for task in tasks:
        status = "done" if task["completed"] else "open"
        due = f" (due {task['due']})" if task.get("due") else ""
        rows.append(f"{task['id']}: [{status}] {task['title']}{due}")
    text = "\n".join(rows)
    return text if len(text) <= MAX_RESPONSE_LENGTH else text[:MAX_RESPONSE_LENGTH] + "\n[cut]"


def call(name: str, arguments: dict) -> str:
    tasks = load_tasks()
    if name == "list_tasks":
        return shown(tasks)
    if name == "search_tasks":
        words = str(arguments.get("query", "")).lower().split()
        return shown(
            [
                task
                for task in tasks
                if all(word in str(task["title"]).lower() for word in words)
            ]
        )
    if name == "add_task":
        title = arguments.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("a task needs a title")
        title = title.strip()
        if len(title) > MAX_TITLE_LENGTH:
            raise ValueError(f"task titles must be at most {MAX_TITLE_LENGTH} characters")
        if len(tasks) >= MAX_TASKS:
            raise ValueError(f"task list is limited to {MAX_TASKS} items")
        due = arguments.get("due")
        if due is not None:
            if not isinstance(due, str):
                raise ValueError("due must be a date in YYYY-MM-DD form")
            try:
                date.fromisoformat(due)
            except ValueError as exc:
                raise ValueError("due must be a date in YYYY-MM-DD form") from exc
        task: dict[str, object] = {
            "id": uuid.uuid4().hex[:12],
            "title": title,
            "completed": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if due:
            task["due"] = due
        tasks.append(task)
        save_tasks(tasks)
        return f"Added task {task['id']}: {title}"
    if name == "complete_task":
        task_id = arguments.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id is required")
        for task in tasks:
            if task["id"] == task_id:
                if task["completed"]:
                    return f"Task {task_id} is already complete."
                task["completed"] = True
                save_tasks(tasks)
                return f"Completed task {task_id}."
        raise ValueError(f"No task with id {task_id!r}.")
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
            "serverInfo": {"name": "tasks", "version": "1.0"},
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
