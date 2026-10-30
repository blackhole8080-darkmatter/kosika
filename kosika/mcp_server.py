"""An MCP server that lets an AI assistant write and try Kosika programs.

Run it with `kosika-mcp` (or `python -m kosika.mcp_server`). It speaks the
Model Context Protocol over standard input and output, and offers eight tools:

* `kosika_guide`   : the language guide written for AI authors.
* `kosika_check`   : check a program and return its diagnostics.
* `kosika_test`    : run the tests written in a program, with their own answers.
* `kosika_repair`  : the token to change when a typo has a near name.
* `kosika_run`     : run a program with supplied inputs and scripted answers.
* `kosika_replay`  : validate a trace from `kosika_run` and return its result.
* `kosika_map`     : a map of what a checked program declares.
* `kosika_declare` : turn a pinned component contract into Kosika declarations.

The server is deliberately powerless. A program arrives as text and is checked
in a temporary folder that is deleted afterwards. `kosika_run` never reaches a
real model, tool, file, or network: every model and tool answer comes from the
caller's own `model_answers` and `tool_answers`, and a program that needs one
it was not given fails. Nothing the calling assistant sends can grant a
program more than that.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .ast import ToolDecl
from .component_declaration import (
    ComponentDeclarationError,
    declaration_for_contract,
)
from .components import ComponentContractError, parse_component_contract
from .development import check_project
from .execution import ExecutionLimits
from .mindmap import build_mindmap, to_mermaid, to_text
from .model import ModelResponse, ScriptedModelProvider
from .modules import load_program
from .runtime import replay, run
from .testing import run_tests

PROTOCOL_VERSION = "2025-11-25"
MAX_SOURCE_BYTES = 64 * 1024
MAX_CONTRACT_BYTES = 256 * 1024
MAX_TRACE_BYTES = 512 * 1024
MAX_MESSAGE_BYTES = 1024 * 1024
RUN_LIMITS = ExecutionLimits(
    max_function_calls=5000,
    max_steps=128,
    max_model_calls=16,
    max_tool_calls=16,
    deadline_seconds=10,
)

_SUGGESTED_RENAME = re.compile(r" Did you mean '([^']+)'\?$")
_RENAME_CODES = frozenset({"check/field-access", "check/unknown-variable"})
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")

_SOURCE = {"type": "string", "description": "The complete Kosika program text."}
TOOLS: list[dict[str, Any]] = [
    {
        "name": "kosika_guide",
        "description": (
            "Return the Kosika language guide for AI authors. Read it before "
            "writing Kosika: the syntax is not Python, JavaScript, or Rust."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "kosika_check",
        "description": (
            "Check a Kosika program without running it. Returns ok, and for "
            "each problem a stable code, a message, and a line when known."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"source": _SOURCE},
            "required": ["source"],
            "additionalProperties": False,
        },
    },
    {
        "name": "kosika_test",
        "description": (
            "Run the `test` blocks written in a Kosika program and report each "
            "by name. No real model or tool is called: a test uses its own "
            "`answers` lines, and one that needs an answer it did not write "
            "fails."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": _SOURCE,
                "only": {
                    "type": "string",
                    "description": "Run only the test with this name.",
                },
            },
            "required": ["source"],
            "additionalProperties": False,
        },
    },
    {
        "name": "kosika_repair",
        "description": (
            "Check a program and, when the checker can name the spelling it "
            "expected for a field or variable, return the exact token to change "
            "and a patch that checks clean."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"source": _SOURCE},
            "required": ["source"],
            "additionalProperties": False,
        },
    },
    {
        "name": "kosika_run",
        "description": (
            "Check and run one workflow of a Kosika program. No real model or "
            "tool is ever called: supply model_answers and tool_answers for "
            "any the program uses. Returns the typed result and a trace."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": _SOURCE,
                "workflow": {"type": "string", "description": "Defaults to main."},
                "inputs": {
                    "type": "object",
                    "description": "One record per workflow parameter name.",
                },
                "model_answers": {
                    "type": "object",
                    "description": (
                        "Declared model name to an ordered list of answer "
                        "records. Thinking steps use their declared model's list."
                    ),
                },
                "tool_answers": {
                    "type": "object",
                    "description": "Tool name to one result record, or a list of them.",
                },
            },
            "required": ["source"],
            "additionalProperties": False,
        },
    },
    {
        "name": "kosika_replay",
        "description": (
            "Validate a trace returned by kosika_run and return its recorded "
            "result. Nothing is executed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"trace": {"type": "string"}},
            "required": ["trace"],
            "additionalProperties": False,
        },
    },
    {
        "name": "kosika_map",
        "description": (
            "Describe what a checked Kosika program declares: records, models, "
            "tools, agents, workflows, steps, and how they connect."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": _SOURCE,
                "format": {"type": "string", "enum": ["text", "mermaid", "json"]},
            },
            "required": ["source"],
            "additionalProperties": False,
        },
    },
    {
        "name": "kosika_declare",
        "description": (
            "Turn a pinned kosika.component/v1 contract into Kosika enums, "
            "records and a tool block. Shapes only: the host still grants the "
            "manifest, its effects and capabilities."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "contract": {
                    "type": "object",
                    "description": (
                        "The contract as `kosika component describe` printed it."
                    ),
                }
            },
            "required": ["contract"],
            "additionalProperties": False,
        },
    },
]


class ToolInputError(ValueError):
    """The caller's arguments for a tool are not usable."""


