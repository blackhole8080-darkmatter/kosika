"""The Kosika app: look at your agents, test them, run them, read their runs.

    kosika app FOLDER [--model MODEL] [--allow-folder FOLDER]

It opens a page in your browser, served from this machine only. The page
shows every agent found under FOLDER: what it does, which models it asks,
what it remembers, which tools it may use and whether any change the world,
its workflow step by step, its tests, and its past runs told in plain words.

The app has exactly the authority of the command that started it, and no
more. It can only read `.kos` files under FOLDER. It can run an agent only
with the model named on the command line; without `--model` nothing can be
run, only viewed and tested. Tests use saved answers and reach no model. A
run uses the agent's own `memory` folder for its memories, may look inside
`--allow-folder` if one was given, and can never write a file or start a
program: tools that change the world are not bound here.

It listens on 127.0.0.1 only. Every request must carry the random token that
was printed when the app started, and must be addressed to this machine, so
another page in your browser cannot reach it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, urlsplit

from .ast import (
    AgentCall,
    FunctionCall,
    ModelCall,
    Program,
    Step,
    ToolCall,
    WorkflowCall,
    program_payload,
)
from .board import AGENT as BOARD_AGENT
from .chat import ChatError, answer_text, message_parameter
from .memory import AGENT as MEMORY_AGENT
from .memory import bind_memory_tools
from .model import ModelProvider
from .modules import load_program
from .runtime import run
from .story import StoryError, build_story, read_trace
from .system_tools import bind_system_tools
from .testing import run_tests

PAGE = Path(__file__).parent / "app_assets" / "index.html"
MAX_AGENTS = 200
MAX_BODY = 64 * 1024
_SKIPPED = {"_trash", "archive", "node_modules", "__pycache__", "runs"}

ModelBinder = Callable[[Program], Mapping[str, ModelProvider]]


class AppError(ValueError):
    """A request the app cannot serve, with a message fit to show."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def find_agents(root: Path) -> list[Path]:
    """Every `.kos` file under `root` that can be run: it has `workflow main`
    or `fn main`. Hidden folders and build leftovers are skipped."""

    found: list[Path] = []
    pending = [root]
    while pending and len(found) < MAX_AGENTS:
        folder = pending.pop(0)
        try:
            entries = sorted(folder.iterdir(), key=lambda entry: entry.name.lower())
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith(".") or entry.name in _SKIPPED:
                continue
            if entry.is_dir() and not entry.is_symlink():
                pending.append(entry)
            elif entry.suffix == ".kos" and entry.is_file():
                try:
                    text = entry.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                if "workflow main" in text or "fn main" in text:
                    found.append(entry)
    return found[:MAX_AGENTS]


def _about(source: str) -> str:
    """The comment at the top of a file, as the author's own description."""

    lines = []
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            lines.append(stripped[2:].strip())
        elif stripped or lines:
            break
    kept = [line for line in lines if not line.startswith("kosika ")]
    return " ".join(part for part in kept if part).strip()


def _call_view(program: Program, call: Any) -> dict[str, Any]:
    if isinstance(call, FunctionCall):
        return {"kind": "code", "text": call.function_name}
    if isinstance(call, WorkflowCall):
        return {"kind": "workflow", "text": call.workflow_name}
    if isinstance(call, AgentCall):
        return {"kind": "agent", "text": call.agent_name}
    if isinstance(call, ModelCall):
        model = next((m for m in program.models if m.name == call.model_name), None)
        if model is not None and model.verb:
            return {
                "kind": "think",
                "text": model.verb,
                "model": model.provider or model.name,
                "evidence": model.evidence is not None,
            }
        return {"kind": "model", "text": call.model_name, "model": call.model_name}
    if isinstance(call, ToolCall):
        tool = next((t for t in program.tools if t.name == call.tool_name), None)
        source = tool.source if tool else ""
        if source.startswith("memory:"):
            return {
                "kind": "memory",
                "text": f"{tool.operation if tool else ''} {source[7:]}".strip(),
            }
        if source.startswith("board:"):
            author = dict(call.arguments).get("author")
            signed = getattr(author, "value", None)
            return {
                "kind": "board",
                "text": (
                    f"{signed} posts to {source[6:]}"
                    if tool and tool.operation == "post"
                    else f"read {source[6:]}"
                ),
            }
        return {
            "kind": "files" if source.startswith("system:files") else "tool",
            "text": call.tool_name,
            "changes": bool(tool and tool.access == "execute"),
        }
    return {"kind": "code", "text": "?"}


def _step_view(program: Program, step: Step) -> dict[str, Any]:
    models = {model.name: model.provider or model.name for model in program.models}
    view: dict[str, Any] = {"name": step.name}
    if step.branches:
        view["options"] = [
            {"when": pattern, **_call_view(program, call)}
            for pattern, call in step.branches
        ]
    else:
        view["options"] = [{"when": None, **_call_view(program, step.call)}]
        view["single"] = True
    if step.retries:
        view["retries"] = step.retries
    if step.votes:
        view["votes"] = step.votes
        view["voters"] = [models.get(name, name) for name in step.voters]
    if step.fallback is not None:
        view["fallback"] = _call_view(program, step.fallback)
    return view


