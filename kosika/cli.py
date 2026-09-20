from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

from .agent import AgentLoop, AgentLoopError, ApprovalRecord
from .approval_ledger import ApprovalLedger
from .ast import ToolDecl
from .checker import check
from .controls import ControlledProvider, ModelLimits
from .durable import ApprovalPending, ExecutionSession
from .manifest import (
    MCPManifest,
    diff_manifests,
    format_manifest_diff,
    load_manifest,
    manifest_from_client,
    verify_server,
    write_manifest,
)
from .mcp_stdio import MCPProtocolError, MCPStdioClient
from .model import (
    ModelProvider,
    ModelProviderError,
    ModelResponse,
    OpenAICompatibleProvider,
    ScriptedModelProvider,
)
from .model_steps import strict_json
from .modules import load_program
from .runtime import MockMCP, replay, run
from .schema import schema_for_record, schema_for_tool_input, schemas_equal


def _run_agent_command(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> int:
    if not args.model:
        parser.error("--model is required (or set KOSIKA_MODEL)")
    if args.mcp_server is not None and args.mcp_manifest is None:
        parser.error("--mcp-manifest is required when --mcp-server is used")
    if args.mcp_server is None and args.mcp_manifest is not None:
        parser.error("--mcp-manifest requires --mcp-server")

    if (args.approval_plan_hash is None) != (args.approval_action_hash is None):
        parser.error(
            "--approval-plan-hash and --approval-action-hash must be used together"
        )

    program = load_program(args.source)
    check(program)
    manifest: MCPManifest | None = None
    with contextlib.ExitStack() as stack:
        client: MCPStdioClient | None = None
        if args.mcp_server is not None:
            manifest = load_manifest(args.mcp_manifest)
            client = stack.enter_context(
                MCPStdioClient([sys.executable, str(args.mcp_server)])
            )
            verify_server(client, manifest)

        def execute_tool(declaration: ToolDecl, arguments: dict[str, Any]) -> Any:
            if client is not None and manifest is not None:
                if manifest.source != declaration.source:
                    raise AgentLoopError(
                        f"Pinned manifest source {manifest.source!r} does not match "
                        f"tool source {declaration.source!r}"
                    )
                pinned = manifest.tools.get(declaration.operation)
                if pinned is None:
                    raise AgentLoopError(
                        f"No pinned manifest entry for MCP tool {declaration.operation!r}"
                    )
                input_schema = schema_for_tool_input(program, declaration.input)
                if not schemas_equal(pinned.input_schema, input_schema):
                    raise AgentLoopError(
                        f"Pinned input schema for {declaration.operation!r} does not "
                        f"match Kosika input record {declaration.input!r}"
                    )
                return client.call_tool(
                    declaration.operation,
                    arguments,
                    input_schema=input_schema,
                    output_schema=schema_for_record(program, declaration.output),
                )
            return MockMCP().call(declaration.source, declaration.operation)

        provider = OpenAICompatibleProvider(
            args.model_url,
            args.model,
            api_key=os.environ.get("KOSIKA_MODEL_API_KEY"),
        )
        loop = AgentLoop(
            program,
            provider,
            execute_tool,
            max_steps=args.max_steps,
            approval_ledger=ApprovalLedger(args.approval_ledger)
            if args.approval_ledger is not None
            else None,
        )
        current_plan_hash = loop.plan_hash_for(args.agent, args.task)
        approval = None
        if args.approval_plan_hash is not None:
            approval = ApprovalRecord(
                plan_hash=args.approval_plan_hash,
                approved_by=args.approved_by,
                action_hash=args.approval_action_hash,
            )
        print(f"Plan hash: {current_plan_hash}")
        try:
            result = loop.run(
                args.agent,
                args.task,
                system_prompt=args.system_prompt,
                approval=approval,
            )
        except (AgentLoopError, ModelProviderError, MCPProtocolError) as exc:
            print(f"Agent failed safely: {exc}", file=sys.stderr)
            return 2
        print(result.content)
        print(f"Model steps: {result.model_steps}")
        return 0


def _workflow_model_bindings(args: argparse.Namespace) -> dict[str, ModelProvider]:
    live = (args.model_binding, args.model, args.model_url)
    if args.model_fixture is not None:
        if any(live):
            raise ValueError(
                "Use either --model-fixture or an explicit live model binding"
            )
        fixture = strict_json(args.model_fixture.read_text(encoding="utf-8"))
        if not isinstance(fixture, dict):
            raise ValueError(
                "Model fixture must map model names to lists of output records"
            )
        providers: dict[str, ModelProvider] = {}
        for name, results in fixture.items():
            if (
                not isinstance(results, list)
                or not results
                or any(not isinstance(item, dict) for item in results)
            ):
                raise ValueError(
                    f"Model fixture {name!r} requires a nonempty list of records"
                )
            providers[name] = ScriptedModelProvider(
                [
                    ModelResponse(content=json.dumps(item, allow_nan=False))
                    for item in results
                ]
            )
        return providers
    if any(live):
        if not all(live):
            raise ValueError(
                "Live binding requires --model-binding, --model, and --model-url"
            )
        return {
            args.model_binding: OpenAICompatibleProvider(
                args.model_url,
                args.model,
                api_key=os.environ.get("KOSIKA_MODEL_API_KEY"),
            )
        }
    return {}


def main() -> int:
    parser = argparse.ArgumentParser(prog="kosika")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("source", type=Path)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("source", type=Path)
    run_parser.add_argument("--workflow", default="main")
    run_parser.add_argument("--trace", type=Path, default=Path("run_trace.jsonl"))
    run_parser.add_argument("--journal", type=Path)
    run_parser.add_argument(
        "--binding-id",
        help="Stable host configuration identity required with --journal",
    )
    run_parser.add_argument("--approval-record", type=Path)
    run_parser.add_argument("--max-requests", type=int, default=64)
    run_parser.add_argument("--max-tokens", type=int)
    run_parser.add_argument("--max-output-tokens", type=int, default=1024)
    run_parser.add_argument("--telemetry", type=Path)
    run_parser.add_argument(
        "--mcp-server",
        type=Path,
        help="Python MCP stdio server to launch for source declarations using local_stdio.",
    )
    run_parser.add_argument(
        "--mcp-manifest",
        type=Path,
        help="Pinned JSON manifest required for live MCP execution.",
    )
    run_parser.add_argument("--mcp-source", default="local_stdio")
    run_parser.add_argument(
        "--input",
        type=Path,
        help="JSON object mapping workflow parameter names to records.",
    )
    run_parser.add_argument(
        "--model-fixture",
        type=Path,
        help="Offline JSON model outputs grouped by declared model name.",
    )
    run_parser.add_argument(
        "--model-binding", help="Declared Kosika model granted access to this provider."
    )
    run_parser.add_argument(
        "--model", help="Provider model identifier for the explicit live binding."
    )
    run_parser.add_argument(
        "--model-url", help="Explicit OpenAI-compatible endpoint for the live binding."
    )

    agent_parser = subparsers.add_parser(
        "agent",
        help="Run a declared Kosika agent against a local OpenAI-compatible model.",
    )
    agent_parser.add_argument("source", type=Path)
    agent_parser.add_argument("--agent", required=True)
    agent_parser.add_argument("--task", required=True)
    agent_parser.add_argument(
        "--model-url",
        default=os.environ.get("KOSIKA_MODEL_URL", "http://localhost:11434/v1"),
        help="OpenAI-compatible model URL, or KOSIKA_MODEL_URL.",
    )
    agent_parser.add_argument(
        "--model",
        default=os.environ.get("KOSIKA_MODEL"),
        help="Local model name, or KOSIKA_MODEL.",
    )
    agent_parser.add_argument(
        "--system-prompt",
        default=None,
        help="Optional system prompt for the model.",
    )
    agent_parser.add_argument(
        "--max-steps",
        type=int,
        default=8,
        help="Maximum model responses allowed in one run.",
    )
    agent_parser.add_argument(
        "--mcp-server",
        type=Path,
        help="Python MCP stdio server for live declared tools.",
    )
    agent_parser.add_argument(
        "--mcp-manifest",
        type=Path,
        help="Pinned manifest required when --mcp-server is used.",
    )
    agent_parser.add_argument("--mcp-source", default="local_stdio")
    agent_parser.add_argument(
        "--approval-plan-hash",
        help="Plan hash approved for execute-capability tools.",
    )
    agent_parser.add_argument(
        "--approval-action-hash",
        help="Hash of the exact execute tool and arguments printed on approval refusal.",
    )
    agent_parser.add_argument(
        "--approval-ledger",
        type=Path,
        help="Persistent SQLite ledger required before an execute tool can run.",
    )
    agent_parser.add_argument(
        "--approved-by",
        default="cli-user",
        help="Approval actor recorded with --approval-plan-hash.",
    )

    replay_parser = subparsers.add_parser("replay")
    replay_parser.add_argument("trace", type=Path)

    manifest_parser = subparsers.add_parser("manifest")
    manifest_subparsers = manifest_parser.add_subparsers(
        dest="manifest_command", required=True
    )

    generate_parser = manifest_subparsers.add_parser(
        "generate",
        help="Discover a live MCP server and write a pinned manifest.",
    )
    generate_parser.add_argument("--mcp-server", type=Path, required=True)
    generate_parser.add_argument("--source", default="local_stdio")
    generate_parser.add_argument("--output", type=Path, required=True)

    validate_parser = manifest_subparsers.add_parser(
        "validate",
        help="Validate a pinned manifest against a live MCP server.",
    )
    validate_parser.add_argument("manifest", type=Path)
    validate_parser.add_argument("--mcp-server", type=Path, required=True)

    diff_parser = manifest_subparsers.add_parser(
        "diff",
        help="Review drift between a pinned manifest and live MCP discovery.",
    )
    diff_parser.add_argument("manifest", type=Path)
    diff_parser.add_argument("--mcp-server", type=Path, required=True)
    diff_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the structured diff as JSON instead of a human-readable summary.",
    )

    check_manifest_parser = manifest_subparsers.add_parser(
        "check",
        help="Fail with exit code 1 when live MCP discovery drifts from a manifest.",
    )
    check_manifest_parser.add_argument("manifest", type=Path)
    check_manifest_parser.add_argument("--mcp-server", type=Path, required=True)
    check_manifest_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the structured diff as JSON instead of a human-readable summary.",
    )

    args = parser.parse_args()
    if args.command == "agent":
        return _run_agent_command(args, parser)
    if args.command == "manifest":
        if args.manifest_command == "generate":
            with MCPStdioClient([sys.executable, str(args.mcp_server)]) as client:
                manifest = manifest_from_client(client, args.source)
            write_manifest(manifest, args.output)
            print(f"Generated manifest: {args.output}")
            print(
                f"Pinned {len(manifest.tools)} MCP tool(s) from {manifest.server_name} {manifest.server_version}"
            )
            return 0

        manifest = load_manifest(args.manifest)
        if args.manifest_command in {"diff", "check"}:
            with MCPStdioClient([sys.executable, str(args.mcp_server)]) as client:
                observed = manifest_from_client(client, manifest.source)
            diff = diff_manifests(manifest, observed)
            if args.json:
                print(json.dumps(diff, indent=2, sort_keys=True))
            else:
                print(format_manifest_diff(diff))
            if args.manifest_command == "check" and diff["drift"]:
                return 1
            return 0
        with MCPStdioClient([sys.executable, str(args.mcp_server)]) as client:
            verify_server(client, manifest)
        print(f"Valid manifest: {args.manifest}")
        print(
            f"Verified {len(manifest.tools)} MCP tool(s) against {manifest.server_name} {manifest.server_version}"
        )
        return 0
    if args.command == "replay":
        try:
            print(replay(args.trace))
        except (ValueError, OSError) as exc:
            print(f"Replay rejected: {exc}", file=sys.stderr)
            return 2
        return 0

    try:
        program = load_program(args.source)
        check(program)
    except (ValueError, OSError) as exc:
        print(f"Check failed: {exc}", file=sys.stderr)
        return 2
    if args.command == "check":
        print(f"OK: {args.source}")
        return 0

    mcp_commands: dict[str, Sequence[str]] | None = None
    run_manifest: MCPManifest | None = None
    if args.mcp_server is not None:
        if args.mcp_manifest is None:
            parser.error("--mcp-manifest is required when --mcp-server is used")
        mcp_commands = {args.mcp_source: [sys.executable, str(args.mcp_server)]}
        run_manifest = load_manifest(args.mcp_manifest)
    elif args.mcp_manifest is not None:
        parser.error("--mcp-manifest requires --mcp-server")
    bindings: dict[str, ControlledProvider] = {}
    telemetry_error = False
    try:
        inputs = (
            strict_json(args.input.read_text(encoding="utf-8")) if args.input else {}
        )
        if not isinstance(inputs, dict):
            raise ValueError("Workflow input must be a JSON object")
        if bool(args.journal) != bool(args.binding_id) or (
            args.approval_record and not args.journal
        ):
            raise ValueError(
                "Use --journal with --binding-id; approvals also require a journal"
            )
        approvals = []
        if args.approval_record:
            record = strict_json(args.approval_record.read_text(encoding="utf-8"))
            if (
                not isinstance(record, dict)
                or set(record) - {"plan_hash", "action_hash", "approved_by", "reason"}
                or any(
                    not isinstance(record.get(key), str) or not record[key].strip()
                    for key in ("plan_hash", "action_hash", "approved_by")
                )
            ):
                raise ValueError(
                    "Approval record needs plan_hash, action_hash and approved_by strings"
                )
            approvals.append(ApprovalRecord(**record))
        session = (
            ExecutionSession(
                args.journal, binding_id=args.binding_id, approvals=approvals
            )
            if args.journal
            else None
        )
        limits = ModelLimits(
            max_requests=args.max_requests,
            max_tokens=args.max_tokens,
            max_output_tokens=args.max_output_tokens,
        )
        bindings = {
            name: ControlledProvider(provider, limits)
            for name, provider in _workflow_model_bindings(args).items()
        }
        result = run(
            program,
            args.workflow,
            args.trace,
            mcp_commands=mcp_commands,
            manifest=run_manifest,
            inputs=inputs,
            model_bindings=bindings,
            session=session,
        )
    except ApprovalPending as exc:
        print(json.dumps({"status": exc.code, **exc.request}, indent=2))
        return 3
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"Workflow failed: {exc}", file=sys.stderr)
        return 2
    finally:
        if args.telemetry:
            try:
                args.telemetry.write_text(
                    json.dumps(
                        {
                            name: provider.telemetry
                            for name, provider in bindings.items()
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except OSError as exc:
                print(f"Telemetry write failed: {exc}", file=sys.stderr)
                telemetry_error = True
    if telemetry_error:
        return 2
    print(result)
    print(f"Trace: {args.trace}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