def _text(arguments: dict[str, Any], key: str, limit: int) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolInputError(f"{key} must be non-empty text")
    if len(value.encode("utf-8")) > limit:
        raise ToolInputError(f"{key} is larger than {limit // 1024} KiB")
    return value


def _mapping(arguments: dict[str, Any], key: str) -> dict[str, Any]:
    value = arguments.get(key, {})
    if not isinstance(value, dict):
        raise ToolInputError(f"{key} must be an object")
    return value


def _object(arguments: dict[str, Any], key: str, limit: int) -> dict[str, Any]:
    value = arguments.get(key)
    if not isinstance(value, dict):
        raise ToolInputError(f"{key} must be an object")
    if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > limit:
        raise ToolInputError(f"{key} is larger than {limit // 1024} KiB")
    return value


def _only(arguments: dict[str, Any], allowed: tuple[str, ...]) -> None:
    unknown = sorted(set(arguments) - set(allowed))
    if unknown:
        raise ToolInputError(f"unknown argument(s): {', '.join(unknown)}")


def _diagnostics(report: dict[str, Any]) -> list[dict[str, Any]]:
    problems = []
    for item in report.get("diagnostics", ()):
        position = item.get("range")
        problems.append(
            {
                "code": item.get("code"),
                "message": item.get("message"),
                "line": position["start"]["line"] + 1 if position else None,
                "column": position["start"]["character"] + 1 if position else None,
            }
        )
    return problems


def _with_program(
    source: str, action: Callable[[Path], dict[str, Any]]
) -> dict[str, Any]:
    """Check `source` in a throwaway folder, then act on it if it is valid."""

    with tempfile.TemporaryDirectory(prefix="kosika-mcp-") as directory:
        path = Path(directory) / "main.kos"
        path.write_text(source, encoding="utf-8")
        report = check_project(path)["diagnostics"]
        if not report["ok"]:
            return {"ok": False, "stage": "check", "problems": _diagnostics(report)}
        return action(path)


def guide(_arguments: dict[str, Any]) -> dict[str, Any]:
    _only(_arguments, ())
    path = Path(__file__).resolve().parents[1] / "AGENT_GUIDE.md"
    try:
        return {"ok": True, "guide": path.read_text(encoding="utf-8")}
    except OSError:
        return {
            "ok": False,
            "message": "The guide file is not installed with this copy of Kosika.",
        }


def check(arguments: dict[str, Any]) -> dict[str, Any]:
    _only(arguments, ("source",))
    return _with_program(
        _text(arguments, "source", MAX_SOURCE_BYTES),
        lambda _path: {"ok": True, "problems": []},
    )


def test_program(arguments: dict[str, Any]) -> dict[str, Any]:
    _only(arguments, ("source", "only"))
    source = _text(arguments, "source", MAX_SOURCE_BYTES)
    only = arguments.get("only")
    if only is not None and (not isinstance(only, str) or not only.strip()):
        raise ToolInputError("only must be non-empty text")

    def action(path: Path) -> dict[str, Any]:
        program = load_program(path)
        try:
            results = run_tests(program, only=only)
        except Exception as exc:  # any failure of the candidate is reported, not raised
            return {
                "ok": False,
                "stage": "test",
                "error": type(exc).__name__,
                "message": str(exc),
            }
        if only is not None and not results:
            asks_a_model = any(t.name == only and t.live for t in program.tests)
            return {
                "ok": False,
                "stage": "test",
                "message": (
                    "that test asks a real model, which this server never reaches"
                    if asks_a_model
                    else f"no test named {only!r}"
                ),
            }
        rows = [result.to_dict() for result in results]
        passed = sum(1 for result in results if result.passed)
        answer: dict[str, Any] = {
            "ok": passed == len(results),
            "tests": rows,
            "passed": passed,
            "failed": len(results) - passed,
        }
        if not rows:
            answer["message"] = "No tests found"
        return answer

    return _with_program(source, action)


