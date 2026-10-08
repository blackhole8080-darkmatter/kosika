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
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, urlsplit

from .agent import ApprovalRecord
from .approval_ledger import ApprovalLedger
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
from .durable import ApprovalPending, ExecutionSession
from .memory import AGENT as MEMORY_AGENT
from .memory import bind_memory_tools
from .model import ModelProvider
from .modules import load_program
from .runtime import run
from .starter import KINDS, StarterError, create_starter
from .story import StoryError, build_story, read_trace
from .swarm import SwarmApproval, run_swarm, swarm_plan_hash
from .system_tools import bind_system_tools
from .testing import check_tests, run_tests

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
    or `fn main`, or it declares a swarm. Hidden folders and build leftovers are skipped."""

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
                # A program with a swarm can be run as that swarm even when
                # it has no `main`.
                if "workflow main" in text or "fn main" in text or _SWARM.search(text):
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
            "kind": "files"
            if source.startswith("system:files")
            else "web"
            if source == "system:web"
            else "tool",
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
        has_main=entry is not None,
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
        record_shapes=[
            {
                "name": record.name,
                "fields": [
                    {"name": field.name, "type": field.type_name}
                    for field in record.fields
                ],
            }
            for record in sorted(program.records, key=lambda item: item.name)
        ],
        test_names=[test.name for test in program.tests],
        traces=_traces(agent.path.parent),
        example_input=example.read_text(encoding="utf-8") if example.is_file() else "",
    )
    return view


APPROVAL_WAIT_SECONDS = 600
MAX_SOURCE_BYTES = 1024 * 1024
_SWARM = re.compile(r"^swarm\s+\w+\s*\{", re.MULTILINE)
NEW_NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")
_EVENT_KEYS = (
    "event",
    "step",
    "workflow",
    "tool",
    "function",
    "model",
    "agent",
    "source",
    "operation",
    "access",
    "timestamp",
    "round",
)


def _slim(event: dict[str, Any], depth: int) -> dict[str, Any]:
    """An event as the page needs it: what happened, not the whole payload."""

    slim: dict[str, Any] = {key: event[key] for key in _EVENT_KEYS if key in event}
    slim["depth"] = depth
    if isinstance(event.get("recovered_from"), dict):
        slim["recovered"] = str(event["recovered_from"].get("message", ""))[:200]
    if "rejected" in event:
        slim["rejected"] = len(event["rejected"])
    if isinstance(event.get("votes"), dict):
        slim["votes"] = {
            key: event["votes"].get(key) for key in ("asked", "agreed", "models")
        }
    if isinstance(event.get("usage"), dict):
        slim["tokens"] = event["usage"].get("total_tokens")
    # What agents say to each other on a board is the conversation the swarm
    # view shows, so a post travels with who said it and a clip of the words.
    arguments = event.get("arguments")
    if (
        event.get("event") == "tool_call"
        and str(event.get("source", "")).startswith("board:")
        and event.get("operation") == "post"
        and isinstance(arguments, dict)
    ):
        body = arguments.get("body")
        words = (
            " ".join(str(value) for value in body.values())
            if isinstance(body, dict)
            else str(body)
        )
        slim["author"] = str(arguments.get("author", ""))
        slim["said"] = words[:600]
    return slim


class Job:
    """One run started from the app, followed while it works."""

    def __init__(self, job_id: str, agent_id: str, trace: Path, folder: Path):
        self.id, self.agent_id, self.trace, self.folder = (
            job_id,
            agent_id,
            trace,
            folder,
        )
        self.state = "running"  # running, waiting, done, failed, refused
        self.events: list[dict[str, Any]] = []
        self.request: dict[str, Any] | None = None
        self.allow: bool | None = None
        self.decision = threading.Event()
        self.result: Any = None
        self.text = ""
        self.story: dict[str, Any] | None = None
        self.error = ""
        self.seconds = 0.0


class App:
    """The agents under one folder, and what may be done with them."""

    def __init__(
        self,
        root: Path,
        *,
        bind_models: ModelBinder | None = None,
        model_label: str = "",
        allow_folder: Path | None = None,
        sites: tuple[str, ...] = (),
        approved_by: str = "app",
        allow_shell: bool = False,
    ):
        self.sites = sites
        # Starting programs is its own grant, on top of the folder they run
        # in. Even then every start waits for a yes to that exact command.
        self.allow_shell = allow_shell and allow_folder is not None
        self.root = root.resolve()
        self.bind_models = bind_models
        self.model_label = model_label
        self.allow_folder = allow_folder
        self.token = secrets.token_urlsafe(24)
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._jobs_lock = threading.Lock()
        self.approved_by = approved_by

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
        cards = [describe(a, detail=False) for a in self.agents().values()]
        return {
            "root": str(self.root),
            "can_run": self.bind_models is not None,
            "model": self.model_label,
            "allow_folder": str(self.allow_folder) if self.allow_folder else "",
            "sites": list(self.sites),
            "shell": self.allow_shell,
            "starters": [kind for kind in KINDS if kind != "hello"],
            "agents": cards,
            "assistant": self.assistant(cards),
        }

    def assistant(self, cards: list[dict[str, Any]] | None = None) -> str:
        """The agent the app opens on: one that can be chatted with,
        preferring a folder named `assistant`."""

        if cards is None:
            cards = [describe(a, detail=False) for a in self.agents().values()]
        chatty = [card["id"] for card in cards if card.get("chat")]
        named = [item for item in chatty if "assistant" in item.lower()]
        return (named or chatty or [""])[0]

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

    def start(self, agent_id: str, body: dict[str, Any]) -> Job:
        """Begin one run of the agent's `main` and return at once.

        The run happens on its own thread. Its events can be followed with
        `poll` while it works, and if it reaches an action that changes the
        world it stops and waits for `decide`.
        """

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
            tools = self._tools(program, folder)
            models = self.bind_models(program)
        except AppError:
            raise
        except (RuntimeError, ValueError, PermissionError, OSError) as exc:
            raise AppError(f"The run could not start: {exc}", 422) from exc
        job = Job(secrets.token_urlsafe(9), agent_id, trace, folder)
        with self._jobs_lock:
            # Keep the last few, so a page that reloads can still read them.
            for old in list(self._jobs)[:-20]:
                del self._jobs[old]
            self._jobs[job.id] = job
        threading.Thread(
            target=self._work, args=(job, program, inputs, models, tools), daemon=True
        ).start()
        return job

    def _tools(self, program: Program, folder: Path) -> dict[str, Any]:
        """What a run may reach. A tool that changes the world, or starts a
        program, may be bound, and still cannot run until this person allows
        that one call: the runtime refuses it without an approval."""

        return {
            **bind_system_tools(
                program,
                folder=self.allow_folder,
                sites=self.sites,
                allow_shell=self.allow_shell,
            ),
            **bind_memory_tools(program, folder / "memory"),
        }

    @staticmethod
    def _show_writing(job: Job, models: Mapping[str, ModelProvider]) -> None:
        """Let the page show an answer while the model writes it."""

        def wrote(piece: str) -> None:
            job.events.append({"event": "model_text", "text": piece, "depth": 0})

        for provider in models.values():
            if hasattr(provider, "on_text"):
                setattr(provider, "on_text", wrote)

    def start_swarm(self, agent_id: str, body: dict[str, Any]) -> Job:
        """Begin one run of a declared swarm. A swarm runs only with a yes
        to that swarm, which the page asks for before calling this."""

        if self.bind_models is None:
            raise AppError("This app was started without a model.", 403)
        if body.get("allow") is not True:
            raise AppError("A swarm runs only after you allow it", 403)
        agent = self.agent(agent_id)
        try:
            program = load_program(agent.path)
            name = str(body.get("swarm", ""))
            if name not in [swarm.name for swarm in program.swarms]:
                raise AppError("No such swarm in this program", 404)
            inputs = body.get("inputs")
            if not isinstance(inputs, dict):
                raise AppError("Send the swarm's inputs as an object")
            folder = agent.path.parent
            tools = self._tools(program, folder)
            models = self.bind_models(program)
        except AppError:
            raise
        except (RuntimeError, ValueError, PermissionError, OSError) as exc:
            raise AppError(f"The swarm could not start: {exc}", 422) from exc
        job = Job(secrets.token_urlsafe(9), agent_id, folder / "swarm", folder)
        with self._jobs_lock:
            for old in list(self._jobs)[:-20]:
                del self._jobs[old]
            self._jobs[job.id] = job

        def work() -> None:
            started = time.monotonic()
            self._show_writing(job, models)
            try:
                with (
                    self._lock,
                    tempfile.TemporaryDirectory(prefix="kosika-app-") as scratch,
                ):
                    outcome = run_swarm(
                        program,
                        name,
                        approval=SwarmApproval(
                            swarm_plan_hash(program, name),
                            self.approved_by,
                            "approved in Kosika Studio",
                        ),
                        ledger=ApprovalLedger(Path(scratch) / "ledger.sqlite"),
                        inputs=inputs,
                        model_bindings=models,
                        tool_bindings=tools,
                        collect_events=False,
                        on_event=lambda event, depth: job.events.append(
                            _slim(event, depth)
                        ),
                    )
                summary = outcome.to_dict()
                job.result = {
                    "result": summary["result"],
                    "rounds": len(summary["rounds"]),
                    "ended": summary["terminated_by"],
                    "counts": summary["counts"],
                }
                job.state = "done"
            except (RuntimeError, ValueError, PermissionError, OSError) as exc:
                job.error = f"The swarm did not finish: {exc}"
                job.state = "failed"
            finally:
                job.seconds = round(time.monotonic() - started, 1)

        threading.Thread(target=work, daemon=True).start()
        return job

    # ---- building agents in the window

    def check_source(self, agent_id: str, source: str) -> dict[str, Any]:
        """Would this text be a valid program in that agent's place? Nothing
        is written: the text stands in for the file while it is checked."""

        agent = self.agent(agent_id)
        try:
            program = load_program(agent.path, source_overrides={agent.path: source})
            check_tests(program)
        except (ValueError, TypeError) as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "workflows": len(program.workflows),
            "tests": len(program.tests),
        }

    def save_source(self, agent_id: str, source: str) -> dict[str, Any]:
        """Write the program's text. The page's own Save button is the yes:
        this is the person editing their own file. A text that does not
        check is still saved, as an editor would, and the answer says so."""

        agent = self.agent(agent_id)
        if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise AppError("The program is larger than 1 MiB")
        checked = self.check_source(agent_id, source)
        try:
            agent.path.write_text(source, encoding="utf-8", newline="")
        except OSError as exc:
            raise AppError(f"Could not save: {exc}", 422) from exc
        return {"saved": True, **checked}

    def new_agent(self, name: str, kind: str) -> dict[str, Any]:
        """Create a starter agent in a new folder under the app's folder."""

        if not NEW_NAME.fullmatch(name or ""):
            raise AppError(
                "Name it with lowercase letters, digits and underscores, "
                "starting with a letter (for example: my_helper)"
            )
        if kind == "hello" or kind not in KINDS:
            raise AppError("Unknown kind of agent")
        try:
            create_starter(self.root / name, kind)
        except (StarterError, OSError) as exc:
            raise AppError(f"Could not create it: {exc}", 422) from exc
        return {"id": f"{name}/main.kos"}

    def _work(
        self,
        job: Job,
        program: Program,
        inputs: dict[str, Any],
        models: Mapping[str, ModelProvider],
        tools: Mapping[str, Any],
    ) -> None:
        def watch(event: dict[str, Any], depth: int) -> None:
            job.events.append(_slim(event, depth))

        started = time.monotonic()
        self._show_writing(job, models)
        try:
            with (
                self._lock,
                tempfile.TemporaryDirectory(prefix="kosika-app-") as scratch,
            ):
                approvals: list[ApprovalRecord] = []
                while True:
                    session = ExecutionSession(
                        Path(scratch) / "run.sqlite",
                        binding_id="studio",
                        approvals=approvals,
                    )
                    try:
                        result = run(
                            program,
                            "main",
                            job.trace,
                            inputs=inputs,
                            model_bindings=models,
                            tool_bindings=tools,
                            session=session,
                            on_event=watch,
                        )
                        break
                    except ApprovalPending as pending:
                        asked = next(
                            (
                                tool
                                for tool in program.tools
                                if tool.name == pending.request["tool"]
                            ),
                            None,
                        )
                        job.request = {
                            "tool": pending.request["tool"],
                            "arguments": pending.request["arguments"],
                            "step": pending.request.get("step"),
                            # So the page can say plainly what kind of
                            # action this is, such as which program starts.
                            "kind": asked.source if asked else "",
                            "operation": asked.operation if asked else "",
                        }
                        job.allow = None
                        job.decision.clear()
                        job.state = "waiting"
                        # Nobody answering is a no.
                        job.decision.wait(timeout=APPROVAL_WAIT_SECONDS)
                        if job.allow is not True:
                            job.state = "refused"
                            job.error = "Refused. Nothing was changed."
                            return
                        approvals.append(
                            ApprovalRecord(
                                plan_hash=pending.request["plan_hash"],
                                action_hash=pending.request["action_hash"],
                                approved_by=self.approved_by,
                                reason="approved in the Kosika app",
                            )
                        )
                        job.request = None
                        job.events.append({"event": "resumed", "depth": 0})
                        job.state = "running"
            job.result = result
            job.text = answer_text(result)
            job.story = build_story(read_trace(job.trace))
            job.state = "done"
        except (RuntimeError, ValueError, PermissionError, OSError) as exc:
            job.error = f"The run did not finish: {exc}"
            job.state = "failed"
        finally:
            job.seconds = round(time.monotonic() - started, 1)

    def job(self, job_id: str) -> Job:
        with self._jobs_lock:
            found = self._jobs.get(job_id)
        if found is None:
            raise AppError("No such run", 404)
        return found

    def poll(self, job_id: str, since: int) -> dict[str, Any]:
        """Where a run stands, and the events it produced after `since`."""

        job = self.job(job_id)
        events = job.events[max(0, since) :]
        view: dict[str, Any] = {
            "run": job.id,
            "state": job.state,
            "events": events,
            "next": max(0, since) + len(events),
        }
        if job.state == "waiting":
            view["request"] = job.request
        if job.state in ("done", "failed", "refused"):
            view["seconds"] = job.seconds
            view["error"] = job.error
        if job.state == "done":
            view.update(result=job.result, text=job.text, story=job.story)
        return view

    def decide(self, job_id: str, allow: bool) -> dict[str, Any]:
        """Answer the one action a waiting run is asking about."""

        job = self.job(job_id)
        if job.state != "waiting":
            raise AppError("This run is not waiting for an answer", 409)
        job.allow = bool(allow)
        job.decision.set()
        return {"run": job.id, "allowed": job.allow}

    def run(self, agent_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Run the agent's `main` once and wait for it. A run that asks for
        approval is refused, because nobody is there to give it."""

        job = self.start(agent_id, body)
        while job.state == "running":
            time.sleep(0.01)
        if job.state == "waiting":
            self.decide(job.id, False)
            while job.state == "waiting":
                time.sleep(0.01)
        if job.state != "done":
            raise AppError(job.error or "The run did not finish", 422)
        return {
            "result": job.result,
            "text": job.text,
            "seconds": job.seconds,
            "trace": job.trace.relative_to(job.folder).as_posix(),
            "story": job.story,
        }

    def memory(self, agent_id: str) -> dict[str, Any]:
        """What the agent remembers right now, read from its memory folder."""

        agent = self.agent(agent_id)
        program = load_program(agent.path)
        folder = agent.path.parent / "memory"
        memories = []
        for declared in program.memories:
            entries: dict[str, Any] = {}
            path = folder / f"{declared.name}.json"
            if path.is_file():
                try:
                    loaded = json.loads(path.read_text(encoding="utf-8"))
                    entries = loaded if isinstance(loaded, dict) else {}
                except (OSError, ValueError):
                    entries = {}
            memories.append(
                {"name": declared.name, "record": declared.record, "entries": entries}
            )
        return {"memories": memories}


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

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_BODY:
                raise AppError("The request is empty or too large")
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                raise AppError("The request must be an object")
            return body

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
                elif method == "POST" and url.path == "/api/start":
                    self._json(200, {"run": app.start(agent_id, self._body()).id})
                elif method == "GET" and url.path == "/api/poll":
                    since = (query.get("since") or ["0"])[0]
                    self._json(
                        200,
                        app.poll(
                            (query.get("run") or [""])[0],
                            int(since) if since.isdigit() else 0,
                        ),
                    )
                elif method == "POST" and url.path == "/api/decide":
                    body = self._body()
                    self._json(
                        200,
                        app.decide(
                            (query.get("run") or [""])[0], body.get("allow") is True
                        ),
                    )
                elif method == "POST" and url.path == "/api/swarm":
                    self._json(200, {"run": app.start_swarm(agent_id, self._body()).id})
                elif method == "POST" and url.path in ("/api/check", "/api/save"):
                    source = self._body().get("source")
                    if not isinstance(source, str):
                        raise AppError("Send the program's text as source")
                    act = (
                        app.check_source
                        if url.path == "/api/check"
                        else app.save_source
                    )
                    self._json(200, act(agent_id, source))
                elif method == "POST" and url.path == "/api/new":
                    body = self._body()
                    self._json(
                        200,
                        app.new_agent(
                            str(body.get("name", "")), str(body.get("kind", ""))
                        ),
                    )
                elif method == "GET" and url.path == "/api/memory":
                    self._json(200, app.memory(agent_id))
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


def window_browsers() -> list[Path]:
    """Installed browsers that can show a page as a window of its own."""

    found: list[Path] = []
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(variable)
        if not base:
            continue
        for tail in (
            "Microsoft/Edge/Application/msedge.exe",
            "Google/Chrome/Application/chrome.exe",
        ):
            candidate = Path(base) / tail
            if candidate.is_file():
                found.append(candidate)
    for name in ("msedge", "google-chrome", "chromium", "chromium-browser"):
        located = shutil.which(name)
        if located:
            found.append(Path(located))
    return found


def open_window(url: str) -> bool:
    """Open the page as its own window, with no tabs or address bar.

    Returns False when no browser that can do this is installed, so the
    caller can open an ordinary tab instead.
    """

    for browser in window_browsers():
        try:
            subprocess.Popen(  # noqa: S603 - a fixed browser path and our own URL
                [str(browser), f"--app={url}", "--window-size=1280,860"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except OSError:
            continue
    return False


def address(app: App, server: ThreadingHTTPServer) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}/?token={app.token}"
