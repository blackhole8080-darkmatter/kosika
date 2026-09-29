from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .agent import AgentLoop, AgentLoopError, ApprovalRecord
from .approval_ledger import ApprovalLedger
from .assistant import AssistantError, KosikaAssistant
from .ast import ToolDecl
from .checker import check
from .controls import ControlledProvider, ExecutionError, ModelLimits
from .design import (
    DESIGN_THEMES,
    build_design_from_program,
    load_design_manifest,
    render_design,
    write_design_manifest,
)
from .durable import ApprovalPending, ExecutionSession
from .event_stream_design import (
    DesignError,
    build_timeline_design,
    publish_approval_event,
    timeline_stage_summary,
)
from .events import (
    EVENT_BRIDGE_VERSION as BRIDGE_VERSION,
)
from .events import (
    EVENT_SCHEMA,
    EventValidationError,
    read_jsonl,
    validate_event,
    write_event_jsonl,
)
from .execution import ExecutionLimits
from .explain import (
    ExplainError,
    build_explanation,
    to_explanation_json,
    to_explanation_mermaid,
    to_explanation_outsider,
    to_explanation_text,
)
from .extensions import (
    ExtensionManifestError,
    extension_manifest_digest,
    load_extension_manifest,
)
from .language_adapters import (
    SUPPORTED_LANGUAGES,
    LanguageAdapterError,
    adapter_for_suffix,
)
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
from .mindmap import build_mindmap, to_json, to_mermaid, to_text
from .model import (
    ModelProvider,
    ModelProviderError,
    ModelResponse,
    OpenAICompatibleProvider,
    ScriptedModelProvider,
)
from .model_steps import strict_json
from .modules import load_program
from .network import (
    NetworkDirectory,
    NetworkManifest,
    load_network_manifest,
    new_network_manifest,
    render_directory,
    render_network,
    write_network_manifest,
)
from .project_analysis import ProjectAnalysisError, analyze_python_project
from .runtime import MockMCP, replay, run
from .schema import schema_for_record, schema_for_tool_input, schemas_equal
from .source_map import (
    SourceMapError,
    build_source_map,
)
from .source_map import to_mermaid as render_source_map_mermaid
from .source_map import to_text as render_source_map_text
from .synapse_bridge import normalize_events

COMMANDS: tuple[str, ...] = (
    "project",
    "check",
    "analyze",
    "sourcemap",
    "mindmap",
    "explain",
    "assistant",
    "run",
    "agent",
    "replay",
    "network",
    "design",
    "manifest",
    "extension",
)


def analyze_source_file(path: Path) -> dict[str, Any]:
    """Analyze one source file with the adapter for its language."""

    adapter = adapter_for_suffix(path.suffix)
    if adapter is None:
        supported = ", ".join(sorted(SUPPORTED_LANGUAGES))
        raise LanguageAdapterError(
            f"No structural adapter for {path.suffix or 'this file type'}; "
            f"supported languages: {supported}"
        )
    return adapter.analyze_file(path)


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


def _assistant_provider(args: argparse.Namespace) -> ModelProvider | None:
    if args.fixture is not None:
        if args.model:
            raise ValueError("Use either --fixture or a live model, not both")
        payload = strict_json(args.fixture.read_text(encoding="utf-8"))
        responses: Any = payload
        if isinstance(payload, dict):
            responses = payload.get("responses")
        if (
            not isinstance(responses, list)
            or not responses
            or any(not isinstance(item, str) or not item.strip() for item in responses)
        ):
            raise ValueError(
                "Assistant fixture must be a nonempty JSON list of response strings"
            )
        return ScriptedModelProvider(
            [ModelResponse(content=response) for response in responses]
        )
    if args.model:
        return OpenAICompatibleProvider(
            args.model_url,
            args.model,
            api_key=os.environ.get("KOSIKA_MODEL_API_KEY"),
        )
    return None


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


