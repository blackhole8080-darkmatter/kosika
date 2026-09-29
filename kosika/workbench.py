"""Local Kosika workbench: browser UI, fixture execution, and exact replay.

No model endpoints, shell commands, external MCP servers, or arbitrary filesystem
paths can be supplied through this HTTP API. Optional live inference is host-configured. Source files run in a temporary root.
"""

from __future__ import annotations

import argparse
import contextlib
import hmac
import json
import re
import secrets
import socket
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .ast import Program, WorkflowCall
from .controls import ControlledProvider, ModelLimits
from .design import render_design
from .devtools import format_source
from .event_stream_design import (
    BridgePublicationError,
    build_timeline_design,
    publish_check_event,
    publish_failure_event,
    timeline_stage_summary,
)
from .model import (
    ModelResponse,
    ModelToolCall,
    ScriptedModelProvider,
)
from .model_steps import strict_json
from .modules import load_program
from .runtime import run
from .sse import (
    SSE_WAIT_SECONDS,
    EventStream,
    EventStreamError,
    ReplayGapError,
    SubscriberLimitError,
    format_sse,
)
from .synapse_bridge import normalize_events
from .trace_replay import replay_events
from .workbench_mcp import DEMO_SAMPLE, DemoMCP
from .workbench_mindmap import build_mindmap
from .workbench_samples import SAMPLES
from .workbench_support import LiveConfig, describe_inputs, diagnostic

MAX_BODY = 1048576
ASSETS = Path(__file__).with_name("workbench_assets")


def _files(payload: dict[str, Any], directory: Path) -> Path:
    files = payload.get("files")
    if not isinstance(files, dict) or not 1 <= len(files) <= 16:
        raise ValueError("A workspace needs 1–16 .kos files.")
    total = 0
    folded = set()
    for name, source in files.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_/-]+\.kos", name):
            raise ValueError(
                "Use relative .kos filenames with letters, numbers, dashes, or underscores."
            )
        parts = PurePosixPath(name).parts
        if (
            not parts
            or PurePosixPath(name).as_posix() != name
            or name.startswith("/")
            or any(
                part in (".", "..")
                or part.split(".")[0].upper()
                in {
                    "CON",
                    "PRN",
                    "AUX",
                    "NUL",
                    *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10)),
                }
                for part in parts
            )
        ):
            raise ValueError("Invalid workspace filename.")
        if name.casefold() in folded:
            raise ValueError(
                "Workspace filenames must be unique, including letter case."
            )
        folded.add(name.casefold())
        if not isinstance(source, str) or len(source.encode("utf-8")) > 65536:
            raise ValueError("Each source file must be text under 64 KiB.")
        total += len(source.encode("utf-8"))
        if total > 262144:
            raise ValueError("Workspace source exceeds 256 KiB.")
        target = directory.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    entry = payload.get("entry", "main.kos")
    if not isinstance(entry, str) or entry not in files:
        raise ValueError("Choose an entry file from this workspace.")
    return directory.joinpath(*PurePosixPath(entry).parts)


def _limits(program: Program, workflow: str, mcp: DemoMCP | None = None) -> None:
    records = {record.name: record for record in program.records}
    record_sizes: dict[str, int] = {}
    active: set[str] = set()

    def record_size(name: str) -> int:
        name = name.removesuffix("[]")
        if name not in records:
            return 1
        if name in active:
            raise ValueError("Recursive records are not supported in the workbench.")
        if name not in record_sizes:
            active.add(name)
            record_sizes[name] = 1 + sum(
                record_size(field.type_name) for field in records[name].fields
            )
            active.remove(name)
        if record_sizes[name] > 4096:
            raise ValueError("A record expands beyond the workbench schema limit.")
        return record_sizes[name]

    for name in records:
        record_size(name)
    workflows = {item.name: item for item in program.workflows}
    if workflow not in workflows:
        raise ValueError(f"Unknown workflow {workflow!r}.")
    if len(workflows) > 32 or sum(len(item.steps) for item in workflows.values()) > 128:
        raise ValueError("Workbench programs are limited to 128 declared steps.")
    cache: dict[str, int] = {}

    def count(name: str) -> int:
        if name not in cache:
            cache[name] = sum(
                count(step.call.workflow_name)
                if isinstance(step.call, WorkflowCall)
                else 1
                for step in workflows[name].steps
            )
        if cache[name] > 128:
            raise ValueError(
                "This workflow expands beyond the 128-step workbench limit."
            )
        return cache[name]

    count(workflow)
    for tool in program.tools:
        if mcp is not None and mcp.permits(program, tool):
            continue
        if (
            tool.access != "observe"
            or tool.source != "local_mock"
            or tool.operation not in {"get_disk_info", "get_structured_info"}
        ):
            raise PermissionError(
                "Workbench tools must be read-only local_mock operations. Use the host API for other adapters."
            )