def _rename_edit(source: str, problem: dict[str, Any]) -> dict[str, Any] | None:
    """The single-token rename a diagnostic asks for, when it is unambiguous."""

    if problem.get("code") not in _RENAME_CODES:
        return None
    message = problem.get("message")
    position = problem.get("range")
    if not isinstance(message, str) or not isinstance(position, dict):
        return None
    start, end = position.get("start") or {}, position.get("end") or {}
    if start.get("line") != end.get("line"):
        return None
    match = _SUGGESTED_RENAME.search(message)
    line_index, column, last = (
        start.get("line"),
        start.get("character"),
        end.get("character"),
    )
    if not match:
        return None
    if not isinstance(line_index, int) or not isinstance(column, int):
        return None
    if not isinstance(last, int):
        return None
    wanted = match.group(1)
    lines = source.splitlines()
    if not 0 <= line_index < len(lines):
        return None
    token = lines[line_index][column:last]
    if token == wanted or not _IDENTIFIER.fullmatch(token):
        return None
    if f"'{token}'" not in message.split(" Did you mean", 1)[0]:
        return None
    return {
        "code": problem["code"],
        "line": line_index + 1,
        "column": column + 1,
        "remove": token,
        "with": wanted,
    }


def _apply_edits(source: str, edits: list[dict[str, Any]]) -> str | None:
    """Rewrite `source`, or return None if an edit no longer fits its line."""

    lines = source.splitlines(keepends=True)
    for edit in sorted(
        edits, key=lambda e: (int(e["line"]), int(e["column"])), reverse=True
    ):
        index, start = int(edit["line"]) - 1, int(edit["column"]) - 1
        remove, replacement = str(edit["remove"]), str(edit["with"])
        if not 0 <= index < len(lines):
            return None
        line = lines[index]
        if line[start : start + len(remove)] != remove:
            return None
        lines[index] = line[:start] + replacement + line[start + len(remove) :]
    return "".join(lines)


def repair_program(arguments: dict[str, Any]) -> dict[str, Any]:
    _only(arguments, ("source",))
    source = _text(arguments, "source", MAX_SOURCE_BYTES)
    with tempfile.TemporaryDirectory(prefix="kosika-mcp-") as directory:
        path = Path(directory) / "main.kos"
        path.write_text(source, encoding="utf-8")
        report = check_project(path)["diagnostics"]
        ok = bool(report["ok"])
        problems = _diagnostics(report)
        edits: list[dict[str, Any]] = []
        patched = source
        while not report["ok"]:
            found = [
                edit
                for edit in (
                    _rename_edit(patched, item)
                    for item in report.get("diagnostics", ())
                )
                if edit is not None
            ]
            if not found:
                break
            candidate = _apply_edits(patched, found)
            if candidate is None:
                break
            edits.extend(found)
            patched = candidate
            path.write_text(patched, encoding="utf-8")
            report = check_project(path)["diagnostics"]
        answer: dict[str, Any] = {
            "ok": ok,
            "problems": problems,
            "edits": edits,
        }
        if edits and report["ok"]:
            answer["patched"] = patched
        return answer


def run_program(arguments: dict[str, Any]) -> dict[str, Any]:
    _only(arguments, ("source", "workflow", "inputs", "model_answers", "tool_answers"))
    source = _text(arguments, "source", MAX_SOURCE_BYTES)
    workflow = arguments.get("workflow", "main")
    if not isinstance(workflow, str):
        raise ToolInputError("workflow must be text")
    inputs = _mapping(arguments, "inputs")
    model_answers = _mapping(arguments, "model_answers")
    tool_answers = _mapping(arguments, "tool_answers")
    for name, answers in model_answers.items():
        if not isinstance(answers, list) or not all(
            isinstance(a, dict) for a in answers
        ):
            raise ToolInputError(f"model_answers[{name!r}] must be a list of records")
    remaining = {
        name: list(value)
        for name, value in tool_answers.items()
        if isinstance(value, list)
    }

    def scripted_tool(declaration: ToolDecl, _arguments: dict[str, Any]) -> Any:
        answer = tool_answers[declaration.name]
        if isinstance(answer, list):
            if not remaining[declaration.name]:
                raise RuntimeError(f"no tool answer left for {declaration.name!r}")
            return remaining[declaration.name].pop(0)
        return answer

    def action(path: Path) -> dict[str, Any]:
        program = load_program(path)
        declared_models = {m.name for m in program.models if m.provider is None}
        declared_tools = {tool.name for tool in program.tools}
        trace = path.with_name("trace.jsonl")
        try:
            result = run(
                program,
                workflow,
                trace,
                inputs=inputs,
                model_bindings={
                    name: ScriptedModelProvider(
                        [
                            ModelResponse(content=json.dumps(answer))
                            for answer in answers
                        ]
                    )
                    for name, answers in model_answers.items()
                    if name in declared_models
                },
                tool_bindings={
                    name: scripted_tool
                    for name in tool_answers
                    if name in declared_tools
                },
                limits=RUN_LIMITS,
            )
        except Exception as exc:  # any failure of the candidate is reported, not raised
            return {
                "ok": False,
                "stage": "run",
                "error": type(exc).__name__,
                "message": str(exc),
            }
        return {
            "ok": True,
            "result": result,
            "trace": trace.read_text(encoding="utf-8"),
        }

    return _with_program(source, action)


