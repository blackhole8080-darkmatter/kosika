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
from .ast import Program, ToolDecl
from .checker import check
from .city import CITY_NAME, CityError, build_city
from .cityscape import render_cityscape_svg
from .components import (
    ComponentContractError,
    bind_component,
    load_component_contract,
    verify_component_artifact,
)
from .controls import ControlledProvider, ExecutionError, ModelLimits
from .design import (
    DESIGN_THEMES,
    build_design_from_program,
    load_design_manifest,
    render_design,
    write_design_manifest,
)
from .development import check_project, execute_development_json
from .durable import (
    AgentExecutionSession,
    ApprovalPending,
    ExecutionSession,
    inspect_journal,
)
from .editor_features import EditorFeatureError, build_editor_features
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
from .metropolis import ENVIRONMENT_NAMES
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
from .skills import SkillCatalog, SkillCatalogError, SkillRouter, SkillRoutingError
from .source_map import (
    SourceMapError,
    build_source_map,
)
from .source_map import to_mermaid as render_source_map_mermaid
from .source_map import to_text as render_source_map_text
from .synapse_bridge import normalize_events
from .unified_mindmap import (
    UnifiedMindmapError,
    build_unified_mindmap,
)
from .unified_mindmap import to_mermaid as render_unified_mermaid
from .unified_mindmap import to_text as render_unified_text
from .world import WorldError, load_world_manifest


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


def _workflow_model_bindings(
    args: argparse.Namespace, program: Program
) -> dict[str, ModelProvider]:
    live = (args.model_binding, args.model, args.model_url)
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
    provider = OpenAICompatibleProvider(
        url,
        args.model,
        api_key=os.environ.get("KOSIKA_MODEL_API_KEY"),
        protocol=args.model_protocol,
    )
    return dict.fromkeys(granted, provider)


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


def _missing_model_hint(program: Program, message: str) -> str:
    if "has no authorized host binding" not in message or not program.models:
        return ""
    return (
        "\nThis program declares a model, and a declaration alone grants no "
        "access. Add --model NAME to use a local model (default endpoint "
        f"{DEFAULT_LOCAL_MODEL_URL}), or --model-fixture FILE to run offline."
    )


def _author_proposal_provider(args: argparse.Namespace) -> ModelProvider | None:
    """Build the provider that proposes patches.

    The proposal model and the program's own run-time models are separate
    authorities, so they have separate flags: wiring a live proposal endpoint
    must not silently become authority to execute the declared models. No
    proposal authority at all is allowed, because a project that already checks
    needs no model; if a diagnostic then needs one, the loop reports
    `authoring/no-provider` rather than claiming a repair.
    """

    live = (args.proposal_base_url, args.proposal_model)
    if args.proposal_fixture is not None:
        if any(live):
            raise ValueError(
                "Use either --proposal-fixture or a live proposal model, not both"
            )
        payload = strict_json(args.proposal_fixture.read_text(encoding="utf-8"))
        responses: Any = payload
        if isinstance(payload, dict):
            responses = payload.get("responses")
        if (
            not isinstance(responses, list)
            or not responses
            or any(not isinstance(item, str) or not item.strip() for item in responses)
        ):
            raise ValueError(
                "Authoring fixture must be a nonempty JSON list of response strings"
            )
        return ScriptedModelProvider(
            [ModelResponse(content=response) for response in responses]
        )
    if any(live) and not all(live):
        raise ValueError(
            "A live proposal model requires --proposal-base-url and --proposal-model"
        )
    if not all(live):
        return None
    proposal_think = {"auto": None, "on": True, "off": False}[args.proposal_think]
    return OpenAICompatibleProvider(
        args.proposal_base_url,
        args.proposal_model,
        api_key=os.environ.get("KOSIKA_MODEL_API_KEY"),
        think=proposal_think,
        protocol=args.proposal_protocol,
    )