def _timeline_event(
    sequence: int,
    event_type: str,
    status: str,
    *,
    workflow: str | None = None,
    error_type: str | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    """Build one validated normalized lifecycle event for the event stream."""

    event: dict[str, Any] = {
        "schema": EVENT_SCHEMA,
        "bridge_version": BRIDGE_VERSION,
        "event_id": f"kosika-cli-{sequence:04d}-{event_type}",
        "sequence": sequence,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "bus": "synapse",
        "source": "kosika",
        "region": "kosika",
        "type": event_type,
        "status": status,
        "replay": False,
    }
    if workflow is not None:
        event["workflow"] = workflow
    if message is not None:
        event["payload"] = {
            "message": message[:512],
            **({"error_type": error_type} if error_type else {}),
        }
    return validate_event(event)


def _cli_approval_event(sequence: int) -> dict[str, Any]:
    """One normalized approval-pause event using the shared lifecycle identity."""

    event = publish_approval_event(sequence)
    event["event_id"] = f"kosika-cli-{sequence:04d}-kosika_approval_pending"
    return validate_event(event)


def _publish_timeline(
    args: argparse.Namespace, events: Sequence[Mapping[str, Any]]
) -> None:
    """Write the deterministic timeline design when --design is supplied."""

    if args.design is None:
        return
    manifest = build_timeline_design(
        events, args.design_id, args.design_name
    )
    args.design.parent.mkdir(parents=True, exist_ok=True)
    write_design_manifest(manifest, args.design)
    summary = timeline_stage_summary(manifest)
    print(
        "Timeline design: "
        f"{args.design} (stage {summary['first_stage']}, "
        f"{summary['event_count']} event(s))"
    )


def _publish_run_events(
    args: argparse.Namespace, trace: Path
) -> tuple[dict[str, Any], ...] | None:
    """Load the completed raw trace, normalize it, and write the stream file."""

    assert args.event_trace is not None
    if args.event_trace.resolve() == trace.resolve():
        raise ValueError(
            "--event-trace must differ from the raw trace path; "
            "normalized and raw replay streams stay separate"
        )
    raw_events = read_jsonl(trace)
    if not raw_events:
        print("Event stream: raw trace is empty; nothing normalized to publish.")
        return None
    normalized = normalize_events(raw_events)
    write_event_jsonl(args.event_trace, normalized)
    return normalized


def main() -> int:
    parser = argparse.ArgumentParser(prog="kosika")
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{" + ",".join(COMMANDS) + "}",
    )

    project_parser = subparsers.add_parser(
        "project",
        help="Index a bounded Python project and its local imports without executing code.",
    )
    project_parser.add_argument("root", type=Path, help="Project directory to analyze.")
    project_parser.add_argument(
        "--format", choices=("json",), default="json", help="Project report format."
    )
    project_parser.add_argument("--output", type=Path, help="Optional JSON report path.")

    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("source", type=Path)

    analyze_parser = subparsers.add_parser(
        "analyze",
        help="Extract bounded, read-only structural facts from supported source files.",
    )
    analyze_parser.add_argument(
        "source", type=Path, help="Python, JavaScript, or TypeScript source file to analyze."
    )
    analyze_parser.add_argument(
        "--format", choices=("json", "text"), default="text", help="Output format."
    )

    sourcemap_parser = subparsers.add_parser(
        "sourcemap",
        help=(
            "Map Python, JavaScript, and TypeScript declarations in a directory "
            "into one readable, deterministic map."
        ),
    )
    sourcemap_parser.add_argument("root", type=Path, help="Directory to map.")
    sourcemap_parser.add_argument(
        "--format",
        choices=("text", "json", "mermaid"),
        default="text",
        help="Source map format.",
    )
    sourcemap_parser.add_argument(
        "--output", type=Path, help="Optional JSON report path."
    )

    mindmap_parser = subparsers.add_parser(
        "mindmap",
        help="Explain checked code as deterministic JSON, Mermaid, or text.",
    )
    mindmap_parser.add_argument("source", type=Path)
    mindmap_parser.add_argument(
        "--extension-manifest",
        action="append",
        type=Path,
        default=[],
        help="Extension manifest to include; may be repeated.",
    )
    mindmap_parser.add_argument(
        "--format",
        choices=("json", "mermaid", "text"),
        default="json",
        help="Mindmap representation to print.",
    )
    mindmap_parser.add_argument(
        "--output",
        type=Path,
        help="Write the representation to a file instead of stdout.",
    )

    explain_parser = subparsers.add_parser(
        "explain",
        help="Explain selected code and optionally validate a trace offline.",
    )
    explain_parser.add_argument("source", type=Path)
    explain_parser.add_argument(
        "--extension-manifest",
        action="append",
        type=Path,
        default=[],
        help="Extension manifest to include; may be repeated.",
    )
    explain_parser.add_argument(
        "--workflow",
        action="append",
        default=[],
        help="Workflow to explain; may be repeated. Defaults to the whole program.",
    )
    explain_parser.add_argument(
        "--record",
        action="append",
        default=[],
        help="Record contract to explain; may be repeated.",
    )
    explain_parser.add_argument(
        "--diagnostic",
        action="append",
        default=[],
        help="Diagnostic to include; may be repeated.",
    )
    explain_parser.add_argument(
        "--trace",
        type=Path,
        help="Completed JSONL trace to validate and summarize offline.",
    )
    explain_parser.add_argument(
        "--format",
        choices=("text", "json", "mermaid", "outsider"),
        default="text",
        help="Explanation representation to print.",
    )
    explain_parser.add_argument(
        "--output",
        type=Path,
        help="Write the representation to a file instead of stdout.",
    )

    assistant_parser = subparsers.add_parser(
        "assistant",
        help="Explain checked Kosika code with a fixture or optional local model.",
    )
    assistant_parser.add_argument("source", type=Path)
    assistant_parser.add_argument("--task", required=True)
    assistant_parser.add_argument(
        "--source-file",
        action="append",
        type=Path,
        default=[],
        help="Read-only Python source file to include as grounded assistant context; repeatable.",
    )
    assistant_parser.add_argument(
        "--project",
        action="append",
        type=Path,
        default=[],
        help="Bounded project root to analyze for grounded assistant context; repeatable.",
    )
    assistant_parser.add_argument(
        "--diagnostic",
        action="append",
        default=[],
        help="Diagnostic to include as assistant context; may be repeated.",
    )
    assistant_parser.add_argument(
        "--fixture",
        type=Path,
        help="Offline JSON list (or {\"responses\": [...]}) of answer strings.",
    )
    assistant_parser.add_argument(
        "--model-url",
        default=os.environ.get("KOSIKA_MODEL_URL", "http://localhost:11434/v1"),
        help="OpenAI-compatible model URL, or KOSIKA_MODEL_URL.",
    )
    assistant_parser.add_argument(
        "--model",
        default=os.environ.get("KOSIKA_MODEL"),
        help="Optional local model name, or KOSIKA_MODEL.",
    )
    assistant_parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=1024,
        help="Maximum output tokens requested from a configured model.",
    )
    assistant_parser.add_argument(
        "--json",
        action="store_true",
        help="Print answer, diagnostics, and mindmap as structured JSON.",
    )

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
    run_parser.add_argument("--max-function-calls", type=int, default=10000)
    run_parser.add_argument("--max-workflow-steps", type=int, default=10000)
    run_parser.add_argument("--max-model-calls", type=int, default=64)
    run_parser.add_argument("--max-tool-calls", type=int, default=256)
    run_parser.add_argument("--deadline-seconds", type=float)
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
        "--event-trace",
        type=Path,
        help="Optional normalized event JSONL for SSE replay and timeline views.",
    )
    run_parser.add_argument(
        "--design",
        type=Path,
        help="Write a deterministic event-timeline design manifest from the run.",
    )
    run_parser.add_argument(
        "--design-id",
        default="event-timeline",
        help="Design id recorded in the timeline manifest (with --design).",
    )
    run_parser.add_argument(
        "--design-name",
        default="Kosika run timeline",
        help="Design name recorded in the timeline manifest (with --design).",
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

    network_parser = subparsers.add_parser(
        "network",
        help="Create, validate, inspect, and index declarative Kosika networks.",
    )
    network_subparsers = network_parser.add_subparsers(
        dest="network_command", required=True
    )
    network_init_parser = network_subparsers.add_parser(
        "init", help="Create an empty network manifest."
    )
    network_init_parser.add_argument("path", type=Path)
    network_init_parser.add_argument("--id", required=True)
    network_init_parser.add_argument("--name", required=True)
    network_validate_parser = network_subparsers.add_parser(
        "validate", help="Validate a network manifest without connecting to targets."
    )
    network_validate_parser.add_argument("path", type=Path)
    network_show_parser = network_subparsers.add_parser(
        "show", help="Render a network manifest as text, JSON, or Mermaid."
    )
    network_show_parser.add_argument("path", type=Path)
    network_show_parser.add_argument(
        "--format", choices=("text", "json", "mermaid"), default="text"
    )
    network_index_parser = network_subparsers.add_parser(
        "index", help="Build a deterministic index of network manifests."
    )
    network_index_parser.add_argument("directory", type=Path)
    network_index_parser.add_argument("--recursive", action="store_true")
    network_index_parser.add_argument(
        "--format", choices=("text", "json"), default="text"
    )

    design_parser = subparsers.add_parser(
        "design",
        help="Generate and render deterministic agent-workflow vector designs.",
    )
    design_subparsers = design_parser.add_subparsers(
        dest="design_command", required=True
    )
    design_generate_parser = design_subparsers.add_parser(
        "generate", help="Generate a design manifest from a Kosika source file."
    )
    design_generate_parser.add_argument("source", type=Path)
    design_generate_parser.add_argument("--output", type=Path, required=True)
    design_generate_parser.add_argument("--id", required=True)
    design_generate_parser.add_argument("--name", required=True)
    design_generate_parser.add_argument("--network", type=Path)
    design_validate_parser = design_subparsers.add_parser(
        "validate", help="Validate a design manifest."
    )
    design_validate_parser.add_argument("path", type=Path)
    design_show_parser = design_subparsers.add_parser(
        "show", help="Render a design manifest as text, JSON, Mermaid, or SVG."
    )
    design_show_parser.add_argument("path", type=Path)
    design_show_parser.add_argument(
        "--format", choices=("text", "json", "mermaid", "svg"), default="text"
    )
    design_show_parser.add_argument(
        "--view", choices=("workflow", "network", "mindmap"), default="workflow"
    )
    design_show_parser.add_argument(
        "--theme",
        choices=DESIGN_THEMES,
        default="lab",
        help="SVG presentation theme. Never affects the design digest.",
    )
    design_show_parser.add_argument("--output", type=Path)

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

    extension_parser = subparsers.add_parser(
        "extension",
        help="Validate a Kosika extension manifest.",
    )
    extension_subparsers = extension_parser.add_subparsers(
        dest="extension_command", required=True
    )
    extension_check_parser = extension_subparsers.add_parser(
        "check",
        help="Validate an extension manifest without activating it.",
    )
    extension_check_parser.add_argument("manifest", type=Path)
    extension_check_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the structured validation summary as JSON.",
    )

    args = parser.parse_args()
    if args.command == "project":
        try:
            report = analyze_python_project(args.root)
            rendered = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False)
            if args.output is not None:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(rendered + "\n", encoding="utf-8")
                print(f"Project analysis written: {args.output}")
                print(json.dumps(report["summary"], sort_keys=True))
            else:
                print(rendered)
            return 0
        except (OSError, ProjectAnalysisError, ValueError) as exc:
            print(f"Project analysis failed safely: {exc}", file=sys.stderr)
            return 2
    if args.command == "network":
        try:
            if args.network_command == "init":
                network_manifest = new_network_manifest(args.id, args.name)
                write_network_manifest(network_manifest, args.path)
                print(f"Initialized network manifest: {args.path}")
                return 0
            if args.network_command == "validate":
                network_manifest = load_network_manifest(args.path)
                print(f"Valid network manifest: {args.path}")
                print(
                    f"Network {network_manifest.network_id} has "
                    f"{len(network_manifest.nodes)} node(s), "
                    f"{len(network_manifest.links)} link(s), "
                    f"{len(network_manifest.modules)} module(s), "
                    f"and {len(network_manifest.memory_graphs)} memory graph(s)"
                )
                return 0
            if args.network_command == "show":
                print(render_network(load_network_manifest(args.path), args.format))
                return 0
            directory = NetworkDirectory.load(
                args.directory, recursive=args.recursive
            )
            print(render_directory(directory, args.format))
            return 0
        except (OSError, ValueError) as exc:
            print(f"Network failed: {exc}", file=sys.stderr)
            return 2
    if args.command == "analyze":
        try:
            analysis = analyze_source_file(args.source)
            if args.format == "json":
                print(json.dumps(analysis, indent=2, sort_keys=True))
            else:
                print(
                    f"{analysis['language'].title()} structure: {analysis['source']['path']} "
                    f"({analysis['source']['sha256'][:12]})"
                )
                print(
                    f"Support: {analysis['support_level']}; "
                    f"{len(analysis['symbols'])} symbol(s), "
                    f"{len(analysis['imports'])} import(s), "
                    f"{len(analysis['observed_effects'])} obvious effect(s) observed"
                )
                for symbol in analysis["symbols"]:
                    print(
                        f"- {symbol['kind']} {symbol['qualified_name']} "
                        f"({symbol['start_line']}-{symbol['end_line']})"
                    )
                if analysis.get("uncertainties"):
                    print("Uncertainties reported during scanning:")
                    for item in analysis["uncertainties"]:
                        print(f"- line {item['line']}: {item['reason']}")
                print("Limitations: syntax-backed structure only; no execution or full semantic analysis.")
            return 0
        except (OSError, ValueError) as exc:
            print(f"Source analysis failed safely: {exc}", file=sys.stderr)
            return 2
    if args.command == "sourcemap":
        try:
            report = build_source_map(args.root)
            if args.format == "json":
                print(json.dumps(report, indent=2, sort_keys=True))
            elif args.format == "mermaid":
                print(render_source_map_mermaid(report))
            else:
                print(render_source_map_text(report))
            if args.output is not None:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(
                    json.dumps(report, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                print(f"Source map written: {args.output}", file=sys.stderr)
            return 0
        except (OSError, SourceMapError, ValueError) as exc:
            print(f"Source map failed safely: {exc}", file=sys.stderr)
            return 2
    if args.command == "design":
        try:
            if args.design_command == "generate":
                program = load_program(args.source)
                check(program)
                attached_network: NetworkManifest | None = None
                if args.network is not None:
                    attached_network = load_network_manifest(args.network)
                design_manifest = build_design_from_program(
                    program,
                    args.id,
                    args.name,
                    network=attached_network,
                )
                write_design_manifest(design_manifest, args.output)
                print(f"Generated design manifest: {args.output}")
                print(
                    f"Design contains {len(design_manifest.nodes)} node(s) and "
                    f"{len(design_manifest.edges)} edge(s)"
                )
                return 0
            design_manifest = load_design_manifest(args.path)
            if args.design_command == "validate":
                print(f"Valid design manifest: {args.path}")
                print(
                    f"Design contains {len(design_manifest.nodes)} node(s), "
                    f"{len(design_manifest.edges)} edge(s), and "
                    f"{len(design_manifest.views)} view(s)"
                )
                return 0
            rendered = render_design(
                design_manifest, args.format, args.view, getattr(args, "theme", "lab")
            )
            if args.output is not None:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(rendered + "\n", encoding="utf-8")
                print(f"Rendered {args.format} design: {args.output}")
            else:
                print(rendered)
            return 0
        except (OSError, ValueError) as exc:
            print(f"Design failed: {exc}", file=sys.stderr)
            return 2
    if args.command == "agent":
        return _run_agent_command(args, parser)
    if args.command == "extension":
        try:
            extension_manifest = load_extension_manifest(args.manifest)
        except ExtensionManifestError as exc:
            print(f"Extension check failed: {exc}", file=sys.stderr)
            return 2
        extension_summary: dict[str, Any] = {
            "valid": True,
            "id": extension_manifest.id,
            "version": extension_manifest.version,
            "compatibility": {
                "minVersion": extension_manifest.compatibility.min_version,
                "maxVersion": extension_manifest.compatibility.max_version,
            },
            "contributions": [
                {
                    "kind": contribution.kind,
                    "name": contribution.name,
                    "permissions": list(contribution.permissions),
                }
                for contribution in extension_manifest.contributions
            ],
            "permissions": list(extension_manifest.permissions),
            "healthChecks": len(extension_manifest.health_checks),
            "digest": extension_manifest_digest(extension_manifest),
        }
        if args.extension_command == "check" and args.json:
            print(json.dumps(extension_summary, indent=2, sort_keys=True))
        else:
            print(f"Valid extension manifest: {args.manifest}")
            print(
                f"Extension: {extension_manifest.id} {extension_manifest.version}"
            )
            print(f"Contributions: {len(extension_manifest.contributions)}")
            print(f"Health checks: {len(extension_manifest.health_checks)}")
            print(f"Digest: {extension_summary['digest']}")
        return 0
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

    if args.command == "analyze":
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
    if args.command == "mindmap":
        try:
            extension_manifests = tuple(
                load_extension_manifest(path) for path in args.extension_manifest
            )
            mindmap = build_mindmap(
                program, extension_manifests=extension_manifests
            )
            rendered = {
                "json": to_json,
                "mermaid": to_mermaid,
                "text": to_text,
            }[args.format](mindmap)
            if args.output is not None:
                args.output.write_text(rendered, encoding="utf-8")
                print(f"Mindmap written: {args.output}")
            else:
                print(rendered, end="")
        except (OSError, ValueError) as exc:
            print(f"Mindmap failed: {exc}", file=sys.stderr)
            return 2
        return 0
    if args.command == "explain":
        try:
            extension_manifests = tuple(
                load_extension_manifest(path) for path in args.extension_manifest
            )
            explanation = build_explanation(
                program,
                workflows=args.workflow,
                records=args.record,
                diagnostics=args.diagnostic,
                trace_path=args.trace,
                extension_manifests=extension_manifests,
            )
            rendered = {
                "json": to_explanation_json,
                "mermaid": to_explanation_mermaid,
                "text": to_explanation_text,
                "outsider": to_explanation_outsider,
            }[args.format](explanation)
            if args.output is not None:
                args.output.write_text(rendered, encoding="utf-8")
                print(f"Explanation written: {args.output}")
            else:
                print(rendered, end="")
        except (ExplainError, OSError, ValueError) as exc:
            print(f"Explain failed: {exc}", file=sys.stderr)
            return 2
        return 0
    if args.command == "assistant":
        try:
            provider = _assistant_provider(args)
            source_analyses = tuple(
                analyze_source_file(path) for path in args.source_file
            )
            project_analyses = tuple(
                analyze_python_project(path) for path in args.project
            )
            result = KosikaAssistant(
                program,
                provider,
                max_output_tokens=args.max_output_tokens,
            ).ask(
                args.task,
                diagnostics=args.diagnostic,
                source_analyses=source_analyses,
                project_analyses=project_analyses,
            )
            if args.json:
                print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
            else:
                print(result.answer)
                print(f"Assistant mode: {result.mode}")
                summary = result.mindmap.get("summary", {})
                if not isinstance(summary, dict):
                    summary = {}
                print(
                    "Mindmap: "
                    f"{summary.get('node_count', 0)} nodes, "
                    f"{summary.get('edge_count', 0)} edges"
                )
        except (
            AssistantError,
            ModelProviderError,
            OSError,
            ProjectAnalysisError,
            ValueError,
        ) as exc:
            print(f"Assistant failed safely: {exc}", file=sys.stderr)
            return 2
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
        run_result = run(
            program,
            args.workflow,
            args.trace,
            mcp_commands=mcp_commands,
            manifest=run_manifest,
            inputs=inputs,
            model_bindings=bindings,
            session=session,
            limits=ExecutionLimits(
                max_function_calls=args.max_function_calls,
                max_steps=args.max_workflow_steps,
                max_model_calls=args.max_model_calls,
                max_tool_calls=args.max_tool_calls,
                deadline_seconds=args.deadline_seconds,
            ),
        )
        published_events: tuple[dict[str, Any], ...] | None = None
        if args.event_trace is not None:
            published_events = _publish_run_events(args, args.trace)
        if args.design is not None and published_events is not None:
            _publish_timeline(args, published_events)
    except ApprovalPending as exc:
        normalized = None
        if args.event_trace is not None:
            try:
                normalized = write_event_jsonl(
                    args.event_trace,
                    [_cli_approval_event(1)],
                )
            except (EventValidationError, OSError) as stream_exc:
                print(f"Event stream write failed: {stream_exc}", file=sys.stderr)
                return 2
            try:
                _publish_timeline(args, normalized)
            except (DesignError, OSError) as design_exc:
                print(f"Timeline design failed: {design_exc}", file=sys.stderr)
                return 2
        print(json.dumps({"status": exc.code, **exc.request}, indent=2))
        return 3
    except ExecutionError as exc:
        print(f"Workflow stopped ({exc.code}): {exc}", file=sys.stderr)
        return 130 if exc.code == "cancelled" else 2
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
    print(run_result)
    print(f"Trace: {args.trace}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
