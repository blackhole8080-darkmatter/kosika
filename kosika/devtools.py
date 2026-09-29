"""Development commands: format, complete, inspect, and fixture evaluations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .evaluation import EvaluationCase, evaluate
from .lexer import lex
from .manifest import load_manifest
from .model import ModelResponse, ScriptedModelProvider
from .model_steps import strict_json
from .modules import CATEGORIES, load_program
from .parser import parse
from .trace_replay import replay_trace


def format_source(source: str) -> str:
    """Conservative indentation formatter: preserves comments and line structure."""
    original = parse(source)
    depth = 0
    lines = []
    for line in source.splitlines():
        stripped = line.strip()
        tokens = [token for token in lex(stripped) if token.kind != "EOF"]
        closing = 0
        for token in tokens:
            if token.value not in ("}", "]") or token.kind != "SYMBOL":
                break
            closing += 1
        lines.append("  " * max(0, depth - closing) + stripped if stripped else "")
        depth += sum(
            1 if token.value in ("{", "[") else -1 if token.value in ("}", "]") else 0
            for token in tokens
            if token.kind == "SYMBOL"
        )
    result = "\n".join(lines).rstrip() + "\n"
    if parse(result) != original:
        raise ValueError("Formatting would change the program")
    return result


def completions(path: str | Path, prefix: str = "") -> list[str]:
    program = load_program(path)
    names = {
        "record",
        "tool",
        "agent",
        "model",
        "workflow",
        "fn",
        "ref",
        "recurse",
        "match",
        "case",
        "step",
        "import",
        "export",
        "call",
        "ask",
        "use_model",
        "execute",
        "require",
        "approval",
    }
    names.update(
        item.name
        for category in CATEGORIES
        for item in getattr(program, category)
        if "/" not in item.name
    )
    return sorted(name for name in names if name.startswith(prefix))


def inspect_trace(path: str | Path) -> dict[str, Any]:
    replay_trace(path)
    events = [
        strict_json(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
    ]
    steps: list[dict[str, Any]] = []

    def walk_functions(items: list[dict[str, Any]], prefix: str) -> None:
        for offset in range(0, len(items), 2):
            call, returned = items[offset : offset + 2]
            function = str(call["function"])
            name = prefix + function
            steps.append(
                {
                    "step": name,
                    "kind": "function_call",
                    "target": function,
                }
            )
            walk_functions(call.get("expression_events", []), name + "/argument/")
            walk_functions(returned.get("events", []), name + "/")

    def walk(items: list[dict[str, Any]], prefix: str = "") -> None:
        for offset in range(1, len(items) - 1, 4):
            step, call, returned, _ = items[offset : offset + 4]
            name = prefix + step["step"]
            steps.append(
                {
                    "step": name,
                    "kind": call["event"],
                    "target": call.get(
                        "workflow",
                        call.get(
                            "function",
                            call.get("agent", call.get("model", call.get("tool"))),
                        ),
                    ),
                }
            )
            if call["event"] == "workflow_call":
                walk(returned["events"], name + "/")
            walk_functions(call.get("expression_events", []), name + "/argument/")
            if call["event"] == "function_call":
                walk_functions(returned.get("events", []), name + "/")

    walk(events)
    return {
        "workflow": events[0]["workflow"],
        "program_hash": events[0].get("program_hash"),
        "steps": steps,
        "mode": "exact_replay",
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="kosika-dev")
    commands = parser.add_subparsers(dest="command", required=True)
    fmt = commands.add_parser("format")
    fmt.add_argument("source", type=Path)
    fmt.add_argument("--write", action="store_true")
    fmt.add_argument("--check", action="store_true")
    complete = commands.add_parser("complete")
    complete.add_argument("source", type=Path)
    complete.add_argument("--prefix", default="")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("trace", type=Path)
    evaluation = commands.add_parser("eval")
    evaluation.add_argument("source", type=Path)
    evaluation.add_argument("fixture", type=Path)
    evaluation.add_argument("--workflow", default="main")
    evaluation.add_argument("--output", type=Path, required=True)
    evaluation.add_argument("--mcp-server", type=Path)
    evaluation.add_argument("--mcp-manifest", type=Path)
    evaluation.add_argument("--mcp-source", default="local_stdio")
    args = parser.parse_args()
    try:
        if args.command == "format":
            source = args.source.read_text(encoding="utf-8")
            result = format_source(source)
            if args.check:
                return int(result != source)
            if args.write:
                args.source.write_text(result, encoding="utf-8")
            else:
                print(result, end="")
        elif args.command == "complete":
            print(json.dumps(completions(args.source, args.prefix)))
        elif args.command == "inspect":
            print(json.dumps(inspect_trace(args.trace), indent=2))
        else:
            if (args.mcp_server is None) != (args.mcp_manifest is None):
                raise ValueError(
                    "--mcp-server and --mcp-manifest must be supplied together"
                )
            fixture = strict_json(args.fixture.read_text(encoding="utf-8"))
            if not isinstance(fixture, list) or any(
                not isinstance(item, dict)
                or set(item) - {"name", "inputs", "expected", "contains", "responses"}
                or not isinstance(item.get("name"), str)
                or not isinstance(item.get("inputs"), dict)
                or not isinstance(item.get("responses"), dict)
                for item in fixture
            ):
                raise ValueError("Invalid evaluation fixture")
            cases = [
                EvaluationCase(
                    item["name"],
                    item["inputs"],
                    item.get("expected"),
                    item.get("contains", {}),
                )
                for item in fixture
            ]
            responses = {item["name"]: item["responses"] for item in fixture}

            def providers(case: EvaluationCase) -> dict[str, ScriptedModelProvider]:
                return {
                    name: ScriptedModelProvider(
                        [ModelResponse(content=json.dumps(value)) for value in values]
                    )
                    for name, values in responses[case.name].items()
                }

            mcp_commands = (
                {args.mcp_source: [sys.executable, str(args.mcp_server)]}
                if args.mcp_server is not None
                else None
            )
            manifest = (
                load_manifest(args.mcp_manifest)
                if args.mcp_manifest is not None
                else None
            )
            report = evaluate(
                load_program(args.source),
                args.workflow,
                cases,
                args.output,
                providers,
                mcp_commands=mcp_commands,
                manifest=manifest,
            )
            print(json.dumps(report, indent=2))
            return 0 if report["passed"] else 1
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"Development command rejected: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