def _run_author_command(args: argparse.Namespace) -> int:
    from .authoring import AuthoringLimits, author
    from .repair import RepairStrategy

    try:
        provider = _author_proposal_provider(args)
        inputs: Any = {}
        if args.input is not None:
            try:
                inputs = strict_json(args.input)
            except ValueError as exc:
                raise ValueError(f"--input must be valid JSON: {exc}") from exc
        if not isinstance(inputs, dict):
            raise ValueError("--input must be a JSON object of workflow inputs")
        execute = not args.no_run
        response = author(
            args.source,
            intent=args.intent,
            provider=provider,
            root=args.root,
            limits=AuthoringLimits(
                max_attempts=args.attempts,
                max_output_tokens=args.max_tokens,
                strategy=RepairStrategy(args.strategy),
            ),
            model=args.proposal_model,
            workflow=args.workflow,
            inputs=inputs,
            trace_path=args.trace,
            execute=execute,
            model_bindings=_model_fixture_bindings(args.model_fixture)
            if execute and args.model_fixture is not None
            else None,
        )
    except (OSError, TypeError, ValueError) as exc:
        print(f"Authoring failed safely: {exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps(response, indent=2, sort_keys=True))
        return 0 if response["ok"] else 1

    print(
        f"status      : {response['status']}\n"
        f"attempts    : {len(response['attempts'])} of {response['limits']['max_attempts']}\n"
        f"model calls : {response['usage']['model_calls']} "
        f"(prompt {response['usage']['prompt_tokens']}, "
        f"completion {response['usage']['completion_tokens']})"
    )
    for attempt in response["attempts"]:
        print(
            f"  attempt {attempt['index'] + 1}: {attempt['path'] or '-'} "
            f"[{attempt['code'] or '-'}] applied={attempt['applied']}"
            + (f" error={attempt['error']}" if attempt["error"] else "")
        )
    for path in response["proposals"]:
        # The proposal is printed, never written: applying it stays a host
        # decision, so the CLI cannot turn model output into a mutation.
        print(f"proposed    : {path} (not written to disk)")
    if response["execution"] is not None:
        observed = response["execution"]["result"].get("value")
        if response["execution"]["ok"]:
            print(f"run         : {json.dumps(observed, sort_keys=True)}")
        else:
            print(f"run failed  : {response['execution']['error']['message']}")
    if response["error"] is not None:
        print(f"error       : {response['error']['message']}", file=sys.stderr)
    return 0 if response["ok"] else 1


def _run_authoring_evaluation(args: argparse.Namespace) -> int:
    """Measure the authoring loop over the repair corpus."""

    from .authoring_evaluation import (
        EvaluationLimits,
        evaluate_authoring,
        identity_provider_factory,
        live_provider_factory,
        reference_provider_factory,
        to_text,
        validate,
    )
    from .corpus import build_corpus
    from .repair import RepairStrategy

    try:
        cases = build_corpus(Path(__file__).resolve().parent.parent)
        limits = EvaluationLimits(
            max_attempts=args.attempts,
            max_output_tokens=args.max_tokens,
            strategy=RepairStrategy(args.strategy),
            limit=args.limit,
            measure_behavior=not args.no_behavior,
        ).validated()
    except ValueError as exc:
        print(f"Authoring evaluation setup failed: {exc}", file=sys.stderr)
        return 2

    think = {"auto": None, "on": True, "off": False}[args.think]
    if args.solver == "model":
        if not args.model or not args.base_url:
            print("model evaluator requires --model and --base-url", file=sys.stderr)
            return 2
        factory = live_provider_factory(
            base_url=args.base_url,
            model=args.model,
            api_key=args.api_key or os.environ.get("KOSIKA_API_KEY"),
            timeout=float(args.model_timeout),
            think=think,
            protocol=args.protocol,
        )
        name = f"model:{args.model}"
    elif args.solver == "identity":
        factory = identity_provider_factory()
        name = "identity"
    else:
        factory = reference_provider_factory(args.attempts)
        name = "reference"

    seen: list = []

    def progress(result) -> None:
        mark = "ok  " if result.solved else "FAIL"
        detail = (
            "" if result.solved else f"  {', '.join(result.remaining_codes) or '-'}"
        )
        print(
            f"[{len(seen) + 1}/{limits.limit or len(cases)}] {mark} {result.code or '-'}"
            f" {result.attempts} attempt(s) {result.seconds:.2f}s{detail}",
            file=sys.stderr,
            flush=True,
        )
        seen.append(result)

    evaluation = evaluate_authoring(
        cases,
        factory,
        name=name,
        limits=limits,
        on_result=progress,
    )
    verdict = validate(evaluation)

    if args.format == "json":
        payload = evaluation.to_dict()
        payload["validation"] = verdict
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if verdict["ok"] else 1

    print(to_text(evaluation, top=args.slowest))
    print(
        f"expressible  : {verdict['expressible_cases']} case(s) "
        f"({verdict['unexpressible_cases']} not quotable as a patch)"
    )
    for problem in verdict["problems"]:
        print(f"PROBLEM {problem}", file=sys.stderr)
    return 0 if verdict["ok"] else 1


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
    manifest = build_timeline_design(events, args.design_id, args.design_name)
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

    author_parser = subparsers.add_parser(
        "author",
        help=("Run the bounded authoring loop: check, propose, repair, run, inspect."),
    )
    author_parser.add_argument("source", type=Path, help="Entry .kos file.")
    author_parser.add_argument(
        "--intent",
        required=True,
        help="What the host wants the loop to achieve. Recorded, never executed.",
    )
    author_parser.add_argument(
        "--root",
        type=Path,
        help="Project root. Defaults to the entry file's directory.",
    )
    author_parser.add_argument(
        "--proposal-fixture",
        type=Path,
        help="JSON list of canned patch responses, for a hermetic run.",
    )
    author_parser.add_argument(
        "--proposal-base-url",
        help="OpenAI-compatible base URL for the proposing model.",
    )
    author_parser.add_argument(
        "--proposal-model", help="Model name used to propose patches."
    )
    author_parser.add_argument(
        "--proposal-protocol",
        choices=("openai", "ollama"),
        default="openai",
        help=(
            "Proposing model wire format. 'ollama' targets Ollama's native "
            "/api/chat and disables the reasoning trace, which a patch needs."
        ),
    )
    author_parser.add_argument(
        "--proposal-think",
        choices=("auto", "on", "off"),
        default="off",
        help=(
            "Reasoning mode for the proposing model. Defaults to 'off' because "
            "a machine-readable patch cannot be read out of a reasoning trace."
        ),
    )
    author_parser.add_argument(
        "--attempts",
        type=int,
        default=2,
        help="Maximum proposing attempts. Each attempt is at most one model call.",
    )
    author_parser.add_argument(
        "--max-tokens",
        type=int,
        default=2048,
        help="Output token budget per proposal call.",
    )
    author_parser.add_argument(
        "--strategy",
        choices=("contextual", "full"),
        default="contextual",
        help="What the proposing model is shown.",
    )
    author_parser.add_argument(
        "--workflow", default="main", help="Workflow executed after a clean check."
    )
    author_parser.add_argument(
        "--input", help="JSON object of workflow inputs for the run."
    )
    author_parser.add_argument(
        "--trace", type=Path, help="Write the run trace here and inspect it."
    )
    author_parser.add_argument(
        "--model-fixture",
        type=Path,
        help="JSON map of declared model name to outputs, for the run step.",
    )
    author_parser.add_argument(
        "--no-run",
        action="store_true",
        help="Check and propose only. Nothing is executed.",
    )
    author_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Loop report format.",
    )

    author_eval_parser = subparsers.add_parser(
        "author-eval",
        help="Measure the bounded authoring loop over the repair corpus.",
    )
    author_eval_parser.add_argument(
        "--solver",
        choices=("reference", "identity", "model"),
        default="reference",
        help=(
            "reference replays the corpus's own repair (the ceiling), identity "
            "grants no proposing model (the floor), model scores a live model."
        ),
    )
    author_eval_parser.add_argument(
        "--attempts",
        type=int,
        default=2,
        help="Authoring attempts per case. Each attempt is at most one model call.",
    )
    author_eval_parser.add_argument(
        "--limit",
        type=int,
        help="Evaluate only the first N corpus cases.",
    )
    author_eval_parser.add_argument(
        "--max-tokens",
        type=int,
        default=2048,
        help="Output token budget per proposal call.",
    )
    author_eval_parser.add_argument(
        "--strategy",
        choices=("contextual", "full"),
        default="contextual",
        help="What the proposing model is shown.",
    )
    author_eval_parser.add_argument(
        "--base-url", help="OpenAI-compatible base URL for the model evaluator."
    )
    author_eval_parser.add_argument(
        "--protocol",
        choices=("openai", "ollama"),
        default="openai",
        help=(
            "Model wire format. 'ollama' targets Ollama's native /api/chat, "
            "which honours --think; its OpenAI-compatible shim accepts the "
            "field and ignores it."
        ),
    )
    author_eval_parser.add_argument(
        "--model", help="Model name for the model evaluator."
    )
    author_eval_parser.add_argument(
        "--api-key",
        help="API key for the model evaluator. Defaults to KOSIKA_API_KEY.",
    )
    author_eval_parser.add_argument(
        "--model-timeout",
        type=float,
        default=600.0,
        help="Per-call model timeout in seconds.",
    )
    author_eval_parser.add_argument(
        "--think",
        choices=("auto", "on", "off"),
        default="auto",
        help="Reasoning mode. 'off' is usually required for patch output.",
    )
    author_eval_parser.add_argument(
        "--no-behavior",
        action="store_true",
        help="Skip running programs to compare repaired behavior.",
    )
    author_eval_parser.add_argument(
        "--slowest",
        type=int,
        default=0,
        help="List the N slowest cases after the summary.",
    )
    author_eval_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Report format.",
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

    audience_map_parser = subparsers.add_parser(
        "audience-map",
        help="Build an evidence-labelled map for different readers.",
    )
    audience_map_parser.add_argument("source", type=Path)
    audience_map_parser.add_argument(
        "--audience",
        choices=("outsider", "overview", "operator", "reviewer", "maintainer"),
        default="outsider",
    )
    audience_map_parser.add_argument(
        "--purpose",
        action="append",
        default=[],
        help="Human-authored purpose context, labelled unverified.",
    )
    audience_map_parser.add_argument("--trace", type=Path)
    audience_map_parser.add_argument(
        "--component-contract", action="append", type=Path, default=[]
    )
    audience_map_parser.add_argument("--root", type=Path)
    audience_map_parser.add_argument(
        "--format", choices=("json", "mermaid", "text"), default="text"
    )
    audience_map_parser.add_argument("--output", type=Path)

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
    swarm_parser.add_argument("--format", choices=("json", "text"), default="text")
    swarm_parser.add_argument("--output", type=Path)

    unified_parser = subparsers.add_parser(
        "unified",
        help="Combine a checked Kosika mindmap with an optional external source map.",
    )
    unified_parser.add_argument("source", type=Path)
    unified_parser.add_argument("--project", type=Path)
    unified_parser.add_argument(
        "--format", choices=("json", "mermaid", "text"), default="json"
    )
    unified_parser.add_argument("--output", type=Path)

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
        help='Offline JSON list (or {"responses": [...]}) of answer strings.',
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

    new_parser = subparsers.add_parser(
        "new", help="Create a starter Kosika project in a new folder."
    )
    new_parser.add_argument("directory", type=Path)

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

    design_parser = subparsers.add_parser(
        "design",
        help="Generate and render deterministic agent-workflow vector designs.",
    )

    city_parser = subparsers.add_parser(
        "city",
        help="Build and render the agent city across every program under a root.",
    )
    city_parser.add_argument("root", type=Path)
    city_parser.add_argument(
        "--format", choices=("text", "json", "svg", "live"), default="text"
    )
    city_parser.add_argument("--theme", choices=("lab", "day"), default="lab")
    city_parser.add_argument(
        "--environment",
        choices=ENVIRONMENT_NAMES,
        default="metropolitan",
        help="Live-world environment preset.",
    )
    city_parser.add_argument(
        "--world",
        type=Path,
        default=None,
        help="Validated world manifest controlling live-world presentation.",
    )
    city_parser.add_argument("--output", type=Path, default=None)
    city_parser.add_argument(
        "--name",
        default=None,
        help=f"City name to render (default: {CITY_NAME}).",
    )
    city_parser.add_argument(
        "--serve",
        action="store_true",
        help="Serve the live city and drive it from real executions under root.",
    )
    city_parser.add_argument("--host", default="127.0.0.1")
    city_parser.add_argument("--port", type=int, default=8787)
    city_parser.add_argument(
        "--trace",
        type=Path,
        default=None,
        help=(
            "Run the city once and write one replayable trace per resident "
            "into this directory, without serving. Use with: kosika replay <trace>"
        ),
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


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "author":
        return _run_author_command(args)
    if args.command == "author-eval":
        return _run_authoring_evaluation(args)
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

    if args.command == "audience-map":
        from .audience_maps import (
            AudienceMapError,
            build_audience_map,
        )
        from .audience_maps import (
            to_mermaid as audience_map_to_mermaid,
        )
        from .audience_maps import (
            to_text as audience_map_to_text,
        )
        from .audience_maps import (
            verify as verify_audience_map,
        )

        try:
            contracts = tuple(
                load_component_contract(path) for path in args.component_contract
            )
            audience_map = build_audience_map(
                args.source,
                audience=args.audience,
                root=args.root,
                trace_path=args.trace,
                contracts=contracts,
                purpose=args.purpose,
            )
            verification = verify_audience_map(audience_map)
            if not verification["ok"]:
                raise AudienceMapError(
                    "Audience map integrity check failed: "
                    + "; ".join(verification["problems"])
                )
            rendered = {
                "json": lambda item: (
                    json.dumps(
                        item.to_dict(), indent=2, sort_keys=True, ensure_ascii=False
                    )
                    + "\n"
                ),
                "mermaid": audience_map_to_mermaid,
                "text": audience_map_to_text,
            }[args.format](audience_map)
            if args.output is not None:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(rendered, encoding="utf-8")
                print(f"Audience map written: {args.output}")
            else:
                print(rendered, end="")
        except (AudienceMapError, OSError, ValueError) as exc:
            print(f"Audience map failed safely: {exc}", file=sys.stderr)
            return 2
        return 0

    if args.command == "new":
        from .starter import StarterError, create_starter

        try:
            created = create_starter(args.directory)
        except (StarterError, OSError) as exc:
            print(f"Could not create the project: {exc}", file=sys.stderr)
            return 2
        print(f"Created a starter project in {args.directory}:")
        for path in created:
            print(f"  {path.name}")
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
            outcome = run_swarm(
                program,
                args.swarm,
                approval=approval,
                ledger=ApprovalLedger(args.approval_ledger),
                mcp_commands=swarm_mcp_commands,
                manifest=swarm_manifest,
                model_bindings=_model_fixture_bindings(args.model_fixture)
                if args.model_fixture is not None
                else None,
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
    if args.command == "city":
        try:
            world = load_world_manifest(args.world) if args.world is not None else None
            if args.trace is not None:
                from .city_live import capture_trace

                report = capture_trace(args.root, args.trace)
                print(
                    f"Captured {report['runs']} resident runs into {report['directory']}"
                )
                print(
                    f"  replayable: {report['replayable']}"
                    f"   awaiting approval: {report['awaiting_approval']}"
                )
                for entry in report["entries"]:
                    if entry.get("replayable"):
                        result = json.dumps(entry["replayed"], sort_keys=True)
                        print(f"  [ok]   {entry['workflow']} -> {result[:96]}")
                    else:
                        print(
                            f"  [held] {entry['workflow']} -> {entry.get('replay_error', '')}"
                        )
                if report["replayable"]:
                    first = next(
                        entry for entry in report["entries"] if entry.get("replayable")
                    )
                    print(f"Replay one with: kosika replay {first['trace']}")
                return 0
            if args.serve:
                from .city_live import serve_city

                name = world.name if world is not None else (args.name or CITY_NAME)
                environment = (
                    world.environment if world is not None else args.environment
                )
                labels = world.labels_by_domain if world is not None else None
                print(f"{name} live world on http://{args.host}:{args.port}")
                serve_city(
                    args.root,
                    host=args.host,
                    port=args.port,
                    name=name,
                    environment=environment,
                    world=world,
                )
                return 0
            city = build_city(
                args.root,
                name=world.name if world is not None else args.name,
            )
            if world is not None:
                world.validate_city(city)
            environment = world.environment if world is not None else args.environment
            labels = world.labels_by_domain if world is not None else None
            if args.format == "json":
                city_output = json.dumps(city.to_dict(), indent=2, sort_keys=True)
            elif args.format == "svg":
                city_output = render_cityscape_svg(city, theme=args.theme)
            elif args.format == "live":
                from .city_live import render_city_live

                city_output = render_city_live(
                    city,
                    environment=environment,
                    world_name=world.name if world is not None else None,
                    world_labels=labels,
                    world_kinds=(world.kinds_by_domain if world is not None else None),
                    world_buildings=(
                        world.building_ids_by_resident if world is not None else None
                    ),
                    world_camera=world.camera if world is not None else "isometric",
                    render_style=world.render_style if world is not None else "city",
                )
            else:
                city_output = city.to_text()
            if args.output is not None:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(city_output, encoding="utf-8")
                print(f"City written: {args.output}")
            else:
                print(city_output)
            return 0
        except (OSError, CityError, WorldError) as exc:
            print(f"City failed safely: {exc}", file=sys.stderr)
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
    if args.command == "unified":
        try:
            report = build_unified_mindmap(args.source, project=args.project)
            rendered = (
                json.dumps(report, indent=2, sort_keys=True)
                if args.format == "json"
                else (
                    render_unified_mermaid(report)
                    if args.format == "mermaid"
                    else render_unified_text(report)
                )
            )
            if args.output is not None:
                args.output.write_text(rendered, encoding="utf-8")
                print(f"Unified mindmap written: {args.output}")
            else:
                print(rendered, end="")
        except (OSError, UnifiedMindmapError, ValueError) as exc:
            print(f"Unified mindmap failed: {exc}", file=sys.stderr)
            return 2
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
            assistant_result = KosikaAssistant(
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
                print(json.dumps(assistant_result.to_dict(), indent=2, sort_keys=True))
            else:
                print(assistant_result.answer)
                print(f"Assistant mode: {assistant_result.mode}")
                mindmap_summary = assistant_result.mindmap.get("summary", {})
                if not isinstance(mindmap_summary, dict):
                    mindmap_summary = {}
                print(
                    "Mindmap: "
                    f"{mindmap_summary.get('node_count', 0)} nodes, "
                    f"{mindmap_summary.get('edge_count', 0)} edges"
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

    if args.component_manifest is None and (
        args.component_runtime is not None
        or args.allow_component_effect
        or args.allow_component_capability
    ):
        parser.error(
            "--component-runtime and component grants require --component-manifest"
        )

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
    component_tool_bindings = {}
    try:
        inputs = (
            strict_json(args.input.read_text(encoding="utf-8")) if args.input else {}
        )
        if not isinstance(inputs, dict):
            raise ValueError("Workflow input must be a JSON object")
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
            for name, provider in _workflow_model_bindings(args, program).items()
        }
        run_result = run(
            program,
            args.workflow,
            args.trace,
            mcp_commands=mcp_commands,
            manifest=run_manifest,
            inputs=inputs,
            model_bindings=bindings,
            tool_bindings=component_tool_bindings or None,
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
    print(run_result)
    print(f"Trace: {args.trace}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