def replay_trace(arguments: dict[str, Any]) -> dict[str, Any]:
    _only(arguments, ("trace",))
    trace = _text(arguments, "trace", MAX_TRACE_BYTES)
    with tempfile.TemporaryDirectory(prefix="kosika-mcp-") as directory:
        path = Path(directory) / "trace.jsonl"
        path.write_text(trace, encoding="utf-8")
        try:
            return {"ok": True, "result": replay(path)}
        except (ValueError, OSError, KeyError, TypeError) as exc:
            return {"ok": False, "stage": "replay", "message": str(exc)}


def map_program(arguments: dict[str, Any]) -> dict[str, Any]:
    _only(arguments, ("source", "format"))
    style = arguments.get("format", "text")
    if style not in ("text", "mermaid", "json"):
        raise ToolInputError("format must be text, mermaid, or json")

    def action(path: Path) -> dict[str, Any]:
        mindmap = build_mindmap(load_program(path))
        if style == "json":
            return {"ok": True, "map": mindmap}
        return {
            "ok": True,
            "map": to_text(mindmap) if style == "text" else to_mermaid(mindmap),
        }

    return _with_program(_text(arguments, "source", MAX_SOURCE_BYTES), action)


def declare(arguments: dict[str, Any]) -> dict[str, Any]:
    _only(arguments, ("contract",))
    data = _object(arguments, "contract", MAX_CONTRACT_BYTES)
    try:
        contract = parse_component_contract(data)
    except ComponentContractError as exc:
        return {"ok": False, "stage": "contract", "message": str(exc)}
    try:
        text = declaration_for_contract(contract)
    except ComponentDeclarationError as exc:
        return {"ok": False, "stage": "declaration", "message": str(exc)}
    return {"ok": True, "declaration": text}


HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "kosika_guide": guide,
    "kosika_check": check,
    "kosika_test": test_program,
    "kosika_repair": repair_program,
    "kosika_run": run_program,
    "kosika_replay": replay_trace,
    "kosika_map": map_program,
    "kosika_declare": declare,
}


def call_tool(name: Any, arguments: Any) -> dict[str, Any]:
    """Run one tool and wrap its answer as an MCP tool result."""

    if name not in HANDLERS:
        raise ToolInputError(f"unknown tool {name!r}")
    if not isinstance(arguments, dict):
        raise ToolInputError("arguments must be an object")
    try:
        payload = HANDLERS[name](arguments)
        failed = False
    except ToolInputError as exc:
        payload, failed = {"ok": False, "stage": "input", "message": str(exc)}, True
    return {
        "structuredContent": payload,
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "isError": failed,
    }


def handle(message: dict[str, Any]) -> dict[str, Any]:
    method = message.get("method")
    params = message.get("params") or {}
    if method == "initialize":
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "serverInfo": {"name": "kosika", "version": __version__},
            "capabilities": {"tools": {}},
            "instructions": (
                "Call kosika_guide first, then write a program and use "
                "kosika_check until it passes. When a spelling is off, "
                "kosika_repair names the token to change and returns a patch "
                "that checks clean. Run its tests with kosika_test, and use "
                "kosika_run with supplied answers."
            ),
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        return call_tool(params.get("name"), params.get("arguments", {}))
    raise LookupError(f"Unknown method {method!r}")


def serve(stdin: Any = None, stdout: Any = None) -> None:
    """Answer newline-delimited JSON-RPC requests until input ends."""

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
            response: dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
        else:
            if not isinstance(message, dict) or "id" not in message:
                continue  # a notification needs no answer
            response = {"jsonrpc": "2.0", "id": message["id"]}
            try:
                response["result"] = handle(message)
            except LookupError as exc:
                response["error"] = {"code": -32601, "message": str(exc)}
            except (ToolInputError, TypeError, KeyError) as exc:
                response["error"] = {"code": -32602, "message": str(exc)}
        stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        stdout.flush()


def main() -> int:
    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
