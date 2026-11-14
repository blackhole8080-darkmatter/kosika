"""Serve a Kosika program's workflows as MCP tools, for any AI app.

    kosika serve-mcp hands/plan.kos -- --allow-shell --allow-folder hands \
        --approve-in hands/asks --model kosika-virtual ...

Each workflow becomes one tool, named after it, whose input schema is the
workflow's own parameters as Kosika declares them. Calling a tool runs the
workflow with `kosika run` and the flags given after `--`, exactly as the
person would at the command line, and answers with its result.

What an outside app may do is what the person granted, nothing more:

* the flags after `--` are fixed when the server starts; a call cannot add
  a folder, shell, model or server, it only gives the workflow its input;
* every action that changes something still waits for the person's yes. A
  server's standard input is the app's channel, so `--approve` (which asks
  at the terminal) is refused: yes comes through `--approve-in FOLDER`,
  answered with `kosika answer FOLDER` or from the phone with `--serve`;
* `--only NAME` serves only the named workflows.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .modules import load_program
from .schema import schema_for_type

PROTOCOL_VERSION = "2025-11-25"
MAX_MESSAGE_BYTES = 1024 * 1024
MAX_RESULT_CHARACTERS = 64 * 1024
REFUSED_FLAGS = ("--approve", "--input", "--workflow", "--trace")


class ProgramServerError(ValueError):
    """The server cannot start with what it was given."""


def _flag_problem(flags: list[str]) -> str:
    for flag in flags:
        name = flag.split("=", 1)[0]
        if name in REFUSED_FLAGS:
            if name == "--approve":
                return ("--approve asks at the terminal, but a server's terminal is the app's channel; "
                        "use --approve-in FOLDER and answer with `kosika answer FOLDER`")
            return f"{name} is chosen by each call, not when the server starts"
    return ""


def _approve_wait(flags: list[str]) -> float:
    for at, flag in enumerate(flags):
        if flag == "--approve-wait" and at + 1 < len(flags):
            try:
                return float(flags[at + 1])
            except ValueError:
                return 600.0
        if flag.startswith("--approve-wait="):
            try:
                return float(flag.split("=", 1)[1])
            except ValueError:
                return 600.0
    return 600.0


def _first_comment(source: Path) -> str:
    lines: list[str] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("//"):
            break
        text = stripped[2:].strip()
        if not text:
            break
        lines.append(text)
    return " ".join(lines)


class ProgramServer:
    def __init__(
        self,
        source: Path,
        flags: list[str],
        only: list[str] | None = None,
        runner: Callable[[list[str], float], subprocess.CompletedProcess] | None = None,
    ) -> None:
        problem = _flag_problem(flags)
        if problem:
            raise ProgramServerError(problem)
        self.source = source.resolve()
        self.flags = list(flags)
        self.program = load_program(self.source)
        workflows = {w.name: w for w in self.program.workflows}
        wanted = only or list(workflows)
        missing = [name for name in wanted if name not in workflows]
        if missing:
            raise ProgramServerError(f"{self.source.name} has no workflow {', '.join(missing)}")
        self.workflows = {name: workflows[name] for name in wanted}
        self.about = _first_comment(self.source)
        self.wait = _approve_wait(flags)
        self.runner = runner or self._run

    # ------------------------------------------------------------ tools
    def tools(self) -> list[dict[str, Any]]:
        out = []
        for name, workflow in self.workflows.items():
            properties = {p.name: schema_for_type(self.program, p.type_name) for p in workflow.parameters}
            asks = " Each action that changes something waits for the person's yes." if workflow.approval_required else ""
            about = f" {self.about}" if self.about else ""
            out.append({
                "name": name,
                "description": f"Runs the Kosika workflow {name} of {self.source.name}.{asks}{about}"[:1024],
                "inputSchema": {
                    "type": "object",
                    "properties": properties,
                    "required": [p.name for p in workflow.parameters],
                    "additionalProperties": False,
                },
            })
        return out

    def call(self, name: Any, arguments: Any) -> dict[str, Any]:
        if name not in self.workflows:
            raise LookupError(f"no tool named {name!r}")
        if not isinstance(arguments, dict):
            raise TypeError("arguments must be an object")
        workflow = self.workflows[name]
        expected = [p.name for p in workflow.parameters]
        extra = sorted(set(arguments) - set(expected))
        absent = [p for p in expected if p not in arguments]
        if extra or absent:
            raise TypeError(f"{name} takes {', '.join(expected) or 'nothing'}"
                            + (f"; not {', '.join(extra)}" if extra else "")
                            + (f"; missing {', '.join(absent)}" if absent else ""))
        with tempfile.TemporaryDirectory(prefix="kosika-mcp-") as scratch:
            given = Path(scratch) / "input.json"
            trace = Path(scratch) / "trace.jsonl"
            given.write_text(json.dumps(arguments, ensure_ascii=False), encoding="utf-8")
            args = ["run", str(self.source), "--workflow", name, "--input", str(given), "--trace", str(trace), *self.flags]
            try:
                done = self.runner(args, self.wait + 120)
            except subprocess.TimeoutExpired:
                return _answer("the run took too long and was stopped (no yes came in time?)", error=True)
            result, failed = _outcome(trace)
        if result is not None and not failed:
            return _answer(json.dumps(result, ensure_ascii=False, indent=2), structured=result)
        said = (done.stderr or done.stdout or "").strip()
        return _answer(failed or said or f"the run ended with exit code {done.returncode}", error=True)

    def _run(self, args: list[str], timeout: float) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        root = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        return subprocess.run(
            [sys.executable, "-m", "kosika.cli", *args],
            cwd=self.source.parent,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )

    # ------------------------------------------------------------ protocol
    def handle(self, message: dict[str, Any]) -> dict[str, Any]:
        method = message.get("method")
        params = message.get("params") or {}
        if method == "initialize":
            return {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": f"kosika-{self.source.stem}", "version": __version__},
                "instructions": f"Workflows of the Kosika program {self.source.name}. Actions that change something wait for the person's yes, so a call may take a while.",
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": self.tools()}
        if method == "tools/call":
            return self.call(params.get("name"), params.get("arguments", {}))
        raise LookupError(f"Method not found: {method}")

    def serve(self, stdin: Any = None, stdout: Any = None) -> None:
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        while True:
            line = stdin.readline(MAX_MESSAGE_BYTES + 1)
            if not line or len(line) > MAX_MESSAGE_BYTES:
                return
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except ValueError:
                response: dict[str, Any] = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
            else:
                if not isinstance(message, dict) or "id" not in message:
                    continue
                response = {"jsonrpc": "2.0", "id": message["id"]}
                try:
                    response["result"] = self.handle(message)
                except LookupError as exc:
                    response["error"] = {"code": -32601, "message": str(exc)}
                except (TypeError, KeyError, ValueError) as exc:
                    response["error"] = {"code": -32602, "message": str(exc)}
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()


def _outcome(trace: Path) -> tuple[Any, str]:
    """The run's result from its trace, or why it did not finish."""
    if not trace.exists():
        return None, ""
    result: Any = None
    failed = ""
    for line in trace.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get("event")
        if kind == "run_complete":
            result = event.get("result")
        elif kind in ("run_failed", "run_cancelled"):
            failed = str(event.get("error") or event.get("message") or event.get("code") or kind)
    return result, failed


def _answer(text: str, error: bool = False, structured: Any = None) -> dict[str, Any]:
    if len(text) > MAX_RESULT_CHARACTERS:
        text = text[:MAX_RESULT_CHARACTERS] + "\n… (cut)"
    answer: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": error}
    if isinstance(structured, dict):
        answer["structuredContent"] = structured
    return answer