def _providers(raw: Any, program: Program) -> dict[str, ControlledProvider]:
    if not isinstance(raw, dict) or set(raw) - {model.name for model in program.models}:
        raise ValueError("Fixtures must map declared model names to response arrays.")
    if len(json.dumps(raw).encode("utf-8")) > 65536:
        raise ValueError("Model fixtures exceed 64 KiB.")
    bindings = {}
    for name, records in raw.items():
        if not isinstance(records, list) or not 1 <= len(records) <= 64:
            raise ValueError(f"Fixture {name!r} needs 1–64 response records.")
        responses = []
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("Each model response must be a JSON object.")
            if "$tool_calls" in record:
                raw_calls = record["$tool_calls"]
                if (
                    set(record) != {"$tool_calls"}
                    or not isinstance(raw_calls, list)
                    or not 1 <= len(raw_calls) <= 8
                ):
                    raise ValueError("A tool-call fixture needs 1–8 calls.")
                calls = []
                for call in raw_calls:
                    if (
                        not isinstance(call, dict)
                        or set(call) != {"id", "name", "arguments"}
                        or not isinstance(call["id"], str)
                        or not call["id"]
                        or not isinstance(call["name"], str)
                        or not isinstance(call["arguments"], dict)
                    ):
                        raise ValueError(
                            "A tool call requires id, name, and object arguments."
                        )
                    calls.append(ModelToolCall(**call))
                responses.append(
                    ModelResponse(tool_calls=tuple(calls), finish_reason="tool_calls")
                )
            else:
                responses.append(
                    ModelResponse(content=json.dumps(record, allow_nan=False))
                )
        bindings[name] = ControlledProvider(
            ScriptedModelProvider(responses), ModelLimits(max_requests=64)
        )
    return bindings


