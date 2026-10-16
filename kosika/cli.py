from __future__ import annotations

import argparse
import contextlib
import getpass
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .agent import AgentLoop, AgentLoopError, ApprovalRecord
from .approval_ledger import ApprovalLedger
from .ast import Program, ToolDecl
from .checker import check
from .command_model import CommandModelProvider
from .components import (
    ComponentContractError,
    bind_component,
    load_component_contract,
    verify_component_artifact,
)
from .controls import ControlledProvider, ExecutionError, ModelLimits
from .development import check_project, execute_development_json
from .durable import (
    PLAN_TOOL,
    AgentExecutionSession,
    ApprovalPending,
    ExecutionSession,
    inspect_journal,
)
from .editor_features import EditorFeatureError, build_editor_features
from .environment import DECIDED as ENVIRONMENT_DECIDED
from .environment import Environment, EnvironmentError_, find_environment
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
from .lessons import Lessons
from .manifest import (
    ManifestError,
    MCPManifest,
    diff_manifests,
    format_manifest_diff,
    load_manifest,
    manifest_from_client,
    verify_server,
    write_manifest,
)
from .mcp_stdio import MCPProtocolError, MCPStdioClient
from .memory import bind_memory_tools
from .mindmap import build_mindmap, to_json, to_mermaid, to_text
from .model import (
    MODEL_PROTOCOLS,
    ModelProvider,
    ModelProviderError,
    ModelResponse,
    ModelToolCall,
    ModelUsage,
    OpenAICompatibleProvider,
    ScriptedModelProvider,
)
from .model_steps import strict_json
from .modules import load_program
from .network import (
    NetworkDirectory,
    load_network_manifest,
    new_network_manifest,
    render_directory,
    render_network,
    write_network_manifest,
)
from .project_analysis import ProjectAnalysisError, analyze_python_project
from .runtime import MockMCP, replay, run
from .schema import schema_for_record, schema_for_tool_input, schemas_equal
from .skills import (
    SKILLS_FOLDER,
    SkillCatalog,
    SkillCatalogError,
    SkillRouter,
    SkillRoutingError,
    load_skill_notes,
)
from .source_map import (
    SourceMapError,
    build_source_map,
)
from .source_map import to_mermaid as render_source_map_mermaid
from .source_map import to_text as render_source_map_text
from .synapse_bridge import normalize_events
from .system_tools import bind_system_tools


def command_metavar(subparsers: Any) -> str:
    """Build the top-level usage list from the parsers that are registered.

    The list used to be a hand-maintained tuple, which silently drifted: every
    new command had to be remembered in two places, and the missing entries were
    invisible in `--help` because argparse still printed their help lines. The
    metavar is therefore derived from the subparsers themselves, so registering
    a parser is the single act that makes a command discoverable.
    """

    return "{" + ",".join(subparsers.choices) + "}"


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


def _render_journal_text(report: dict[str, Any]) -> str:
    lines = [
        f"Journal: {report['path']}",
        f"Kind: {report['kind']}",
    ]
    agent = report.get("agent")
    if isinstance(agent, dict):
        lines.extend(
            [
                f"State: {agent['state']}",
                f"Plan hash: {agent['plan_hash']}",
                f"Binding ID: {agent['binding_id']}",
                f"Checkpoint: {json.dumps(agent['checkpoint'], sort_keys=True)}",
            ]
        )
    workflow = report.get("workflow")
    if isinstance(workflow, dict):
        steps = workflow.get("steps", [])
        lines.append(f"Workflow steps: {len(steps)}")
        for item in steps:
            lines.append(
                f"  {json.dumps(item['step'], separators=(',', ':'))}: {item['state']}"
            )
    return "\n".join(lines)