def _traces(folder: Path) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for directory in (folder, folder / "runs"):
        if not directory.is_dir():
            continue
        for path in directory.glob("*.jsonl"):
            try:
                found.append(
                    {
                        "name": path.relative_to(folder).as_posix(),
                        "modified": path.stat().st_mtime,
                    }
                )
            except OSError:
                continue
    return sorted(found, key=lambda item: float(item["modified"]), reverse=True)[:50]


@dataclass
class Agent:
    id: str
    path: Path

    @property
    def title(self) -> str:
        return self.path.parent.name if self.path.name == "main.kos" else self.path.stem


def describe(agent: Agent, *, detail: bool) -> dict[str, Any]:
    """What the app shows for one agent. A program that does not check is
    still listed, with the reason, so it can be found and fixed."""

    view: dict[str, Any] = {"id": agent.id, "title": agent.title, "path": agent.id}
    try:
        source = agent.path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {**view, "error": f"Cannot read the file: {exc}"}
    view["about"] = _about(source)
    try:
        program = load_program(agent.path)
    except ValueError as exc:
        return {**view, "error": str(exc), **({"source": source} if detail else {})}
    declared = [model for model in program.models if model.provider is None]
    tools = [
        tool
        for tool in program.tools
        if not tool.source.startswith(("memory:", "board:"))
    ]
    entry = next((w for w in program.workflows if w.name == "main"), None)
    try:
        message_parameter(program, "main")
        chat = True
    except ChatError:
        chat = False
    view.update(
        models=[model.name for model in declared],
        default_model=next((m.name for m in declared if m.default), None),
        memories=[{"name": m.name, "record": m.record} for m in program.memories],
        boards=[{"name": b.name, "record": b.record} for b in program.boards],
        tools=[
            {
                "name": tool.name,
                "source": tool.source,
                "operation": tool.operation,
                "changes": tool.access == "execute",
            }
            for tool in tools
        ],
        changes_world=any(tool.access == "execute" for tool in tools),
        agents=[
            {"name": a.name, "can": list(a.can), "model": a.model}
            for a in program.agents
            if a.name not in (MEMORY_AGENT, BOARD_AGENT)
        ],
        swarms=[
            {
                "name": s.name,
                "agents": list(s.agents),
                "workflow": s.workflow,
                "max_rounds": s.max_rounds,
            }
            for s in program.swarms
        ],
        tests=len(program.tests),
        steps=len(entry.steps) if entry else 0,
        budget=[list(item) for item in entry.budget] if entry else [],
        chat=chat,
    )
    if not detail:
        return view
    example = agent.path.parent / "input.json"
    view.update(
        source=source,
        program_hash=hashlib.sha256(
            json.dumps(program_payload(program), sort_keys=True).encode("utf-8")
        ).hexdigest(),
        workflows=[
            {
                "name": workflow.name,
                "parameters": [
                    {"name": p.name, "type": p.type_name} for p in workflow.parameters
                ],
                "output": workflow.output_type,
                "budget": [list(item) for item in workflow.budget],
                "approval": workflow.approval_required,
                "steps": [_step_view(program, step) for step in workflow.steps],
            }
            for workflow in sorted(
                program.workflows, key=lambda item: (item.name != "main", item.name)
            )
        ],
        functions=sorted(f.name for f in program.functions if "$" not in f.name),
        records=sorted(record.name for record in program.records),
        test_names=[test.name for test in program.tests],
        traces=_traces(agent.path.parent),
        example_input=example.read_text(encoding="utf-8") if example.is_file() else "",
    )
    return view