def dispatch(
    action: str,
    payload: dict[str, Any],
    *,
    live: LiveConfig | None = None,
    mcp: DemoMCP | None = None,
    event_stream: EventStream | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    allowed = {
        "inspect-mcp": set(),
        "format": {"source"},
        "replay": {"trace"},
        "check": {"files", "entry", "workflow"},
        "mindmap": {"files", "entry", "workflow"},
        "run": {"files", "entry", "workflow", "inputs", "fixtures", "mode"},
        "timeline": {"events"},
    }
    if action not in allowed or set(payload) - allowed[action]:
        raise ValueError("Unknown action or unsupported workbench options.")
    if action == "inspect-mcp":
        if mcp is None:
            raise ValueError("No MCP server has been granted by the host.")
        return mcp.inspect()
    if action == "format":
        source = payload.get("source")
        if not isinstance(source, str) or len(source.encode("utf-8")) > 65536:
            raise ValueError("Format expects a source file under 64 KiB.")
        return {"source": format_source(source)}
    if action == "replay":
        trace = payload.get("trace")
        if not isinstance(trace, str):
            raise ValueError("Replay expects JSONL trace text.")
        recorded_events = [strict_json(line) for line in trace.splitlines()]
        return {
            "result": replay_events(recorded_events),
            "mode": "exact_replay",
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    if action == "timeline":
        raw_events = payload.get("events")
        if not isinstance(raw_events, list):
            raise ValueError("Timeline expects a JSON array of raw trace events.")
        if len(raw_events) > 512:
            raise ValueError("Timeline input exceeds 512 events.")
        for raw_event in raw_events:
            if not isinstance(raw_event, dict):
                raise ValueError("Each timeline event must be a JSON object.")
        manifest = build_timeline_design(
            normalize_events(raw_events),
            "workbench-timeline",
            "Workbench run timeline",
        )
        return {
            "design": {
                "digest": manifest.digest,
                "kind": manifest.design["kind"],
                "stages": timeline_stage_summary(manifest)["stages"],
            },
            "mermaid": render_design(manifest, "mermaid"),
            "svg": render_design(manifest, "svg"),
        }
    if action not in {"check", "run", "mindmap"}:
        raise ValueError("Unknown workbench action.")
    with tempfile.TemporaryDirectory(prefix="kosika-workbench-") as folder:
        directory = Path(folder)
        entry = _files(payload, directory)
        try:
            program = load_program(entry, root=directory)
            workflow = payload.get("workflow", "main")
            if not isinstance(workflow, str):
                raise ValueError("Workflow name must be text.")
            if event_stream is not None:
                try:
                    event_stream.publish(
                        [
                            publish_check_event(
                                event_stream.next_sequence, program, workflow
                            )
                        ]
                    )
                except BridgePublicationError as exc:
                    raise EventStreamError(str(exc)) from exc
            if action == "mindmap":
                return {"mindmap": build_mindmap(program, workflow, payload["files"])}
            _limits(program, workflow, mcp)
            summary = {
                "workflows": [item.name for item in program.workflows],
                "models": [item.name for item in program.models],
                "tools": [item.name for item in program.tools],
                "records": len(program.records),
                "files": len(program.module_hashes),
            }
            if action == "check":
                return {
                    "summary": summary,
                    "input_schema": describe_inputs(program, workflow),
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                }
            inputs = payload.get("inputs", {})
            if (
                not isinstance(inputs, dict)
                or len(json.dumps(inputs).encode("utf-8")) > 65536
            ):
                raise ValueError("Inputs must be a JSON object under 64 KiB.")
            mode = payload.get("mode", "fixture")
            if mode not in ("fixture", "live"):
                raise ValueError("Unknown execution mode.")
            if mode == "live":
                if live is None:
                    raise ValueError("Live mode is not configured by the host.")
                provider = live.provider()
                providers = {model.name: provider for model in program.models}
            else:
                providers = _providers(payload.get("fixtures", {}), program)
            events: list[dict[str, Any]] = []
            result = run(
                program,
                workflow,
                None,
                inputs=inputs,
                model_bindings=providers,
                tool_bindings={
                    tool.name: mcp.execute
                    for tool in program.tools
                    if tool.source == mcp.source
                }
                if mcp
                else None,
                _events_out=events,
            )
            trace = (
                "\n".join(
                    json.dumps(event, sort_keys=True, allow_nan=False)
                    for event in events
                )
                + "\n"
            )
            if len(trace.encode("utf-8")) > 786432:
                raise ValueError(
                    "Trace exceeds the workbench limit. Use smaller fixtures or fewer steps."
                )
            if event_stream is not None:
                event_stream.publish(
                    normalize_events(
                        events,
                        sequence_start=event_stream.next_sequence,
                    )
                )
            return {
                "result": result,
                "trace": trace,
                "events": events,
                "summary": summary,
                "telemetry": {
                    name: provider.telemetry for name, provider in providers.items()
                },
                "mode": mode,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            }
        except (ValueError, RuntimeError, OSError) as exc:
            if event_stream is not None:
                try:
                    event_stream.publish(
                        [publish_failure_event(event_stream.next_sequence, None, exc)]
                    )
                except (EventStreamError, BridgePublicationError):
                    pass
            # Keep temporary host paths out of diagnostics shown to authors.
            message = (
                str(exc)
                .replace(str(directory) + "\\", "")
                .replace(str(directory) + "/", "")
            )
            raise ValueError(message) from exc


class WorkbenchServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        port: int = 8765,
        *,
        live: LiveConfig | None = None,
        mcp: DemoMCP | None = None,
        event_stream: EventStream | None = None,
        event_jsonl: str | Path | None = None,
    ):
        if event_stream is not None and event_jsonl is not None:
            raise ValueError("Choose an event stream or an event JSONL path, not both.")
        self.mcp = mcp
        self.live = live
        self.token = secrets.token_urlsafe(32)
        self.slots = threading.BoundedSemaphore(2)
        self.event_stream = event_stream or EventStream(jsonl_path=event_jsonl)
        super().__init__(("127.0.0.1", port), WorkbenchHandler)

    def server_close(self) -> None:
        self.event_stream.close()
        super().server_close()


class WorkbenchHandler(BaseHTTPRequestHandler):
    server: WorkbenchServer

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _send(
        self,
        status: int,
        data: bytes,
        content_type: str = "application/json; charset=utf-8",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
        )
        self.send_header("Connection", "close")
        self.end_headers()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(data)
        self.close_connection = True

    def _json(self, status: int, value: Any) -> None:
        self._send(status, json.dumps(value, allow_nan=False).encode("utf-8"))

    def _event_query(self) -> tuple[int | str | None, int]:
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        for key in ("after", "limit"):
            if len(query.get(key, [])) > 1:
                raise EventStreamError(f"SSE query parameter {key!r} may appear once")
        after_values = query.get("after", [])
        after: int | str | None = after_values[0] if after_values else None
        if after == "":
            after = None
        limit_values = query.get("limit", [])
        if limit_values:
            try:
                limit = int(limit_values[0])
            except ValueError as exc:
                raise EventStreamError("SSE limit must be an integer") from exc
        else:
            limit = self.server.event_stream.max_replay
        if limit < 1 or limit > self.server.event_stream.max_replay:
            raise EventStreamError(
                f"SSE limit must be between one and {self.server.event_stream.max_replay}"
            )
        last_event_id = self.headers.get("Last-Event-ID")
        if after is None and last_event_id:
            after = last_event_id
        return after, limit

    def _events(self) -> None:
        if not hmac.compare_digest(
            self.headers.get("X-Kosika-Token", ""), self.server.token
        ):
            self._json(403, {"error": "Session expired. Reload the workbench."})
            return
        try:
            after, limit = self._event_query()
            stream = self.server.event_stream
            initial_batch = stream.read_after(after, limit=limit)
            stream.acquire_subscriber()
        except ReplayGapError as exc:
            self._json(
                409,
                {
                    "error": str(exc),
                    "oldest": exc.oldest,
                    "latest": exc.latest,
                },
            )
            return
        except SubscriberLimitError as exc:
            self._json(429, {"error": str(exc)})
            return
        except EventStreamError as exc:
            self._json(400, {"error": str(exc)})
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; connect-src 'self'; frame-ancestors 'none'",
        )
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.close_connection = True
        cursor: int | str | None = after
        disconnected = threading.Event()
        stop_watcher = threading.Event()

        def watch_client() -> None:
            try:
                while not stop_watcher.is_set():
                    if self.connection.recv(1, socket.MSG_PEEK) == b"":
                        disconnected.set()
                        return
            except (BlockingIOError, TimeoutError, socket.timeout):
                if not stop_watcher.is_set():
                    disconnected.set()
            except OSError:
                disconnected.set()

        watcher = threading.Thread(target=watch_client, daemon=True)
        watcher.start()
        first_batch = True
        try:
            while not stream.closed and not disconnected.is_set():
                if first_batch:
                    batch = initial_batch
                    first_batch = False
                else:
                    try:
                        batch = stream.wait_for(
                            cursor,
                            timeout=min(SSE_WAIT_SECONDS, 0.25),
                            limit=limit,
                        )
                    except EventStreamError:
                        # The bounded buffer evicted this cursor mid-connection;
                        # close cleanly so the client reconnects from its ID.
                        break
                if disconnected.is_set():
                    break
                if batch:
                    for event in batch:
                        self.wfile.write(format_sse(event))
                        self.wfile.flush()
                    cursor = batch[-1]["event_id"]
                else:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            pass
        finally:
            stop_watcher.set()
            with contextlib.suppress(OSError):
                self.connection.shutdown(socket.SHUT_RDWR)
            watcher.join(timeout=0.5)
            stream.release_subscriber()

    def _local(self) -> bool:
        authority = f"127.0.0.1:{self.server.server_port}"
        if (
            self.headers.get("Host") != authority
            or self.headers.get("Sec-Fetch-Site") == "cross-site"
        ):
            self._json(
                403,
                {"error": "This workbench accepts same-origin loopback requests only."},
            )
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin != f"http://{authority}":
            self._json(403, {"error": "Origin is not permitted."})
            return False
        return True

    def do_GET(self) -> None:
        if not self._local():
            return
        if urlsplit(self.path).path == "/api/events":
            self._events()
            return
        if self.path == "/api/session":
            self._json(
                200,
                {
                    "token": self.server.token,
                    "samples": SAMPLES + ([DEMO_SAMPLE] if self.server.mcp else []),
                    "mcp": self.server.mcp.public() if self.server.mcp else None,
                    "mode": "fixture",
                    "version": "0.3",
                    "live": self.server.live.public() if self.server.live else None,
                },
            )
            return
        files = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/integrations.js": ("integrations.js", "text/javascript; charset=utf-8"),
            "/mindmap.js": ("mindmap.js", "text/javascript; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/workbench-v2.js": ("workbench-v2.js", "text/javascript; charset=utf-8"),
            "/events.js": ("events.js", "text/javascript; charset=utf-8"),
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
            "/theme-lab.css": ("theme-lab.css", "text/css; charset=utf-8"),
            "/theme-glass.css": ("theme-glass.css", "text/css; charset=utf-8"),
            "/favicon.svg": ("favicon.svg", "image/svg+xml"),
        }
        asset = files.get(self.path)
        if asset is None:
            self._json(404, {"error": "Not found."})
            return
        self._send(200, (ASSETS / asset[0]).read_bytes(), asset[1])

    def do_POST(self) -> None:
        if not self._local():
            return
        if not hmac.compare_digest(
            self.headers.get("X-Kosika-Token", ""), self.server.token
        ):
            self._json(403, {"error": "Session expired. Reload the workbench."})
            return
        if self.path not in {
            "/api/check",
            "/api/mindmap",
            "/api/run",
            "/api/format",
            "/api/replay",
            "/api/inspect-mcp",
            "/api/timeline",
        }:
            self._json(404, {"error": "Not found."})
            return
        if self.headers.get("Content-Type", "").split(";")[
            0
        ] != "application/json" or self.headers.get("Transfer-Encoding"):
            self._json(415, {"error": "Send a length-delimited JSON request."})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_BODY:
                self._json(413, {"error": "Request must be between 1 byte and 1 MiB."})
                return
        except ValueError:
            self._json(400, {"error": "Invalid content length."})
            return
        if not self.server.slots.acquire(blocking=False):
            self._json(429, {"error": "The workbench is busy. Try again shortly."})
            return
        try:
            self.connection.settimeout(5)
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError("Incomplete request body.")
            payload = strict_json(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Request must be a JSON object.")
            self._json(
                200,
                {
                    "ok": True,
                    **dispatch(
                        self.path.removeprefix("/api/"),
                        payload,
                        live=self.server.live,
                        mcp=self.server.mcp,
                        event_stream=self.server.event_stream,
                    ),
                },
            )
        except (
            ValueError,
            RuntimeError,
            OSError,
            TypeError,
            KeyError,
            RecursionError,
        ) as exc:
            self._json(
                400,
                {
                    "ok": False,
                    "error": str(exc),
                    "kind": type(exc).__name__,
                    "diagnostic": diagnostic(str(exc)),
                },
            )
        finally:
            self.server.slots.release()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--live-model", help="Explicitly enable this local model")
    parser.add_argument("--live-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument(
        "--event-trace",
        type=Path,
        help="Optional normalized event JSONL file for bounded SSE replay",
    )
    parser.add_argument(
        "--mcp-demo",
        action="store_true",
        help="Grant the bundled read-only MCP text server",
    )
    args = parser.parse_args()
    live = LiveConfig(args.live_url, args.live_model) if args.live_model else None
    with WorkbenchServer(
        args.port,
        live=live,
        mcp=DemoMCP() if args.mcp_demo else None,
        event_jsonl=args.event_trace,
    ) as server:
        print(f"Kosika Workbench: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