def _agent_model_fixture(path: Path, model_name: str) -> ModelProvider:
    payload = strict_json(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or model_name not in payload:
        raise ValueError(
            f"Agent model fixture must contain responses for {model_name!r}"
        )
    raw_responses = payload[model_name]
    if (
        not isinstance(raw_responses, list)
        or not raw_responses
        or any(not isinstance(item, dict) for item in raw_responses)
    ):
        raise ValueError(
            f"Agent model fixture {model_name!r} requires a nonempty response list"
        )
    responses: list[ModelResponse] = []
    for index, item in enumerate(raw_responses):
        content = item.get("content")
        if content is not None and not isinstance(content, str):
            raise ValueError(
                f"Agent model fixture response {index} content must be text or null"
            )
        finish_reason = item.get("finish_reason", "stop")
        if not isinstance(finish_reason, str):
            raise ValueError(
                f"Agent model fixture response {index} finish_reason must be text"
            )
        raw_calls = item.get("tool_calls", [])
        if not isinstance(raw_calls, list):
            raise ValueError(
                f"Agent model fixture response {index} tool_calls must be a list"
            )
        calls: list[ModelToolCall] = []
        for call in raw_calls:
            if (
                not isinstance(call, dict)
                or not isinstance(call.get("id"), str)
                or not isinstance(call.get("name"), str)
                or not isinstance(call.get("arguments"), dict)
            ):
                raise ValueError(
                    f"Agent model fixture response {index} contains an invalid tool call"
                )
            calls.append(ModelToolCall(call["id"], call["name"], call["arguments"]))
        usage = item.get("usage")
        if usage is not None and not isinstance(usage, dict):
            raise ValueError(
                f"Agent model fixture response {index} usage must be an object"
            )
        try:
            model_usage = ModelUsage(**usage) if usage is not None else None
        except TypeError as exc:
            raise ValueError(
                f"Agent model fixture response {index} usage is invalid"
            ) from exc
        responses.append(
            ModelResponse(
                content=content,
                tool_calls=tuple(calls),
                finish_reason=finish_reason,
                usage=model_usage,
            )
        )
    return ScriptedModelProvider(responses)


def _agent_tool_fixture(path: Path) -> dict[str, Any]:
    payload = strict_json(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not payload:
        raise ValueError("Agent tool fixture must be a nonempty object")
    for name, value in payload.items():
        if not isinstance(name, str) or not isinstance(value, (dict, list)):
            raise ValueError(
                "Agent tool fixture values must be output records or record lists"
            )
        if isinstance(value, list) and (
            not value or any(not isinstance(item, dict) for item in value)
        ):
            raise ValueError("Agent tool fixture record lists must be nonempty objects")
    return payload


def _agent_tool_fixture_result(fixture: dict[str, Any], declaration: ToolDecl) -> Any:
    value = fixture.get(declaration.name, fixture.get(declaration.operation))
    if value is None:
        raise AgentLoopError(
            f"Agent tool fixture has no result for {declaration.name!r}"
        )
    if isinstance(value, list):
        if not value:
            raise AgentLoopError(
                f"Agent tool fixture has no remaining result for {declaration.name!r}"
            )
        return value.pop(0)
    return value


def _run_agent_command(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> int:
    if args.mcp_server is not None and args.mcp_manifest is None:
        parser.error("--mcp-manifest is required when --mcp-server is used")
    if args.mcp_server is None and args.mcp_manifest is not None:
        parser.error("--mcp-manifest requires --mcp-server")
    if args.tool_fixture is not None and args.mcp_server is not None:
        parser.error("--tool-fixture cannot be combined with --mcp-server")
    if args.tool_fixture is not None and args.mcp_manifest is not None:
        parser.error("--tool-fixture cannot be combined with --mcp-manifest")

    if (args.approval_plan_hash is None) != (args.approval_action_hash is None):
        parser.error(
            "--approval-plan-hash and --approval-action-hash must be used together"
        )
    if args.resume and args.journal is None:
        parser.error("--resume requires --journal")
    if args.journal is not None and not args.binding_id:
        parser.error("--binding-id is required with --journal")

    program = load_program(args.source)
    check(program)
    agent_decl = next(
        (agent for agent in program.agents if agent.name == args.agent),
        None,
    )
    if agent_decl is None:
        parser.error(f"Unknown Kosika agent {args.agent!r}")
    declared_model = next(
        (model for model in program.models if model.name == agent_decl.model),
        None,
    )
    model_name = args.model or (
        declared_model.deployment if declared_model is not None else None
    )
    if args.model_fixture is not None and not model_name and declared_model is not None:
        model_name = declared_model.name
    if not model_name:
        parser.error(
            "--model is required unless the selected agent references a model "
            "with a deployment (or set KOSIKA_MODEL), or --model-fixture is used"
        )
    input_value: dict[str, Any] | None = None
    if args.input is not None:
        try:
            parsed_input = strict_json(args.input.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError) as exc:
            parser.error(f"Invalid agent input JSON: {exc}")
        if not isinstance(parsed_input, dict):
            parser.error("Agent input must be a JSON object")
        input_value = parsed_input
    if declared_model is not None and input_value is None:
        parser.error(
            "--input is required for agents with a declared model; "
            "the file must match the model input record"
        )
    if declared_model is None and input_value is not None:
        parser.error("--input requires the selected agent to declare a model")
    task = args.task or "Run the agent."
    try:
        tool_fixture = (
            _agent_tool_fixture(args.tool_fixture)
            if args.tool_fixture is not None
            else None
        )
    except (OSError, TypeError, ValueError) as exc:
        print(f"Agent fixture failed safely: {exc}", file=sys.stderr)
        return 2
    try:
        skill_router = SkillRouter(SkillCatalog.from_directories(args.skill_dir))
    except SkillCatalogError as exc:
        print(f"Skill catalog failed: {exc}", file=sys.stderr)
        return 2
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
            if tool_fixture is not None:
                return _agent_tool_fixture_result(tool_fixture, declaration)
            return MockMCP().call(declaration.source, declaration.operation)

        try:
            provider = (
                _agent_model_fixture(args.model_fixture, model_name)
                if args.model_fixture is not None
                else OpenAICompatibleProvider(
                    args.model_url,
                    model_name,
                    api_key=os.environ.get("KOSIKA_MODEL_API_KEY"),
                )
            )
        except (OSError, TypeError, ValueError) as exc:
            print(f"Agent fixture failed safely: {exc}", file=sys.stderr)
            return 2
        loop = AgentLoop(
            program,
            provider,
            execute_tool,
            max_steps=args.max_steps,
            deadline_seconds=args.deadline_seconds,
            approval_ledger=ApprovalLedger(args.approval_ledger)
            if args.approval_ledger is not None
            else None,
            skill_router=skill_router,
            skill_policy=args.skill_policy,
            explicit_skills=tuple(args.skill),
            max_skills=args.max_skills,
        )
        agent_session = (
            AgentExecutionSession(
                args.journal,
                binding_id=args.binding_id,
                resume=args.resume,
            )
            if args.journal is not None
            else None
        )
        try:
            current_plan_hash = loop.plan_hash_for(
                args.agent,
                task,
                input_value=input_value,
            )
            approval = None
            if args.approval_plan_hash is not None:
                approval = ApprovalRecord(
                    plan_hash=args.approval_plan_hash,
                    approved_by=args.approved_by,
                    action_hash=args.approval_action_hash,
                )
            print(f"Plan hash: {current_plan_hash}")
            result = loop.run(
                args.agent,
                task,
                input_value=input_value,
                system_prompt=args.system_prompt,
                approval=approval,
                trace_path=args.trace,
                session=agent_session,
            )
        except (
            AgentLoopError,
            ModelProviderError,
            MCPProtocolError,
            SkillRoutingError,
        ) as exc:
            print(f"Agent failed safely: {exc}", file=sys.stderr)
            return 2
        if result.selected_skills:
            print(f"Skills: {', '.join(result.selected_skills)}")
        if result.structured_output is not None:
            print(json.dumps(result.structured_output, indent=2, sort_keys=True))
        else:
            print(result.content)
        if args.trace is not None:
            print(f"Trace: {args.trace}")
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


def _model_fixture_bindings(path: Path) -> dict[str, ModelProvider]:
    payload = strict_json(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(
            "Model fixture must map model names to lists of output records"
        )
    providers: dict[str, ModelProvider] = {}
    for name, results in payload.items():
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


DEFAULT_LOCAL_MODEL_URL = "http://localhost:11434/v1"


def _meaning(args: argparse.Namespace) -> Any:
    """The search-by-meaning grant, or None when no embedding model is named."""

    name = getattr(args, "embed_model", None)
    if not name:
        return None
    from .embeddings import Meaning, default_store, ollama_embedder

    url = getattr(args, "model_url", None) or DEFAULT_LOCAL_MODEL_URL
    # Embeddings are asked of Ollama's own address, without the /v1 that
    # its OpenAI-compatible chat address ends in.
    base = url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return Meaning(ollama_embedder(name, base), name, default_store())


def _model_route_bindings(
    path: Path, program: Program, default_protocol: str, timeout: float = 60.0
) -> dict[str, ModelProvider]:
    """Read `--models FILE`: one real model for each declared model it names.

    The file belongs to the person running the program. A `.kos` file can
    name a model such as `cheap`; only this file says what `cheap` is.
    """

    try:
        routes = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read --models file {path}: {exc}") from exc
    declared = {model.name for model in program.models if model.provider is None}
    if not isinstance(routes, dict) or not routes:
        raise ValueError("--models file must map declared model names to routes")
    bindings: dict[str, ModelProvider] = {}
    for name, route in routes.items():
        if name not in declared:
            known = ", ".join(sorted(declared)) or "none"
            raise ValueError(
                f"--models names {name!r}, which this program does not "
                f"declare; declared: {known}"
            )
        if not isinstance(route, dict):
            raise ValueError(f"--models route for {name!r} must be an object")
        route_command = route.get("command")
        if route_command is not None:
            if (
                set(route) != {"command"}
                or not isinstance(route_command, list)
                or not route_command
                or not all(isinstance(part, str) for part in route_command)
            ):
                raise ValueError(
                    f"--models route for {name!r}: command must be a list of "
                    "text, with no other key"
                )
            bindings[name] = CommandModelProvider(route_command)
            where = f"command {route_command[0]!r}"
        else:
            unknown = set(route) - {"model", "url", "protocol"}
            model_id = route.get("model")
            if unknown or not isinstance(model_id, str) or not model_id:
                raise ValueError(
                    f"--models route for {name!r} needs a model name and may "
                    "set only url and protocol"
                )
            protocol = route.get("protocol", default_protocol)
            if protocol not in MODEL_PROTOCOLS:
                raise ValueError(
                    f"--models route for {name!r} has unknown protocol {protocol!r}"
                )
            url = (
                route.get("url")
                or os.environ.get("KOSIKA_MODEL_URL")
                or (
                    DEFAULT_LOCAL_MODEL_URL.removesuffix("/v1")
                    if protocol == "ollama"
                    else DEFAULT_LOCAL_MODEL_URL
                )
            )
            bindings[name] = OpenAICompatibleProvider(
                url,
                model_id,
                api_key=os.environ.get("KOSIKA_MODEL_API_KEY"),
                protocol=protocol,
                timeout=timeout,
            )
            where = f"model {model_id!r} at {url}"
        print(f"{name}: {where}", file=sys.stderr)
    return bindings


def _workflow_model_bindings(
    args: argparse.Namespace, program: Program
) -> dict[str, ModelProvider]:
    live = (args.model_binding, args.model, args.model_url)
    command = getattr(args, "model_command", None)
    routes = getattr(args, "models", None)
    if routes is not None:
        if any(live) or command is not None or args.model_fixture is not None:
            raise ValueError(
                "Use --models on its own, without --model, --model-url, "
                "--model-binding, --model-command, or --model-fixture"
            )
        return _model_route_bindings(
            routes,
            program,
            args.model_protocol,
            getattr(args, "model_timeout", None) or 60.0,
        )
    if command is not None:
        if any(live) or args.model_fixture is not None:
            raise ValueError(
                "Use --model-command on its own, without --model, --model-url, "
                "--model-binding, or --model-fixture"
            )
        declared_models = [m.name for m in program.models if m.provider is None]
        if not declared_models:
            raise ValueError(
                "--model-command was given, but this program declares no model"
            )
        provider_from_command = CommandModelProvider(command)
        print(
            f"Command {command[0]!r} answers for: {', '.join(declared_models)}",
            file=sys.stderr,
        )
        return dict.fromkeys(declared_models, provider_from_command)
    if args.model_fixture is not None:
        if any(live):
            raise ValueError(
                "Use either --model-fixture or an explicit live model binding"
            )
        return _model_fixture_bindings(args.model_fixture)

    if not any(live):
        return {}
    if not args.model:
        raise ValueError(
            "A live binding needs --model NAME. Add --model-binding to grant "
            "one declared model, or omit it to grant every declared model."
        )
    # Thinking steps' generated models borrow a declared model's binding.
    declared = [model.name for model in program.models if model.provider is None]
    if args.model_binding is not None:
        if not args.model_url:
            raise ValueError("--model-binding requires --model and --model-url")
        granted = [args.model_binding]
        url = args.model_url
    else:
        # `--model NAME` on its own is the short form: the person running the
        # program grants that one provider to every model the program declares.
        if not declared:
            raise ValueError("--model was given, but this program declares no model")
        granted = declared
        url = (
            args.model_url
            or os.environ.get("KOSIKA_MODEL_URL")
            or (
                DEFAULT_LOCAL_MODEL_URL.removesuffix("/v1")
                if args.model_protocol == "ollama"
                else DEFAULT_LOCAL_MODEL_URL
            )
        )
        print(
            f"Model {args.model!r} at {url} answers for: {', '.join(granted)}",
            file=sys.stderr,
        )
    # `--model fast,strong=big`: every declared model is answered by `fast`,
    # except the one the program calls `strong`, which is answered by `big`.
    base, *others = [part.strip() for part in args.model.split(",")]
    overrides: dict[str, str] = {}
    for part in others:
        declared_name, found, real = part.partition("=")
        if not found or not declared_name.strip() or not real.strip():
            raise ValueError(
                f"--model part {part!r} must look like NAME=MODEL, where NAME "
                "is a model the program declares"
            )
        if declared_name.strip() not in granted:
            raise ValueError(
                f"--model names {declared_name.strip()!r}, which is not a model "
                f"this program declares; it declares: {', '.join(granted)}"
            )
        overrides[declared_name.strip()] = real.strip()
    if not base or "=" in base:
        raise ValueError("--model must begin with the model that answers by default")

    def provider_for(name: str) -> ModelProvider:
        return OpenAICompatibleProvider(
            url,
            name,
            api_key=os.environ.get("KOSIKA_MODEL_API_KEY"),
            protocol=args.model_protocol,
            timeout=getattr(args, "model_timeout", None) or 60.0,
        )

    made = {name: provider_for(name) for name in {base, *overrides.values()}}
    return {name: made[overrides.get(name, base)] for name in granted}


def _failure_line(source: Path) -> str:
    """Where the first diagnostic points, when a truthful position exists."""

    try:
        report = check_project(source)["diagnostics"]
        position = report["diagnostics"][0]["range"]
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return ""
    if not isinstance(position, dict):
        return ""
    start = position["start"]
    return f" (line {start['line'] + 1}, column {start['character'] + 1})"


def _ask_approval(
    request: dict[str, Any], approved_by: str | None
) -> ApprovalRecord | None:
    """Show one pending action and ask the person at the terminal about it.

    Returns an approval bound to exactly that action, or None when it is
    refused or nobody is there to ask. The answer covers this one call with
    these arguments only; the next action is asked about separately.
    """

    if not sys.stdin.isatty():
        return None
    is_plan = request["tool"] == PLAN_TOOL
    if is_plan:
        print(
            f"\nAgent {request['arguments']['agent']} wants to carry out this "
            "plan, in this order, and nothing else:",
            file=sys.stderr,
        )
        for at, action in enumerate(request["arguments"]["actions"], 1):
            print(
                f"  {at}. {action['tool']}  "
                f"{json.dumps(action['arguments'], ensure_ascii=False)}",
                file=sys.stderr,
            )
        for warning in request["arguments"].get("warnings", []):
            print(f"  ! checked and found wrong: {warning}", file=sys.stderr)
    else:
        print(
            "\nApproval needed before this action runs:\n"
            f"  step      : {' / '.join(request['step'])}\n"
            f"  tool      : {request['tool']}\n"
            f"  arguments : {json.dumps(request['arguments'], ensure_ascii=False)}",
            file=sys.stderr,
        )
    try:
        answer = input(
            "Approve this whole plan? [y/N] "
            if is_plan
            else "Approve this one action? [y/N] "
        )
    except EOFError:
        return None
    if answer.strip().lower() not in ("y", "yes"):
        print("Refused. Nothing was run.", file=sys.stderr)
        return None
    return ApprovalRecord(
        plan_hash=request["plan_hash"],
        action_hash=request["action_hash"],
        approved_by=approved_by or getpass.getuser(),
        reason="approved at the terminal",
    )


def _environment_model_bindings(
    environment: Environment, program: Program
) -> dict[str, ModelProvider]:
    """The models an environment names, for the models a program declares.

    A model named in the environment that this program does not declare is
    left out: one environment serves every program of a project.
    """

    if environment.fixture is not None:
        return _model_fixture_bindings(environment.fixture)
    declared = {model.name for model in program.models if model.provider is None}
    bindings: dict[str, ModelProvider] = {}
    for name, route in environment.models.items():
        if name not in declared:
            continue
        url = route.url or (
            DEFAULT_LOCAL_MODEL_URL.removesuffix("/v1")
            if route.protocol == "ollama"
            else DEFAULT_LOCAL_MODEL_URL
        )
        bindings[name] = OpenAICompatibleProvider(
            url,
            route.model,
            api_key=os.environ.get("KOSIKA_MODEL_API_KEY"),
            protocol=route.protocol,
            timeout=60.0,
        )
        print(f"{name}: model {route.model!r} at {url}", file=sys.stderr)
    return bindings


def _missing_model_hint(program: Program, message: str) -> str:
    if "has no authorized host binding" not in message or not program.models:
        return ""
    return (
        "\nThis program declares a model, and a declaration alone grants no "
        "access. Add --model NAME to use a local model (default endpoint "
        f"{DEFAULT_LOCAL_MODEL_URL}), or --model-fixture FILE to run offline."
    )


def _no_folder(given: Path) -> str:
    """Why a folder named on the command line cannot be used, with the place
    that was actually looked in, since a short name is read from where the
    command was run."""

    looked = given.resolve()
    if given.is_absolute():
        return f"{given} is not a folder"
    return (
        f"{given} is not a folder. I looked for {looked}. "
        "If it is somewhere else, give its full path."
    )


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

    return _timeline_event(sequence, "kosika_approval_pending", "pending")


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


def _run_model_benchmark(args: argparse.Namespace, cases: list) -> int:
    """Score a real model through the same harness as the baselines."""

    from .benchmark import score
    from .model import OpenAICompatibleProvider
    from .repair import ModelRepairSolver, RepairStrategy

    if not args.model or not args.base_url:  # type: ignore[attr-defined]
        print(
            "model solver requires --model and --base-url",
            file=sys.stderr,
        )
        return 2
    if args.attempts < 1:  # type: ignore[attr-defined]
        print("--attempts must be at least 1", file=sys.stderr)
        return 2
    repairable = [case for case in cases if case.is_repairable]
    if args.limit is not None:  # type: ignore[attr-defined]
        repairable = repairable[: max(args.limit, 0)]
    if not repairable:
        print("no repair cases selected", file=sys.stderr)
        return 2

    think = {"auto": None, "on": True, "off": False}[args.think]  # type: ignore[attr-defined]
    provider = OpenAICompatibleProvider(
        base_url=args.base_url,  # type: ignore[attr-defined]
        model=args.model,  # type: ignore[attr-defined]
        api_key=args.api_key or os.environ.get("KOSIKA_API_KEY"),  # type: ignore[attr-defined]
        think=think,
        protocol=args.protocol,  # type: ignore[attr-defined]
        # CPU inference on a 7B model takes minutes per case, and the first call
        # also pays the cost of loading the model. A short timeout reports a
        # spurious failure for a run that is merely slow.
        timeout=float(args.model_timeout),  # type: ignore[attr-defined]
    )
    solver = ModelRepairSolver(
        provider,
        strategy=RepairStrategy(args.strategy),  # type: ignore[attr-defined]
        max_attempts=args.attempts,  # type: ignore[attr-defined]
        model=args.model,  # type: ignore[attr-defined]
        max_output_tokens=args.max_tokens,  # type: ignore[attr-defined]
    )

    def progress(result) -> None:
        mark = "ok  " if result.solved else "FAIL"
        detail = "" if result.solved else f"  {', '.join(result.raised) or 'no patch'}"
        print(
            f"[{len(seen) + 1}/{len(repairable)}] {mark} {result.code or '-'}{detail}",
            file=sys.stderr,
            flush=True,
        )
        seen.append(result)

    seen: list = []
    report = score(
        repairable,
        solver,
        name=f"model:{args.model}:{args.strategy}",  # type: ignore[attr-defined]
        on_result=progress,
    )

    outcomes = solver.outcomes
    model_calls = sum(outcome.model_calls for outcome in outcomes)
    prompt_tokens = sum(outcome.usage_prompt_tokens for outcome in outcomes)
    completion_tokens = sum(outcome.usage_completion_tokens for outcome in outcomes)
    applied = sum(1 for outcome in outcomes if any(a.applied for a in outcome.attempts))
    payload = report.to_dict()
    payload["model_calls"] = model_calls
    payload["cases_with_an_applied_patch"] = applied
    payload["prompt_tokens"] = prompt_tokens
    payload["completion_tokens"] = completion_tokens

    if args.format == "json":  # type: ignore[attr-defined]
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(
        f"solver      : {payload['solver']}\n"
        f"score       : {report.score:.3f}  {report.solved}/{report.total}\n"
        f"model calls : {model_calls} (limit {args.attempts}/case)\n"  # type: ignore[attr-defined]
        f"produced a patch : {applied}/{report.total}\n"
        f"tokens      : prompt {prompt_tokens}, completion {completion_tokens}"
    )
    if args.per_code:  # type: ignore[attr-defined]
        for code, value in report.by_code().items():
            print(
                f"    {code:36} {value['solved']:>3}/{value['total']:<3} {value['rate']:.2f}"
            )
    if getattr(args, "show_responses", False):
        print("\n--- model responses ---")
        for outcome in outcomes[: args.limit or len(outcomes)]:  # type: ignore[attr-defined]
            print(f"\n{outcome.case_id}  [{outcome.code}]  solved={outcome.solved}")
            for attempt in outcome.attempts:
                print(
                    f"  attempt {attempt.index + 1}: applied={attempt.applied}"
                    f" error={attempt.error}"
                )
                if attempt.response:
                    for line in attempt.response.splitlines()[:12]:
                        print(f"    | {line}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level parser with every command registered.

    Separated from `main` so tooling and tests can inspect the registered
    commands without dispatching one.
    """

    parser = argparse.ArgumentParser(prog="kosika")
    subparsers = parser.add_subparsers(dest="command", required=True)

    project_parser = subparsers.add_parser(
        "project",
        help="Index a bounded Python project and its local imports without executing code.",
    )
    project_parser.add_argument("root", type=Path, help="Project directory to analyze.")
    project_parser.add_argument(
        "--format", choices=("json",), default="json", help="Project report format."
    )
    project_parser.add_argument(
        "--output", type=Path, help="Optional JSON report path."
    )

    development_parser = subparsers.add_parser(
        "development",
        help="Execute one kosika.development/v1 JSON request.",
    )
    development_parser.add_argument(
        "request",
        help="JSON request file, or '-' to read the request from standard input.",
    )

    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("source", type=Path)
    check_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format. JSON emits machine-readable diagnostics.",
    )

    benchmark_parser = subparsers.add_parser(
        "benchmark",
        help="Score a solver against the repair corpus.",
    )
    benchmark_parser.add_argument(
        "--format", choices=("text", "json"), default="text", help="Output format."
    )
    benchmark_parser.add_argument(
        "--validate",
        action="store_true",
        help="Check that the harness discriminates, then exit non-zero if it does not.",
    )
    benchmark_parser.add_argument(
        "--per-code",
        action="store_true",
        help="Include a per-diagnostic-code accuracy breakdown.",
    )
    benchmark_parser.add_argument(
        "--solver",
        choices=("identity", "oracle", "model"),
        default="identity",
        help="Which solver to score. Defaults to the identity floor.",
    )
    benchmark_parser.add_argument(
        "--model",
        help="Model name for the model solver, and the server's model id.",
    )
    benchmark_parser.add_argument(
        "--base-url",
        help="OpenAI-compatible base URL for the model solver.",
    )
    benchmark_parser.add_argument(
        "--protocol",
        choices=("openai", "ollama"),
        default="openai",
        help=(
            "Model wire format. 'ollama' targets Ollama's native /api/chat, "
            "which honours --think."
        ),
    )
    benchmark_parser.add_argument(
        "--api-key",
        help="API key for the model solver. Falls back to KOSIKA_API_KEY.",
    )
    benchmark_parser.add_argument(
        "--strategy",
        choices=("contextual", "full"),
        default="contextual",
        help="What the model solver is shown.",
    )
    benchmark_parser.add_argument(
        "--attempts",
        type=int,
        default=2,
        help="Repair attempts per case for the model solver.",
    )
    benchmark_parser.add_argument(
        "--limit",
        type=int,
        help="Score only the first N repair cases.",
    )
    benchmark_parser.add_argument(
        "--show-responses",
        action="store_true",
        help="Print the model response for each case, for debugging a low score.",
    )
    benchmark_parser.add_argument(
        "--model-timeout",
        type=float,
        default=600.0,
        help="Per-call model timeout in seconds. CPU inference is slow.",
    )
    benchmark_parser.add_argument(
        "--max-tokens",
        type=int,
        default=2048,
        help="Output token budget per model call. Too small truncates the answer.",
    )
    benchmark_parser.add_argument(
        "--think",
        choices=("auto", "on", "off"),
        default="auto",
        help=(
            "Reasoning mode for reasoning models. 'off' is usually required "
            "when the answer must be a machine-readable patch."
        ),
    )

    corpus_parser = subparsers.add_parser(
        "corpus",
        help="Build a repair corpus from the repository's Kosika examples.",
    )
    corpus_parser.add_argument(
        "--output", type=Path, help="Write the corpus as JSONL to this path."
    )
    corpus_parser.add_argument(
        "--summary",
        action="store_true",
        help="Print coverage and integrity summary instead of the corpus.",
    )
    corpus_parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero when any case contradicts its mutation's intent.",
    )
    corpus_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Summary output format. Ignored when writing JSONL.",
    )

    coverage_parser = subparsers.add_parser(
        "diagnostics-coverage",
        help="Report whether every error raise site is claimed by a diagnostic code.",
    )
    coverage_parser.add_argument(
        "--format", choices=("text", "json"), default="text", help="Output format."
    )
    coverage_parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero when any raise site is unclassified or any code is dead.",
    )

    editor_parser = subparsers.add_parser(
        "editor",
        help="Return bounded JSON editor features for one Kosika source file.",
    )
    editor_parser.add_argument("source", type=Path)
    editor_parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read unsaved source text from stdin instead of the file.",
    )
    editor_parser.add_argument("--format", choices=("json",), default="json")

    analyze_parser = subparsers.add_parser(
        "analyze",
        help="Extract bounded, read-only structural facts from supported source files.",
    )
    analyze_parser.add_argument(
        "source",
        type=Path,
        help="Python, JavaScript, TypeScript, or Rust source file to analyze.",
    )
    analyze_parser.add_argument(
        "--format", choices=("json", "text"), default="text", help="Output format."
    )

    sourcemap_parser = subparsers.add_parser(
        "sourcemap",
        help=(
            "Map Python, JavaScript, TypeScript, and Rust declarations in a "
            "directory into one readable, deterministic map."
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

    spec_eval_parser = subparsers.add_parser(
        "spec-eval",
        help="Measure whether a model can write Kosika programs from specs.",
    )
    spec_eval_parser.add_argument(
        "--tasks", type=Path, default=Path("benchmarks/spec/tasks.json")
    )
    spec_eval_parser.add_argument(
        "--solver", choices=("reference", "model", "command"), default="reference"
    )
    spec_eval_parser.add_argument(
        "--solver-command",
        nargs=argparse.REMAINDER,
        help=(
            "For --solver command: the program and arguments to run. It reads "
            "the prompt on standard input and prints its reply. Must be last."
        ),
    )
    spec_eval_parser.add_argument(
        "--reference-dir", type=Path, default=Path("benchmarks/spec/reference")
    )
    spec_eval_parser.add_argument(
        "--guide",
        type=Path,
        default=Path("AGENT_GUIDE.md"),
        help="Language guide shown to the model.",
    )
    spec_eval_parser.add_argument(
        "--base-url", help="Model endpoint for --solver model."
    )
    spec_eval_parser.add_argument("--model", help="Model name for --solver model.")
    spec_eval_parser.add_argument(
        "--protocol", choices=MODEL_PROTOCOLS, default="openai"
    )
    spec_eval_parser.add_argument("--timeout", type=float, default=600.0)
    spec_eval_parser.add_argument("--max-tokens", type=int, default=1200)
    spec_eval_parser.add_argument("--attempts", type=int, default=2)
    spec_eval_parser.add_argument("--limit", type=int)
    spec_eval_parser.add_argument(
        "--task", action="append", default=[], help="Run only this task id."
    )
    spec_eval_parser.add_argument("--format", choices=("json", "text"), default="text")
    spec_eval_parser.add_argument("--output", type=Path)

    compare_parser = subparsers.add_parser(
        "compare-python",
        help="Compare Kosika with Python on the tasks in benchmarks/compare.",
    )
    compare_parser.add_argument(
        "--tasks", type=Path, default=Path("benchmarks/compare")
    )
    compare_parser.add_argument("--format", choices=("json", "text"), default="text")

    swarm_parser = subparsers.add_parser(
        "swarm",
        help="Show a swarm's plan hash, or run it under one approval.",
    )
    swarm_parser.add_argument("source", type=Path)
    swarm_parser.add_argument(
        "--swarm", required=True, help="Name of the declared swarm to run."
    )
    swarm_parser.add_argument(
        "--plan",
        action="store_true",
        help="Print the plan hash for review and exit without running anything.",
    )
    swarm_parser.add_argument(
        "--approved-by",
        help="Who approved this run. Required to run, and must not be blank.",
    )
    swarm_parser.add_argument(
        "--plan-hash",
        help="The plan hash from --plan that the approver reviewed.",
    )
    swarm_parser.add_argument(
        "--approval-ledger",
        type=Path,
        help="Persistent one-use approval ledger. Required to run.",
    )
    swarm_parser.add_argument("--reason", default="", help="Why the run was approved.")
    swarm_parser.add_argument("--mcp-source", help="MCP source name to bind.")
    swarm_parser.add_argument(
        "--mcp-server", type=Path, help="Local MCP server the swarm's tools bind to."
    )
    swarm_parser.add_argument(
        "--mcp-manifest", type=Path, help="MCP manifest, required with --mcp-server."
    )
    swarm_parser.add_argument(
        "--input", type=Path, help="JSON inputs for the swarm's workflow."
    )
    swarm_parser.add_argument(
        "--model-fixture",
        type=Path,
        help="JSON map of model names to ordered deterministic output records.",
    )
    swarm_parser.add_argument(
        "--tool-fixture",
        type=Path,
        help="JSON map of tool names to an output record or list of records.",
    )
    swarm_parser.add_argument(
        "--model", help="Live model that answers for every declared model."
    )
    swarm_parser.add_argument("--model-url", help="OpenAI-compatible model URL.")
    swarm_parser.add_argument(
        "--model-protocol", choices=MODEL_PROTOCOLS, default="openai"
    )
    swarm_parser.add_argument(
        "--models", type=Path, help="JSON file giving each declared model its own."
    )
    swarm_parser.add_argument("--model-timeout", type=float, default=300.0)
    swarm_parser.add_argument("--format", choices=("json", "text"), default="text")
    swarm_parser.add_argument("--output", type=Path)

    skills_parser = subparsers.add_parser(
        "skills",
        help="List local skills or select relevant skills for a task.",
    )
    skills_parser.add_argument(
        "--skill-dir",
        action="append",
        type=Path,
        default=[],
        help="Directory containing skill.json manifests; may be repeated.",
    )
    skills_parser.add_argument(
        "--task", help="Task used for automatic skill selection."
    )
    skills_parser.add_argument(
        "--skill",
        action="append",
        default=[],
        help="Skill id to require; may be repeated.",
    )
    skills_parser.add_argument(
        "--policy",
        choices=("automatic", "explicit", "hybrid", "disabled"),
        default="hybrid",
    )
    skills_parser.add_argument("--max-skills", type=int, default=6)
    skills_parser.add_argument("--format", choices=("text", "json"), default="text")

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("source", type=Path)
    run_parser.add_argument("--workflow", default="main")
    run_parser.add_argument(
        "--env",
        help=(
            "An environment in the kosika.env beside the program: the models, "
            "MCP servers, grants, budgets and the folder it keeps things in. "
            "Given, they are taken from it and from nowhere else."
        ),
    )
    run_parser.add_argument("--trace", type=Path, default=Path("run_trace.jsonl"))
    run_parser.add_argument("--journal", type=Path)
    run_parser.add_argument(
        "--binding-id",
        help="Stable host configuration identity required with --journal",
    )
    run_parser.add_argument("--approval-record", type=Path)
    run_parser.add_argument(
        "--allow-folder",
        type=Path,
        help=(
            "Grant the program's system files tools this folder, and run its "
            "shell tools inside it. Every call still needs approval."
        ),
    )
    run_parser.add_argument(
        "--embed-model",
        metavar="NAME",
        help=(
            "An embedding model, such as nomic-embed-text, for file tools that "
            "search by meaning (operation find). Asked at the same address as "
            "--model-url. Without it those tools cannot be reached."
        ),
    )
    run_parser.add_argument(
        "--allow-shell",
        action="store_true",
        help="Also grant the program's system shell tools. Needs --allow-folder.",
    )
    run_parser.add_argument(
        "--approve",
        action="store_true",
        help=(
            "Ask on the terminal before each action that needs approval, "
            "showing the exact tool and arguments."
        ),
    )
    run_parser.add_argument(
        "--approved-by",
        help="Name recorded with approvals given through --approve.",
    )
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
        "--component-manifest",
        type=Path,
        help="Pinned kosika.component/v1 contract for one declared tool.",
    )
    run_parser.add_argument(
        "--component-runtime",
        help="Optional host-authorized interpreter prepended to the pinned artifact.",
    )
    run_parser.add_argument(
        "--allow-component-effect",
        action="append",
        default=[],
        help="Grant one namespaced effect required by the component contract.",
    )
    run_parser.add_argument(
        "--allow-component-capability",
        action="append",
        default=[],
        help="Grant one namespaced capability required by the component contract.",
    )
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
        "--model-fixture",
        type=Path,
        help="Offline JSON model outputs grouped by declared model name.",
    )
    run_parser.add_argument(
        "--model-binding", help="Declared Kosika model granted access to this provider."
    )
    run_parser.add_argument(
        "--model",
        help=(
            "Provider model identifier. Without --model-binding it answers for "
            "every model the program declares."
        ),
    )
    run_parser.add_argument(
        "--model-url",
        help=(
            "Model endpoint. Without --model-binding it defaults to "
            "KOSIKA_MODEL_URL, then a local Ollama server."
        ),
    )
    run_parser.add_argument(
        "--model-protocol",
        choices=MODEL_PROTOCOLS,
        default="openai",
        help="Wire format of the model endpoint.",
    )
    run_parser.add_argument(
        "--allow-site",
        action="append",
        default=[],
        metavar="SITE",
        help=(
            "A site the program's web tool may read, such as en.wikipedia.org. "
            "May be repeated. Without it, the web cannot be reached."
        ),
    )
    run_parser.add_argument(
        "--notify",
        metavar="URL",
        help=(
            "The one address a notify tool sends notes to, such as "
            "https://ntfy.sh/your-private-topic. Without it, nothing is sent."
        ),
    )
    run_parser.add_argument(
        "--model-timeout",
        type=float,
        default=60.0,
        help="Seconds to wait for one model answer (default 60).",
    )
    run_parser.add_argument(
        "--memory",
        type=Path,
        help=(
            "Folder that holds this program's memories, one JSON file each. "
            "Without it, a program that declares a memory is refused."
        ),
    )
    run_parser.add_argument(
        "--models",
        type=Path,
        help=(
            "JSON file granting a different real model to each declared "
            'model: {"cheap": {"model": "NAME", "url": "URL"}, '
            '"careful": {"command": ["program", "arg"]}}.'
        ),
    )
    run_parser.add_argument(
        "--model-command",
        nargs=argparse.REMAINDER,
        help=(
            "Use a command-line model for every declared model: the program "
            "and its arguments. It reads the request on standard input and "
            "prints its reply. Must be the last option."
        ),
    )

    new_parser = subparsers.add_parser(
        "new", help="Create a starter Kosika project in a new folder."
    )
    new_parser.add_argument("directory", type=Path)
    new_parser.add_argument(
        "--kind",
        default="hello",
        help=(
            "What to start from: hello (default), support (an agent with "
            "memory), expense (a model reads, code decides), or notes (a model "
            "extracts, code organises)."
        ),
    )

    agent_parser = subparsers.add_parser(
        "agent",
        help="Run a declared Kosika agent against a live or fixture provider.",
    )
    agent_parser.add_argument("source", type=Path)
    agent_parser.add_argument("--agent", required=True)
    agent_parser.add_argument(
        "--task",
        help="Natural-language task label; optional when --input is provided.",
    )
    agent_parser.add_argument(
        "--input",
        type=Path,
        help="JSON object matching the selected model's input record.",
    )
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
        "--model-fixture",
        type=Path,
        help="Offline JSON agent responses grouped by model identifier.",
    )
    agent_parser.add_argument(
        "--tool-fixture",
        type=Path,
        help="Offline JSON tool results grouped by tool name or operation.",
    )
    agent_parser.add_argument(
        "--system-prompt",
        default=None,
        help="Optional system prompt for the model.",
    )
    agent_parser.add_argument(
        "--skill-dir",
        action="append",
        type=Path,
        default=[],
        help="Directory containing skill.json manifests; may be repeated.",
    )
    agent_parser.add_argument(
        "--skill",
        action="append",
        default=[],
        help="Skill id to require; may be repeated.",
    )
    agent_parser.add_argument(
        "--skill-policy",
        choices=("automatic", "explicit", "hybrid", "disabled"),
        default="hybrid",
        help="Skill selection policy.",
    )
    agent_parser.add_argument(
        "--max-skills",
        type=int,
        default=6,
        help="Maximum selected skills including dependencies.",
    )
    agent_parser.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Maximum model responses; defaults to the declared agent limit.",
    )
    agent_parser.add_argument(
        "--deadline-seconds",
        type=float,
        default=None,
        help="Maximum cooperative runtime; defaults to the declared agent limit.",
    )
    agent_parser.add_argument(
        "--trace",
        type=Path,
        help="Write a completed direct-agent JSONL trace.",
    )
    agent_parser.add_argument(
        "--journal",
        type=Path,
        help="SQLite journal for durable direct-agent execution.",
    )
    agent_parser.add_argument(
        "--binding-id",
        help="Host identity for the model, tools, and policy binding.",
    )
    agent_parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume a matching durable agent journal.",
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
    for app_name, app_help in (
        (
            "studio",
            "Open Kosika Studio in its own window: watch agents work, live.",
        ),
        ("app", "The same as studio, opened as a tab in the browser."),
    ):
        app_parser = subparsers.add_parser(app_name, help=app_help)
        app_parser.add_argument("folder", type=Path, nargs="?", default=Path("."))
        app_parser.add_argument(
            "--model", help="Model that answers for every declared model in a run."
        )
        app_parser.add_argument("--model-url", help="OpenAI-compatible model URL.")
        app_parser.add_argument(
            "--model-protocol", choices=MODEL_PROTOCOLS, default="openai"
        )
        app_parser.add_argument("--model-timeout", type=float, default=300.0)
        app_parser.add_argument(
            "--allow-folder",
            type=Path,
            help="Folder that read-only file tools may look inside during a run.",
        )
        app_parser.add_argument(
            "--allow-site",
            action="append",
            default=[],
            metavar="SITE",
            help=(
                "A site the program's web tool may read, such as en.wikipedia.org. "
                "May be repeated. Without it, the web cannot be reached."
            ),
        )
        app_parser.add_argument(
            "--embed-model",
            metavar="NAME",
            help=(
                "An embedding model, such as nomic-embed-text, for file tools that "
                "search by meaning (operation find). Asked at the same address as "
                "--model-url. Without it those tools cannot be reached."
            ),
        )
        app_parser.add_argument(
            "--allow-shell",
            action="store_true",
            help=(
                "Let agents start the programs their shell tools name, inside "
                "--allow-folder. Every start still waits for your Allow once."
            ),
        )
        app_parser.add_argument(
            "--in-session",
            action="store_true",
            help=(
                "Give each agent its own copy of --allow-folder and run its "
                "programs behind a wall (bubblewrap; on Windows inside WSL), "
                "with no network. What it does there needs no yes step by "
                "step; you approve the changes when they are brought back."
            ),
        )
        app_parser.add_argument(
            "--session-system",
            metavar="NAME",
            help=(
                "On Windows, the WSL system sessions run in, such as "
                "kali-linux. It must have bubblewrap. Without this, the "
                "system WSL uses by default."
            ),
        )
        app_parser.add_argument(
            "--mcp",
            action="append",
            default=[],
            metavar="NAME=COMMAND",
            help=(
                "An MCP server that programs may use tools from, by the name "
                'they give it, such as notes="python notes_server.py". May be '
                "repeated. The command is started by Kosika, not by a shell."
            ),
        )
        app_parser.add_argument("--port", type=int, default=0)
        app_parser.add_argument(
            "--no-browser",
            action="store_true",
            help="Print the address; do not open it.",
        )
        app_parser.add_argument(
            "--until-closed",
            action="store_true",
            help="Open Studio in a window of its own and stop when it is closed.",
        )
    chat_parser = subparsers.add_parser(
        "chat", help="Talk to a Kosika program, one message at a time."
    )
    chat_parser.add_argument("source", type=Path)
    chat_parser.add_argument("--workflow", default="main")
    chat_parser.add_argument(
        "--model", help="Model that answers for every declared model."
    )
    chat_parser.add_argument("--model-url", help="OpenAI-compatible model URL.")
    chat_parser.add_argument(
        "--model-protocol", choices=MODEL_PROTOCOLS, default="openai"
    )
    chat_parser.add_argument(
        "--models", type=Path, help="JSON file giving each declared model its own."
    )
    chat_parser.add_argument(
        "--model-fixture", type=Path, help="Saved answers instead of a live model."
    )
    chat_parser.add_argument(
        "--memory", type=Path, help="Folder that holds the program's memories."
    )
    chat_parser.add_argument(
        "--trace",
        type=Path,
        help="Write each turn's trace here, replacing the last, for `kosika story`.",
    )
    chat_parser.add_argument(
        "--model-timeout",
        type=float,
        default=300.0,
        help="Seconds to wait for one model answer (default 300).",
    )
    chat_parser.add_argument(
        "--allow-folder",
        type=Path,
        help="Folder the program's file tools may work inside.",
    )
    chat_parser.add_argument(
        "--embed-model",
        metavar="NAME",
        help=(
            "An embedding model, such as nomic-embed-text, for file tools that "
            "search by meaning (operation find). Asked at the same address as "
            "--model-url. Without it those tools cannot be reached."
        ),
    )
    chat_parser.add_argument(
        "--allow-site",
        action="append",
        default=[],
        metavar="SITE",
        help=(
            "A site the program's web tool may read, such as en.wikipedia.org. "
            "May be repeated. Without it, the web cannot be reached."
        ),
    )
    chat_parser.add_argument(
        "--approve",
        action="store_true",
        help=(
            "Ask you before each action that changes something, and run it "
            "only on yes. Without this, such actions always fail."
        ),
    )
    story_parser = subparsers.add_parser(
        "story", help="Tell what happened in a run, in plain words, from its trace."
    )
    story_parser.add_argument("trace", type=Path)
    story_parser.add_argument("--json", action="store_true")
    pack_parser = subparsers.add_parser(
        "pack", help="Carry a program to another device and prove it is the same."
    )
    pack_commands = pack_parser.add_subparsers(dest="pack_command", required=True)
    pack_make = pack_commands.add_parser(
        "make", help="Write a program and its hash into one pack file."
    )
    pack_make.add_argument("source", type=Path)
    pack_make.add_argument("--out", type=Path, required=True)
    pack_check = pack_commands.add_parser(
        "check", help="Verify a pack and show what it would ask this device for."
    )
    pack_check.add_argument("pack", type=Path)
    pack_check.add_argument(
        "--test", action="store_true", help="Also run the tests inside the pack."
    )
    pack_check.add_argument("--json", action="store_true")
    pack_unpack = pack_commands.add_parser(
        "unpack", help="Verify a pack, then write its files into an empty folder."
    )
    pack_unpack.add_argument("pack", type=Path)
    pack_unpack.add_argument("directory", type=Path)
    test_parser = subparsers.add_parser(
        "test", help="Run the tests written in a Kosika program."
    )
    test_parser.add_argument("source", type=Path)
    test_parser.add_argument("--only", help="Run only the test with this name.")
    test_parser.add_argument("--json", action="store_true")
    measure_parser = subparsers.add_parser(
        "measure",
        help=(
            "Run a program's evals on one or more real models and report how "
            "often each passes, how long it took, and how many model calls."
        ),
    )
    start_parser = subparsers.add_parser(
        "start",
        help=(
            "The easiest way in. Makes a folder of starter agents if you have "
            "none, finds a model already on this computer, and opens Studio. "
            "Grants nothing."
        ),
    )
    start_parser.add_argument(
        "folder",
        type=Path,
        nargs="?",
        help="Where your agents live. Default: KosikaAgents in your home folder.",
    )
    start_parser.add_argument("--port", type=int, default=0)
    start_parser.add_argument("--no-browser", action="store_true")
    start_parser.add_argument(
        "--until-closed",
        action="store_true",
        help="Stop when the Studio window is closed, as the desktop app does.",
    )
    desktop_parser = subparsers.add_parser(
        "desktop",
        help=(
            "Put Kosika Studio on the Desktop and in the Start menu, as an "
            "app that opens in its own window. Grants nothing."
        ),
    )
    desktop_parser.add_argument(
        "folder",
        nargs="?",
        type=Path,
        help="Where your agents live. Default: KosikaAgents in your home folder.",
    )
    desktop_parser.add_argument(
        "--remove", action="store_true", help="Take the shortcuts away again."
    )
    desktop_parser.add_argument(
        "--kosi",
        action="store_true",
        help=(
            "Make (or with --remove, take away) the shortcut for Kosi, the "
            "assistant as a small pill that stays on the screen."
        ),
    )
    outside_parser = subparsers.add_parser(
        "outside",
        help=(
            "Say which outside command-line agents (devin, omp) Kosika's agents "
            "may ask for help, and the one folder they may be shown."
        ),
    )
    outside_parser.add_argument(
        "--allow", nargs="+", metavar="NAME", help="The agents that may be asked."
    )
    outside_parser.add_argument(
        "--within",
        type=Path,
        metavar="FOLDER",
        help="They are shown only granted folders inside this one.",
    )
    outside_parser.add_argument(
        "--off", action="store_true", help="Allow none. This is how it starts."
    )
    pill_parser = subparsers.add_parser(
        "pill",
        help=(
            "Show Kosi, the assistant, as a small pill that stays on the "
            "screen. Click it or press Ctrl+Alt+K to ask."
        ),
    )
    pill_parser.add_argument(
        "folder",
        nargs="?",
        type=Path,
        help="Where your agents live. Default: KosikaAgents in your home folder.",
    )
    motion_parser = subparsers.add_parser(
        "motion",
        help=(
            "An experiment: run a program whose workflow returns a scene "
            "(shapes and their moves), check it, and write one web file "
            "that plays it."
        ),
    )
    motion_parser.add_argument("source", type=Path)
    motion_parser.add_argument("--out", type=Path, required=True)
    motion_parser.add_argument("--workflow", default="main")
    motion_parser.add_argument(
        "--input", type=Path, help="A JSON file of the workflow's inputs."
    )
    measure_parser.add_argument("source", type=Path)
    measure_parser.add_argument(
        "--model",
        action="append",
        required=True,
        help="A model to measure. Repeat to compare several.",
    )
    measure_parser.add_argument("--model-url", help="OpenAI-compatible model URL.")
    measure_parser.add_argument(
        "--embed-model",
        metavar="NAME",
        help=(
            "An embedding model for the program's meaning tools, such as "
            "nomic-embed-text. Without it they cannot be reached."
        ),
    )
    measure_parser.add_argument(
        "--model-protocol", choices=MODEL_PROTOCOLS, default="openai"
    )
    measure_parser.add_argument("--model-timeout", type=float, default=300.0)
    measure_parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="How many times to run each eval on each model (default 1).",
    )
    measure_parser.add_argument("--only", help="Run only the eval with this name.")
    measure_parser.add_argument("--json", action="store_true")
    journal_parser = subparsers.add_parser(
        "journal",
        help="Inspect a durable Kosika workflow or direct-agent journal.",
    )
    journal_parser.add_argument("path", type=Path)
    journal_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format.",
    )

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

    component_parser = subparsers.add_parser(
        "component",
        help="Validate a pinned cross-language component contract.",
    )
    component_subparsers = component_parser.add_subparsers(
        dest="component_command", required=True
    )
    component_check_parser = component_subparsers.add_parser(
        "check",
        help="Validate a component contract and its pinned implementation artifact.",
    )
    component_check_parser.add_argument("manifest", type=Path)
    component_check_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the validated component identity as JSON.",
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

    # Every subparser is registered by this point, so the usage list can be
    # derived from them rather than maintained by hand.
    subparsers.metavar = command_metavar(subparsers)
    return parser


def _outside_settings() -> tuple[tuple[str, ...], Path | None]:
    """The outside agents the owner allowed, and the root they may see."""

    from .outside import load_settings

    return load_settings()


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "start":
        from .quickstart import default_folder, installed_models, start_plan

        start_arguments, start_lines = start_plan(
            args.folder or default_folder(), installed_models()
        )
        print("\n".join(start_lines))
        if args.port:
            start_arguments += ["--port", str(args.port)]
        if args.no_browser:
            start_arguments.append("--no-browser")
        if args.until_closed:
            start_arguments.append("--until-closed")
        # From here it is an ordinary `kosika studio` with those choices.
        args = parser.parse_args(start_arguments)
    if args.command == "outside":
        from . import outside as outside_agents

        try:
            if args.off:
                outside_agents.save_settings((), None)
            elif args.allow:
                outside_agents.save_settings(args.allow, args.within)
        except outside_agents.OutsideError as exc:
            print(f"Not done: {exc}", file=sys.stderr)
            return 2
        allowed, within = outside_agents.load_settings()
        installed = outside_agents.installed()
        print(
            "Outside agents Kosika knows how to ask: "
            + ", ".join(outside_agents.COMMANDS)
        )
        print("Installed here: " + (", ".join(installed) or "none"))
        if allowed:
            print("Allowed: " + ", ".join(allowed))
            print(f"They are shown only folders inside: {within}")
            print("What they read is sent to the company that runs each of them.")
            print("They work in a copy; nothing changes until you approve it.")
        else:
            print("Allowed: none. No outside agent is ever asked.")
            print("To allow some: kosika outside --allow devin omp --within FOLDER")
        return 0
    if args.command == "pill":
        from .pill import main as pill_main

        if args.folder is not None and not args.folder.is_dir():
            print(f"Not started: {_no_folder(args.folder)}", file=sys.stderr)
            return 2
        return pill_main([str(args.folder)] if args.folder is not None else [])
    if args.command == "desktop":
        from .desktop_app import (
            NAME,
            PILL_MODULE,
            PILL_NAME,
            STUDIO_MODULE,
            make_shortcuts,
            remove_shortcuts,
        )

        app_name = PILL_NAME if args.kosi else NAME
        try:
            if args.remove:
                gone = remove_shortcuts(app_name)
                print(
                    "Removed:\n  " + "\n  ".join(str(path) for path in gone)
                    if gone
                    else f"There was no {app_name} shortcut to remove."
                )
                return 0
            if args.folder is not None and not args.folder.is_dir():
                print(f"Not done: {_no_folder(args.folder)}", file=sys.stderr)
                return 2
            shortcuts = make_shortcuts(
                args.folder, app_name, PILL_MODULE if args.kosi else STUDIO_MODULE
            )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            print(f"Not done: {exc}", file=sys.stderr)
            return 2
        print(f"{app_name} is now an app on this computer:")
        for path in shortcuts:
            print(f"  {path}")
        if args.kosi:
            print("Open it and a small pill appears at the bottom of the screen.")
            print("Click it or press Ctrl+Alt+K to ask. Right-click to quit.")
            print("To take it away again: kosika desktop --kosi --remove")
        else:
            print("Open it like any other app. It starts with nothing granted;")
            print("choose a folder inside Studio, on the Permissions page.")
            print("To take it away again: kosika desktop --remove")
        return 0
    if args.command == "development":
        try:
            request_source = (
                sys.stdin.read()
                if args.request == "-"
                else Path(args.request).read_text(encoding="utf-8")
            )
            response = execute_development_json(request_source)
        except OSError as exc:
            print(f"Development request failed: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(response, indent=2, sort_keys=True))
        return 0 if response["ok"] else 2

    if args.command == "new":
        from .starter import StarterError, create_starter

        try:
            created = create_starter(args.directory, args.kind)
        except (StarterError, OSError) as exc:
            print(f"Could not create the project: {exc}", file=sys.stderr)
            return 2
        print(f"Created a starter project in {args.directory}:")
        for path in created:
            print(f"  {path.name}")
        if args.kind != "hello":
            print(
                "Next:\n"
                f"  cd {args.directory}\n"
                "  kosika test main.kos\n"
                "Then read README.md for how to run it on a model."
            )
            return 0
        print(
            "Next:\n"
            f"  cd {args.directory}\n"
            "  kosika run main.kos --input input.json --model-fixture responses.json"
        )
        return 0

    if args.command == "spec-eval":
        from .spec_benchmark import (
            SpecBenchmarkError,
            TaskResult,
            command_solver,
            load_tasks,
            model_solver,
            reference_solver,
            run_benchmark,
        )
        from .spec_benchmark import to_text as spec_to_text

        try:
            spec_tasks = load_tasks(args.tasks)
            if args.task:
                unknown_tasks = sorted(
                    set(args.task) - {item.id for item in spec_tasks}
                )
                if unknown_tasks:
                    raise SpecBenchmarkError(
                        f"Unknown task id(s): {', '.join(unknown_tasks)}"
                    )
                spec_tasks = [item for item in spec_tasks if item.id in args.task]
            if args.limit is not None:
                if args.limit < 1:
                    raise SpecBenchmarkError("--limit must be at least 1")
                spec_tasks = spec_tasks[: args.limit]
            if args.solver == "model":
                if not args.base_url or not args.model:
                    raise SpecBenchmarkError(
                        "--solver model requires --base-url and --model"
                    )
                spec_solver = model_solver(
                    OpenAICompatibleProvider(
                        args.base_url,
                        args.model,
                        api_key=os.environ.get("KOSIKA_MODEL_API_KEY"),
                        timeout=args.timeout,
                        think=False,
                        protocol=args.protocol,
                    ),
                    args.guide.read_text(encoding="utf-8"),
                    model=args.model,
                    max_output_tokens=args.max_tokens,
                )
            elif args.solver == "command":
                if not args.solver_command:
                    raise SpecBenchmarkError(
                        "--solver command requires --solver-command PROGRAM [ARGS]"
                    )
                spec_solver = command_solver(
                    args.solver_command,
                    args.guide.read_text(encoding="utf-8"),
                    timeout=args.timeout,
                )
            else:
                spec_solver = reference_solver(args.reference_dir)

            def spec_progress(result: TaskResult) -> None:
                state = (
                    "first try"
                    if result.first_try
                    else "passed"
                    if result.passed
                    else f"failed ({result.attempts[-1].verdict.stage})"
                )
                print(f"{result.id}: {state}", file=sys.stderr, flush=True)

            spec_report = run_benchmark(
                spec_tasks,
                spec_solver,
                max_attempts=args.attempts,
                progress=spec_progress,
            )
        except (SpecBenchmarkError, OSError, ValueError) as exc:
            print(f"Spec evaluation failed safely: {exc}", file=sys.stderr)
            return 2
        spec_json = json.dumps(spec_report, indent=2, sort_keys=True)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(spec_json + "\n", encoding="utf-8")
        print(spec_json if args.format == "json" else spec_to_text(spec_report))
        return 0 if spec_report["passed"] == spec_report["tasks"] else 1

    if args.command == "compare-python":
        from .comparison import ComparisonError, compare
        from .comparison import to_text as comparison_to_text

        try:
            comparison = compare(args.tasks)
        except (ComparisonError, OSError) as exc:
            print(f"Comparison failed safely: {exc}", file=sys.stderr)
            return 2
        print(
            json.dumps(comparison, indent=2, sort_keys=True)
            if args.format == "json"
            else comparison_to_text(comparison)
        )
        return 0

    if args.command == "swarm":
        from .approval_ledger import ApprovalLedger
        from .checker import CheckError
        from .swarm import (
            SwarmApproval,
            SwarmRunError,
            run_swarm,
            swarm_plan_hash,
        )

        try:
            program = load_program(args.source)
            plan = swarm_plan_hash(program, args.swarm)
            if args.plan:
                declared = next(
                    item for item in program.swarms if item.name == args.swarm
                )
                plan_payload = {
                    "swarm": declared.name,
                    "workflow": declared.workflow,
                    "agents": list(declared.agents),
                    "max_rounds": declared.max_rounds,
                    "approval_required": declared.approval_required,
                    "plan_hash": plan,
                    "notice": (
                        "Approving this hash authorizes exactly one run of this "
                        "swarm against this checked program. No swarm executes "
                        "until an approval naming this hash is supplied."
                    ),
                }
                print(json.dumps(plan_payload, indent=2, sort_keys=True))
                return 0

            missing = [
                name
                for name, value in (
                    ("--approved-by", args.approved_by),
                    ("--plan-hash", args.plan_hash),
                    ("--approval-ledger", args.approval_ledger),
                )
                if not value
            ]
            if missing:
                raise SwarmRunError(
                    "running a swarm requires "
                    + ", ".join(missing)
                    + f"; review the plan first with --plan (hash {plan})"
                )
            approval = SwarmApproval(
                plan_hash=args.plan_hash,
                approved_by=args.approved_by,
                reason=args.reason,
            )
            if bool(args.mcp_server) != bool(args.mcp_manifest):
                raise SwarmRunError("--mcp-server and --mcp-manifest are used together")
            if args.tool_fixture is not None and args.mcp_server is not None:
                raise SwarmRunError("Use either --tool-fixture or --mcp-server")
            swarm_tool_bindings: dict[str, Any] | None = None
            if args.tool_fixture is not None:
                swarm_tool_fixture = _agent_tool_fixture(args.tool_fixture)

                def swarm_fixture_tool(
                    declaration: ToolDecl, _arguments: dict[str, Any]
                ) -> Any:
                    return _agent_tool_fixture_result(swarm_tool_fixture, declaration)

                swarm_tool_bindings = {
                    tool.name: swarm_fixture_tool
                    for tool in program.tools
                    if tool.name in swarm_tool_fixture
                    or tool.operation in swarm_tool_fixture
                }
            swarm_mcp_commands: dict[str, Sequence[str]] | None = (
                {args.mcp_source: [sys.executable, str(args.mcp_server)]}
                if args.mcp_server is not None
                else None
            )
            swarm_manifest = (
                load_manifest(args.mcp_manifest)
                if args.mcp_manifest is not None
                else None
            )
            args.model_binding = None
            swarm_models = _workflow_model_bindings(args, program)
            outcome = run_swarm(
                program,
                args.swarm,
                approval=approval,
                ledger=ApprovalLedger(args.approval_ledger),
                mcp_commands=swarm_mcp_commands,
                manifest=swarm_manifest,
                model_bindings=swarm_models or None,
                tool_bindings=swarm_tool_bindings,
                inputs=json.loads(args.input.read_text(encoding="utf-8"))
                if args.input is not None
                else None,
            )
            if args.format == "json":
                print(json.dumps(outcome.to_dict(), indent=2, sort_keys=True))
            else:
                print(
                    f"swarm       : {outcome.swarm}\n"
                    f"workflow    : {outcome.workflow}\n"
                    f"agents      : {', '.join(outcome.agents)}\n"
                    f"rounds      : {len(outcome.rounds)} of at most "
                    f"{outcome.max_rounds}\n"
                    f"terminated  : {outcome.terminated_by}\n"
                    f"approved by : {outcome.approved_by}\n"
                    f"plan hash   : {outcome.plan_hash}\n"
                    f"counts      : {json.dumps(outcome.counts, sort_keys=True)}\n"
                    f"digest      : {outcome.digest}\n"
                    f"result      : {json.dumps(outcome.result, sort_keys=True)}"
                )
            if args.output is not None:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(
                    json.dumps(outcome.to_dict(), indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
        except (
            SwarmRunError,
            AgentLoopError,
            CheckError,
            OSError,
            ValueError,
        ) as exc:
            print(f"Swarm run failed safely: {exc}", file=sys.stderr)
            return 2
        return 0

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
            directory = NetworkDirectory.load(args.directory, recursive=args.recursive)
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
                print(
                    "Limitations: syntax-backed structure only; no execution or full semantic analysis."
                )
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
    if args.command == "skills":
        try:
            catalog = SkillCatalog.from_directories(args.skill_dir)
            selection = None
            if args.task is not None:
                selection = SkillRouter(catalog).select(
                    args.task,
                    explicit=tuple(args.skill),
                    policy=args.policy,
                    max_skills=args.max_skills,
                )
            payload: dict[str, Any] = {
                "skills": [skill.manifest() for skill in catalog],
            }
            if selection is not None:
                payload["selection"] = {
                    "policy": selection.policy,
                    "selected": list(selection.plan_entries),
                    "reasons": dict(selection.reasons),
                    "scores": dict(selection.scores),
                }
            if args.format == "json":
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                for skill in payload["skills"]:
                    print(f"- {skill['id']} v{skill['version']}: {skill['summary']}")
                if selection is not None:
                    print("Selected:")
                    for skill_id in selection.ids:
                        reason = dict(selection.reasons).get(skill_id, "selected")
                        print(f"- {skill_id}: {reason}")
            return 0
        except (SkillCatalogError, SkillRoutingError) as exc:
            print(f"Skill selection failed safely: {exc}", file=sys.stderr)
            return 2
    if args.command == "agent":
        return _run_agent_command(args, parser)
    if args.command == "component":
        try:
            component_contract = load_component_contract(args.manifest)
            artifact = verify_component_artifact(
                component_contract, args.manifest.parent
            )
        except (ComponentContractError, OSError) as exc:
            print(f"Component check failed: {exc}", file=sys.stderr)
            return 2
        component_summary = {
            "valid": True,
            "schema": component_contract.schema,
            "id": component_contract.component_id,
            "version": component_contract.version,
            "tool": component_contract.tool_name,
            "operation": component_contract.operation,
            "artifact": artifact.name,
            "identity": component_contract.identity,
            "access": component_contract.access,
            "effects": list(component_contract.effects),
            "capabilities": list(component_contract.capabilities),
            "errors": [
                {"code": error.code, "retryable": error.retryable}
                for error in component_contract.errors
            ],
            "cancellation": {
                "mode": component_contract.cancellation.mode,
                "timeoutMs": component_contract.cancellation.timeout_ms,
            },
        }
        if args.json:
            print(json.dumps(component_summary, indent=2, sort_keys=True))
        else:
            print(f"Valid component contract: {args.manifest}")
            print(
                f"Component: {component_contract.component_id} "
                f"{component_contract.version}"
            )
            print(
                f"Tool: {component_contract.tool_name} ({component_contract.operation})"
            )
            print(
                "Implementation: "
                f"{component_contract.implementation.sha256} "
                f"via {component_contract.implementation.protocol}"
            )
            print(f"Contract digest: {component_contract.identity['contractDigest']}")
        return 0

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
            print(f"Extension: {extension_manifest.id} {extension_manifest.version}")
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
    if args.command == "journal":
        try:
            report = inspect_journal(args.path)
        except (ExecutionError, OSError, ValueError) as exc:
            print(f"Journal inspection failed safely: {exc}", file=sys.stderr)
            return 2
        if args.format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(_render_journal_text(report))
        return 0

    if args.command in ("app", "studio"):
        import webbrowser

        from .app import App, address, make_server, open_window

        root = args.folder
        if not root.is_dir():
            print(f"App unavailable: {_no_folder(root)}", file=sys.stderr)
            return 2
        if args.allow_folder is not None and not args.allow_folder.is_dir():
            print(f"App unavailable: {_no_folder(args.allow_folder)}", file=sys.stderr)
            return 2
        if args.allow_shell and args.allow_folder is None:
            print(
                "App unavailable: --allow-shell also needs --allow-folder, the "
                "folder programs run in",
                file=sys.stderr,
            )
            return 2
        if args.session_system and not args.in_session:
            print(
                "App unavailable: --session-system only means something with "
                "--in-session",
                file=sys.stderr,
            )
            return 2
        if args.in_session:
            from .sessions import SessionError, unavailable, use_system

            if args.session_system:
                try:
                    use_system(args.session_system)
                except SessionError as exc:
                    print(f"App unavailable: {exc}", file=sys.stderr)
                    return 2
            if args.allow_folder is None:
                print(
                    "App unavailable: --in-session also needs --allow-folder, "
                    "the folder each session gets a copy of",
                    file=sys.stderr,
                )
                return 2
            problem = unavailable()
            if problem is not None:
                print(f"App unavailable: {problem}", file=sys.stderr)
                return 2
        from .mcp_tools import MCPGrantError, parse_grant

        try:
            mcp_servers = dict(parse_grant(grant) for grant in args.mcp)
        except MCPGrantError as exc:
            print(f"App unavailable: {exc}", file=sys.stderr)
            return 2
        binder = None
        if args.model:
            args.model_binding = args.model_fixture = args.models = None

            def binder(program: Program) -> dict[str, ModelProvider]:
                if not any(model.provider is None for model in program.models):
                    return {}
                with contextlib.redirect_stderr(io.StringIO()):
                    return _workflow_model_bindings(args, program)

        studio = App(
            root,
            bind_models=binder,
            model_label=args.model or "",
            allow_folder=args.allow_folder,
            sites=tuple(args.allow_site),
            allow_shell=args.allow_shell,
            mcp=mcp_servers,
            meaning=_meaning(args),
            embed_label=args.embed_model or "",
            in_session=args.in_session,
            outside=_outside_settings()[0],
            outside_within=_outside_settings()[1],
        )
        try:
            server = make_server(studio, args.port)
        except OSError as exc:
            print(f"App unavailable: {exc}", file=sys.stderr)
            return 2
        url = address(studio, server)
        print(f"Kosika Studio for {studio.root}")
        print(f"  {url}")
        print(
            "  It can run agents with model " + args.model
            if args.model
            else "  View and test only. Add --model NAME to run agents."
        )
        print("  Press Ctrl+C to stop.")
        if not args.no_browser:
            # Studio opens as a window of its own when a browser that can do
            # that is installed, and as an ordinary tab otherwise.
            window = None
            if args.until_closed:
                from .app import open_app_window
                from .desktop_app import home as app_home

                window = open_app_window(url, app_home() / "window")
            if window is not None:
                # The app ends when its window does.
                def stop_with_window() -> None:
                    window.wait()
                    server.shutdown()

                threading.Thread(target=stop_with_window, daemon=True).start()
            elif args.command != "studio" or not open_window(url):
                webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            studio.close()
        return 0

    if args.command == "chat":
        from .chat import ChatError, chat_loop, message_parameter

        try:
            program = load_program(args.source)
            message_parameter(program, args.workflow)
            args.model_binding = None
            chat_models = _workflow_model_bindings(args, program)
            chat_tools = {
                **bind_system_tools(
                    program,
                    folder=args.allow_folder,
                    sites=args.allow_site,
                    meaning=_meaning(args),
                ),
                **bind_memory_tools(program, args.memory),
            }
        except (ChatError, ValueError, OSError) as exc:
            print(f"Chat unavailable: {exc}", file=sys.stderr)
            return 2

        def read_line() -> str | None:
            try:
                return input("you> ")
            except EOFError:
                return None

        def ask_first(request: dict[str, Any]) -> bool:
            print("\nThe assistant wants to do this, and nothing else:")
            print(f"  action : {request['tool']}")
            for name, value in request["arguments"].items():
                shown = value if isinstance(value, str) else json.dumps(value)
                lines = shown.splitlines() or [""]
                print(f"  {name:<7}: {lines[0]}")
                for more in lines[1:12]:
                    print(f"           {more}")
                if len(lines) > 12:
                    print(f"           ... {len(lines) - 12} more line(s)")
            if args.allow_folder is not None:
                print(f"  inside : {args.allow_folder.resolve()}")
            try:
                answer = input("Allow this one action? [y/N] ")
            except EOFError:
                return False
            return answer.strip().lower() in ("y", "yes")

        # Answers may hold any character; a console's default encoding
        # must not turn that into a crash.
        reconfigure = getattr(sys.stdout, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")
        if chat_models and args.model_fixture is None:
            # A local model is loaded on its first request, which can take
            # half a minute. Doing that now, and saying so, is kinder than
            # letting the first "hello" seem to hang.
            from .model import ChatMessage as WarmMessage
            from .model import ModelRequest as WarmRequest

            print("Getting the model ready...", file=sys.stderr, flush=True)
            warm_started = time.monotonic()
            warmed: set[int] = set()
            for warm_provider in chat_models.values():
                if id(warm_provider) in warmed:
                    continue
                warmed.add(id(warm_provider))
                try:
                    warm_provider.complete(
                        WarmRequest(
                            messages=(WarmMessage(role="user", content="hi"),),
                            max_output_tokens=1,
                        )
                    )
                except (RuntimeError, OSError) as exc:
                    print(f"The model did not answer: {exc}", file=sys.stderr)
            print(
                f"Ready in {time.monotonic() - warm_started:.0f} s.",
                file=sys.stderr,
            )
        print('Type a message, or "exit" to stop.', file=sys.stderr)
        chat_loop(
            program,
            args.workflow,
            model_bindings=chat_models,
            tool_bindings=chat_tools,
            read=read_line,
            write=print,
            trace=args.trace,
            approve=ask_first if args.approve else None,
            approved_by=getpass.getuser(),
        )
        return 0

    if args.command == "story":
        from .story import StoryError, build_story, read_trace
        from .story import to_text as story_to_text

        try:
            told = build_story(read_trace(args.trace))
        except StoryError as exc:
            print(f"Story unavailable: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(told, indent=2) if args.json else story_to_text(told))
        return 0

    if args.command == "pack":
        from .pack import PackError, check_pack, unpack, write_pack
        from .pack import to_text as pack_to_text

        try:
            if args.pack_command == "make":
                made = write_pack(args.source, args.out)
                print(f"Wrote {args.out}")
                print(f"Program  {made['program_hash']}")
                return 0
            if args.pack_command == "unpack":
                pack_report = unpack(args.pack, args.directory)
                print(pack_to_text(pack_report))
                print(f"\nUnpacked into {args.directory}")
                return 0
            pack_report = check_pack(args.pack, run_tests=args.test)
        except (PackError, ValueError, OSError) as exc:
            print(f"Pack rejected: {exc}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(pack_report.to_dict(), indent=2))
        else:
            print(pack_to_text(pack_report))
        return 0 if all(test["passed"] for test in pack_report.tests) else 1

    if args.command == "measure":
        from .testing import measure, measure_to_text

        if not 1 <= args.repeat <= 50:
            print("Measure unavailable: --repeat must be from 1 to 50", file=sys.stderr)
            return 2
        try:
            measured_program = load_program(args.source)
        except (ValueError, OSError) as exc:
            print(f"Check failed: {exc}", file=sys.stderr)
            return 2
        wanted = list(args.model)

        def bind_for(name: str) -> dict[str, ModelProvider]:
            args.model = name
            args.model_binding = args.model_fixture = args.models = None
            with contextlib.redirect_stderr(io.StringIO()):
                return _workflow_model_bindings(args, measured_program)

        try:
            # An eval scripts what its file tools find, so that every model
            # reads the same lines. A tool that only compares meanings
            # reaches nothing but the embedding model, so it may be real.
            measured = measure(
                measured_program,
                bind_for,
                wanted,
                repeat=args.repeat,
                only=args.only,
                live_tools=bind_system_tools(
                    measured_program, folder=None, meaning=_meaning(args)
                )
                if args.embed_model
                else None,
            )
        except (ValueError, RuntimeError, OSError) as exc:
            print(f"Measure failed: {exc}", file=sys.stderr)
            return 2
        print(
            json.dumps(measured, indent=2) if args.json else measure_to_text(measured)
        )
        return 0

    if args.command == "motion":
        from .motion import check_scene, to_html

        try:
            scene_program = load_program(args.source)
            given = (
                json.loads(args.input.read_text(encoding="utf-8"))
                if args.input is not None
                else {}
            )
            with tempfile.TemporaryDirectory(prefix="kosika-motion-") as scratch:
                scene = run(
                    scene_program,
                    args.workflow,
                    Path(scratch) / "run.jsonl",
                    inputs=given,
                )
        except (ValueError, RuntimeError, OSError) as exc:
            print(f"Motion failed: {exc}", file=sys.stderr)
            return 2
        problems = check_scene(scene)
        if problems:
            print("The scene cannot be played:", file=sys.stderr)
            for problem in problems[:20]:
                print(f"  {problem}", file=sys.stderr)
            return 1
        args.out.write_text(
            to_html(scene, args.source.stem), encoding="utf-8", newline="\n"
        )
        print(
            f"Wrote {args.out}: {len(scene['shapes'])} shape(s), "
            f"{len(scene['moves'])} move(s), {scene['seconds']:g} seconds. "
            "Open it in a browser."
        )
        return 0

    if args.command == "test":
        from .testing import run_tests
        from .testing import to_text as tests_to_text

        try:
            results = run_tests(load_program(args.source), only=args.only)
        except (ValueError, OSError) as exc:
            print(f"Check failed: {exc}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps([result.to_dict() for result in results], indent=2))
        else:
            print(tests_to_text(results))
        return 0 if all(result.passed for result in results) else 1

    if args.command == "replay":
        try:
            print(replay(args.trace))
        except (ValueError, OSError) as exc:
            print(f"Replay rejected: {exc}", file=sys.stderr)
            return 2
        return 0

    if args.command == "benchmark":
        from .benchmark import run_reference_baselines, validate
        from .corpus import build_corpus

        cases = build_corpus(Path(__file__).resolve().parent.parent)
        if args.validate:
            report_data = validate(cases)
            if args.format == "json":
                print(json.dumps(report_data, indent=2, sort_keys=True))
            else:
                print(
                    f"cases   : {report_data['cases']}\n"
                    f"identity: {report_data['identity_score']}\n"
                    f"oracle  : {report_data['oracle_score']}"
                )
                for problem in report_data["problems"]:
                    print(f"  PROBLEM {problem}")
            return 0 if report_data["ok"] else 1

        if args.solver == "model":
            return _run_model_benchmark(args, cases)

        baselines = run_reference_baselines(cases)
        if args.format == "json":
            print(
                json.dumps(
                    {name: report.to_dict() for name, report in baselines.items()},
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        for name, baseline_report in baselines.items():
            print(
                f"{name:10} score={baseline_report.score:.3f}  "
                f"{baseline_report.solved}/{baseline_report.total}"
            )
            if args.per_code:
                for code, value in baseline_report.by_code().items():
                    print(
                        f"    {code:36} {value['solved']:>3}/{value['total']:<3} {value['rate']:.2f}"
                    )
        return 0
    if args.command == "corpus":
        from .corpus import build_corpus, corpus_summary, to_jsonl

        cases = build_corpus(Path(__file__).resolve().parent.parent)
        summary = corpus_summary(cases)
        if args.check and summary["mislabelled_cases"]:
            print(
                f"corpus: {summary['mislabelled_cases']} case(s) contradict their "
                f"mutation intent: {', '.join(summary['mislabelled_by_mutation'])}",
                file=sys.stderr,
            )
            return 1
        if args.output is not None:
            args.output.write_text(to_jsonl(cases), encoding="utf-8")
            if not args.summary:
                print(f"Corpus written: {args.output} ({len(cases)} cases)")
                return 0
        if args.summary:
            if args.format == "json":
                print(json.dumps(summary, indent=2, sort_keys=True))
            else:
                print(
                    f"cases           : {summary['cases']}\n"
                    f"  valid         : {summary['valid_cases']}\n"
                    f"  repair        : {summary['repair_cases']}\n"
                    f"distinct codes  : {summary['distinct_codes']}"
                    f" / {summary['registered_codes']}\n"
                    f"mislabelled     : {summary['mislabelled_cases']}\n"
                    f"origins         : {len(summary['origins'])}"
                )
                for code in summary["codes"]:
                    print(f"  {code}")
                uncovered = summary["uncovered_codes"]
                if uncovered:
                    print(f"uncovered codes : {len(uncovered)}")
                    for code in uncovered:
                        print(f"  - {code}")
            return 0
        print(to_jsonl(cases), end="")
        return 0
    if args.command == "diagnostics-coverage":
        from .diagnostics_source import (
            coverage_report,
            iter_templates,
            raise_sites,
        )

        root = Path(__file__).resolve().parent.parent
        coverage_data = coverage_report(raise_sites(root), list(iter_templates()))
        if args.format == "json":
            print(json.dumps(coverage_data, indent=2, sort_keys=True))
        else:
            print(
                f"raise sites: {coverage_data['site_count']}\n"
                f"classified : {coverage_data['classified']}"
            )
            for item in coverage_data["unclassified"]:
                print(f"  UNCLASSIFIED {item['label']}: {item['sample']}")
            for code in coverage_data["unreachable_templates"]:
                print(f"  DEAD CODE   {code}")
        if args.check and (
            coverage_data["unclassified"] or coverage_data["unreachable_templates"]
        ):
            return 1
        return 0
    if args.command == "editor":
        try:
            source = (
                sys.stdin.read()
                if args.stdin
                else args.source.read_text(encoding="utf-8")
            )
            features = build_editor_features(
                source,
                path=args.source.as_posix(),
                source_path=args.source,
                root=args.source.parent,
            )
            print(json.dumps(features, indent=2, sort_keys=True))
        except (EditorFeatureError, OSError, UnicodeError) as exc:
            print(f"Editor feature analysis failed: {exc}", file=sys.stderr)
            return 2
        return 0
    if args.command == "analyze":
        return 0

    json_check = args.command == "check" and getattr(args, "format", None) == "json"
    if json_check:
        response = check_project(args.source)
        payload = response["diagnostics"]
        assert isinstance(payload, dict)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["ok"] else 2
    try:
        program = load_program(args.source)
        check(program)
    except (ValueError, OSError) as exc:
        print(f"Check failed: {exc}{_failure_line(args.source)}", file=sys.stderr)
        return 2
    if args.command == "check":
        print(f"OK: {args.source}")
        return 0
    if args.command == "mindmap":
        try:
            extension_manifests = tuple(
                load_extension_manifest(path) for path in args.extension_manifest
            )
            mindmap = build_mindmap(program, extension_manifests=extension_manifests)
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
    if args.component_manifest is None and (
        args.component_runtime is not None
        or args.allow_component_effect
        or args.allow_component_capability
    ):
        parser.error(
            "--component-runtime and component grants require --component-manifest"
        )

    environment: Environment | None = None
    if getattr(args, "env", None):
        decided = [
            flag
            for attribute, flag in ENVIRONMENT_DECIDED
            if getattr(args, attribute, None) not in (None, False, [])
        ]
        if decided:
            print(
                f"Workflow failed: {decided[0]} cannot be used with --env; "
                "name it in the environment",
                file=sys.stderr,
            )
            return 2
        try:
            environment = find_environment(args.source, args.env)
        except EnvironmentError_ as exc:
            print(f"Workflow failed: {exc}", file=sys.stderr)
            return 2
        args.allow_folder = environment.allow_folder
        args.allow_shell = environment.allow_shell
        args.allow_site = list(environment.sites)
        args.memory = environment.memory_folder()
        args.max_model_calls = min(
            args.max_model_calls,
            int(environment.budget.get("model_calls", args.max_model_calls)),
        )
        args.max_tool_calls = min(
            args.max_tool_calls,
            int(environment.budget.get("tool_calls", args.max_tool_calls)),
        )
        if "seconds" in environment.budget:
            args.deadline_seconds = (
                float(environment.budget["seconds"])
                if args.deadline_seconds is None
                else min(args.deadline_seconds, float(environment.budget["seconds"]))
            )
        if args.trace == Path("run_trace.jsonl") and environment.keep is not None:
            args.trace = environment.trace_path()

    mcp_commands: dict[str, Sequence[str]] | None = None
    run_manifest: MCPManifest | dict[str, MCPManifest] | None = None
    if environment is not None and environment.mcp:
        try:
            mcp_commands = {
                source: list(route.command) for source, route in environment.mcp.items()
            }
            run_manifest = {
                source: load_manifest(route.manifest)
                for source, route in environment.mcp.items()
            }
        except ManifestError as exc:
            print(f"Workflow failed: {exc}", file=sys.stderr)
            return 2
    if args.mcp_server is not None:
        if args.mcp_manifest is None:
            parser.error("--mcp-manifest is required when --mcp-server is used")
        mcp_commands = {args.mcp_source: [sys.executable, str(args.mcp_server)]}
        run_manifest = load_manifest(args.mcp_manifest)
    elif args.mcp_manifest is not None:
        parser.error("--mcp-manifest requires --mcp-server")
    bindings: dict[str, ControlledProvider] = {}
    telemetry_error = False
    component_tool_bindings: dict[str, Any] = {}
    try:
        inputs = (
            strict_json(args.input.read_text(encoding="utf-8")) if args.input else {}
        )
        if not isinstance(inputs, dict):
            raise ValueError("Workflow input must be a JSON object")
        if environment is not None and environment.keep is not None:
            # A run keeps a journal of its own, named by what it runs, so
            # one paused for an approval is found again when run again.
            args.journal = environment.journal_path(args.source, args.workflow, inputs)
            args.binding_id = environment.name
        if args.component_manifest is not None:
            component_contract = load_component_contract(args.component_manifest)
            component_binding = bind_component(
                program,
                component_contract,
                args.component_manifest.parent,
                (
                    [args.component_runtime]
                    if args.component_runtime is not None
                    else []
                ),
                allowed_effects=args.allow_component_effect,
                allowed_capabilities=args.allow_component_capability,
            )
            component_tool_bindings[component_contract.tool_name] = component_binding
        system_bindings = bind_system_tools(
            program,
            folder=args.allow_folder,
            allow_shell=args.allow_shell,
            sites=getattr(args, "allow_site", ()),
            meaning=_meaning(args),
            notify=getattr(args, "notify", None),
        )
        system_bindings = {
            **system_bindings,
            **bind_memory_tools(program, getattr(args, "memory", None)),
        }
        if args.approve and not args.journal:
            # Approval needs a journal to remember what was approved between
            # the pause and the resume; an asked-for run keeps one for itself.
            approve_scratch = tempfile.TemporaryDirectory(prefix="kosika-approve-")
            args.journal = Path(approve_scratch.name) / "run.sqlite"
            args.binding_id = args.binding_id or "interactive"
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
        limits = ModelLimits(
            max_requests=args.max_requests,
            max_tokens=args.max_tokens,
            max_output_tokens=args.max_output_tokens,
        )
        bindings = {
            name: ControlledProvider(provider, limits)
            for name, provider in (
                _environment_model_bindings(environment, program)
                if environment is not None
                else _workflow_model_bindings(args, program)
            ).items()
        }
        host_tool_bindings = {**component_tool_bindings, **system_bindings}
        while True:
            # A fresh session per attempt: after an approval the journal
            # returns finished steps from its checkpoint and resumes at the
            # one that was waiting, so nothing already done runs twice.
            session = (
                ExecutionSession(
                    args.journal, binding_id=args.binding_id, approvals=approvals
                )
                if args.journal
                else None
            )
            try:
                run_result = run(
                    program,
                    args.workflow,
                    args.trace,
                    skills=load_skill_notes(Path(args.source).parent / SKILLS_FOLDER),
                    # Learning steps learn where the program remembers.
                    lessons=Lessons(args.memory) if args.memory else None,
                    mcp_commands=mcp_commands,
                    manifest=run_manifest,
                    inputs=inputs,
                    model_bindings=bindings,
                    tool_bindings=host_tool_bindings or None,
                    session=session,
                    limits=ExecutionLimits(
                        max_function_calls=args.max_function_calls,
                        max_steps=args.max_workflow_steps,
                        max_model_calls=args.max_model_calls,
                        max_tool_calls=args.max_tool_calls,
                        deadline_seconds=args.deadline_seconds,
                    ),
                )
                break
            except ApprovalPending as pending:
                granted = (
                    _ask_approval(pending.request, args.approved_by)
                    if args.approve
                    else None
                )
                if granted is None:
                    raise
                approvals.append(granted)
        if args.event_trace is not None:
            _publish_run_events(args, args.trace)
    except ApprovalPending as exc:
        if args.event_trace is not None:
            try:
                write_event_jsonl(
                    args.event_trace,
                    [_cli_approval_event(1)],
                )
            except (EventValidationError, OSError) as stream_exc:
                print(f"Event stream write failed: {stream_exc}", file=sys.stderr)
                return 2
        print(json.dumps({"status": exc.code, **exc.request}, indent=2))
        return 3
    except ExecutionError as exc:
        print(f"Workflow stopped ({exc.code}): {exc}", file=sys.stderr)
        return 130 if exc.code == "cancelled" else 2
    except (ValueError, RuntimeError, OSError) as exc:
        print(
            f"Workflow failed: {exc}{_missing_model_hint(program, str(exc))}",
            file=sys.stderr,
        )
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
    if environment is not None and args.journal is not None:
        # The run is finished: there is nothing to begin again. Its
        # approvals' ledger stays, so an approval is never used twice.
        Path(args.journal).unlink(missing_ok=True)
        Path(str(args.journal) + ".lock.sqlite").unlink(missing_ok=True)
    print(run_result)
    print(f"Trace: {args.trace}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