class App:
    """The agents under one folder, and what may be done with them."""

    def __init__(
        self,
        root: Path,
        *,
        bind_models: ModelBinder | None = None,
        model_label: str = "",
        allow_folder: Path | None = None,
    ):
        self.root = root.resolve()
        self.bind_models = bind_models
        self.model_label = model_label
        self.allow_folder = allow_folder
        self.token = secrets.token_urlsafe(24)
        self._lock = threading.Lock()

    def agents(self) -> dict[str, Agent]:
        return {
            agent.id: agent
            for agent in (
                Agent(path.relative_to(self.root).as_posix(), path)
                for path in find_agents(self.root)
            )
        }

    def agent(self, agent_id: str) -> Agent:
        found = self.agents().get(agent_id)
        if found is None:
            raise AppError("No such agent in this folder", 404)
        return found

    def overview(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "can_run": self.bind_models is not None,
            "model": self.model_label,
            "allow_folder": str(self.allow_folder) if self.allow_folder else "",
            "agents": [describe(a, detail=False) for a in self.agents().values()],
        }

    def tests(self, agent_id: str) -> dict[str, Any]:
        agent = self.agent(agent_id)
        try:
            results = run_tests(load_program(agent.path))
        except ValueError as exc:
            raise AppError(str(exc)) from exc
        return {"results": [result.to_dict() for result in results]}

    def story(self, agent_id: str, trace: str) -> dict[str, Any]:
        agent = self.agent(agent_id)
        if trace not in {item["name"] for item in _traces(agent.path.parent)}:
            raise AppError("No such run for this agent", 404)
        try:
            return build_story(read_trace(agent.path.parent / trace))
        except StoryError as exc:
            raise AppError(str(exc)) from exc

    def run(self, agent_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Run the agent's `main` once, with what this app was granted."""

        if self.bind_models is None:
            raise AppError(
                "This app was started without a model, so it can show and "
                "test agents but not run them. Start it with --model NAME.",
                403,
            )
        agent = self.agent(agent_id)
        try:
            program = load_program(agent.path)
            if isinstance(body.get("text"), str):
                parameter, _ = message_parameter(program, "main")
                inputs: dict[str, Any] = {parameter: {"text": body["text"]}}
            elif isinstance(body.get("inputs"), dict):
                inputs = body["inputs"]
            else:
                raise AppError("Send either text or inputs")
            folder = agent.path.parent
            runs = folder / "runs"
            runs.mkdir(exist_ok=True)
            trace = (
                runs / f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}.jsonl"
            )
            tools = {
                **bind_system_tools(program, folder=self.allow_folder),
                **bind_memory_tools(program, folder / "memory"),
            }
            # Only tools that cannot change the world are ever bound here.
            tools = {
                name: tool
                for name, tool in tools.items()
                if not any(
                    t.name == name and t.access == "execute" for t in program.tools
                )
            }
            started = time.monotonic()
            with self._lock:
                result = run(
                    program,
                    "main",
                    trace,
                    inputs=inputs,
                    model_bindings=self.bind_models(program),
                    tool_bindings=tools,
                )
        except AppError:
            raise
        except (RuntimeError, ValueError, PermissionError, OSError) as exc:
            raise AppError(f"The run did not finish: {exc}", 422) from exc
        return {
            "result": result,
            "text": answer_text(result),
            "seconds": round(time.monotonic() - started, 1),
            "trace": trace.relative_to(folder).as_posix(),
            "story": build_story(read_trace(trace)),
        }


def _handler(app: App, port_holder: list[int]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "Kosika"

        def log_message(self, *_: Any) -> None:  # quiet
            return

        def _send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; connect-src 'self'",
            )
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: Any) -> None:
            self._send(
                status,
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )

        def _allowed(self, query: dict[str, list[str]]) -> bool:
            host = self.headers.get("Host", "")
            port = port_holder[0]
            if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
                return False
            origin = self.headers.get("Origin")
            if origin and origin not in (
                f"http://127.0.0.1:{port}",
                f"http://localhost:{port}",
            ):
                return False
            given = (
                self.headers.get("X-Kosika-Token") or (query.get("token") or [""])[0]
            )
            return hmac.compare_digest(given, app.token)

        def _serve(self, method: str) -> None:
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            if not self._allowed(query):
                self._json(
                    403, {"error": "This page was not opened from the Kosika app."}
                )
                return
            agent_id = (query.get("id") or [""])[0]
            try:
                if method == "GET" and url.path == "/":
                    self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                elif method == "GET" and url.path == "/api/agents":
                    self._json(200, app.overview())
                elif method == "GET" and url.path == "/api/agent":
                    self._json(200, describe(app.agent(agent_id), detail=True))
                elif method == "GET" and url.path == "/api/story":
                    self._json(
                        200, app.story(agent_id, (query.get("trace") or [""])[0])
                    )
                elif method == "POST" and url.path == "/api/test":
                    self._json(200, app.tests(agent_id))
                elif method == "POST" and url.path == "/api/run":
                    length = int(self.headers.get("Content-Length") or 0)
                    if not 0 < length <= MAX_BODY:
                        raise AppError("The request is empty or too large")
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                    if not isinstance(body, dict):
                        raise AppError("The request must be an object")
                    self._json(200, app.run(agent_id, body))
                else:
                    self._json(404, {"error": "Not found"})
            except AppError as exc:
                self._json(exc.status, {"error": str(exc)})
            except ValueError as exc:
                self._json(400, {"error": str(exc)})

        def do_GET(self) -> None:  # noqa: N802
            self._serve("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._serve("POST")

    return Handler


def make_server(app: App, port: int = 0) -> ThreadingHTTPServer:
    """A server for `app` on this machine only. Port 0 picks a free one."""

    holder = [port]
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler(app, holder))
    holder[0] = server.server_address[1]
    return server


def address(app: App, server: ThreadingHTTPServer) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}/?token={app.token}"
