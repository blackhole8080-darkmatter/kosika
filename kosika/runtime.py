import hashlib
import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from .agent import ToolExecutor
from .agent_steps import call_agent, validate_agent_transcript
from .ast import (
    AgentCall,
    ArrayExpr,
    Expression,
    FieldExpr,
    IndexExpr,
    LiteralExpr,
    ModelCall,
    Program,
    RecordExpr,
    ToolCall,
    ToolDecl,
    VariableExpr,
    WorkflowCall,
)
from .checker import check
from .durable import ExecutionSession
from .manifest import ManifestError, MCPManifest, verify_server
from .mcp_stdio import MCPStdioClient
from .model import ModelProvider
from .model_steps import call_model
from .schema import schema_for_record, schema_for_tool_input, schemas_equal, validate
from .trace_replay import replay_trace


@dataclass(frozen=True)
class MockMCP:
    """A deterministic fallback used by the original v0.1 example."""

    def call(self, source: str, operation: str) -> dict:
        if source == "local_mock" and operation == "get_disk_info":
            return {"mount": "/", "used_percent": 42.0}
        if source == "local_mock" and operation == "get_structured_info":
            return {
                "mount": "/",
                "tags": ["disk", "local"],
                "metadata": {"name": "root-disk"},
            }
        raise RuntimeError(
            f"Mock MCP operation is not allowlisted: {source}/{operation}"
        )


def _event(event: str, **data: object) -> dict:
    return {
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **data,
    }


def _evaluate_expression(expression: Expression, environment: dict[str, Any]) -> Any:
    if isinstance(expression, LiteralExpr):
        return expression.value
    if isinstance(expression, VariableExpr):
        if expression.name not in environment:
            raise RuntimeError(f"Workflow variable {expression.name!r} is unavailable")
        return environment[expression.name]
    if isinstance(expression, FieldExpr):
        base = _evaluate_expression(expression.base, environment)
        if not isinstance(base, dict):
            raise RuntimeError(
                f"Cannot read field {expression.field_name!r} from "
                f"{type(base).__name__}"
            )
        if expression.field_name not in base:
            raise RuntimeError(
                f"Workflow result has no field {expression.field_name!r}"
            )
        return base[expression.field_name]
    if isinstance(expression, IndexExpr):
        base = _evaluate_expression(expression.base, environment)
        index = _evaluate_expression(expression.index, environment)
        if not isinstance(base, list):
            raise RuntimeError(f"Cannot index non-array value {type(base).__name__}")
        if not isinstance(index, int) or isinstance(index, bool):
            raise RuntimeError("Array indexes must be integers")
        try:
            return base[index]
        except IndexError as exc:
            raise RuntimeError(f"Array index {index} is out of range") from exc
    if isinstance(expression, ArrayExpr):
        return [_evaluate_expression(item, environment) for item in expression.items]
    if isinstance(expression, RecordExpr):
        return {
            name: _evaluate_expression(value, environment)
            for name, value in expression.fields
        }
    raise RuntimeError("Workflow contains an unsupported expression")


def _call_declared_tool(
    tool: ToolDecl,
    arguments: dict[str, Any],
    input_schema: dict,
    output_schema: dict,
    mcp_commands: dict[str, Sequence[str]] | None,
    manifest: MCPManifest | None,
) -> tuple[object, str]:
    if mcp_commands is not None and tool.source in mcp_commands:
        if manifest is None:
            raise ManifestError(
                "Live MCP execution requires a pinned manifest; pass --mcp-manifest"
            )
        if manifest.source != tool.source:
            raise ManifestError(
                f"Pinned manifest source {manifest.source!r} does not match tool source {tool.source!r}"
            )
        pinned = manifest.tools.get(tool.operation)
        if pinned is None:
            raise ManifestError(
                f"No pinned manifest entry for MCP tool {tool.operation!r}"
            )
        if not schemas_equal(pinned.input_schema, input_schema):
            raise ManifestError(
                f"Pinned input schema for {tool.operation!r} does not match "
                f"Kosika record {tool.input!r}"
            )
        if not schemas_equal(pinned.output_schema, output_schema):
            raise ManifestError(
                f"Pinned output schema for {tool.operation!r} does not match Kosika record {tool.output!r}"
            )
        with MCPStdioClient(mcp_commands[tool.source]) as client:
            verify_server(client, manifest)
            advertised = client.require_tool(tool.operation)
            result = client.call_tool(
                advertised.name,
                arguments,
                input_schema=pinned.input_schema,
                output_schema=output_schema,
            )
            return result, "stdio"
    return MockMCP().call(tool.source, tool.operation), "mock"


def run(
    program: Program,
    workflow_name: str,
    trace_path: str | Path | None,
    *,
    mcp_commands: dict[str, Sequence[str]] | None = None,
    manifest: MCPManifest | None = None,
    inputs: dict[str, Any] | None = None,
    model_bindings: Mapping[str, ModelProvider] | None = None,
    tool_bindings: Mapping[str, ToolExecutor] | None = None,
    _events_out: list[dict[str, Any]] | None = None,
    session: ExecutionSession | None = None,
    _scope: tuple[str, ...] = (),
) -> dict[str, Any]:
    if len(_scope) > 64:
        raise ValueError("Workflow call depth exceeds 32")
    if (
        session is not None
        and session.active
        and session.owner != threading.get_ident()
    ):
        raise RuntimeError("Execution session is already active in another thread")
    if session is not None and not session.active:
        with session.lock():
            return run(
                program,
                workflow_name,
                trace_path,
                mcp_commands=mcp_commands,
                manifest=manifest,
                inputs=inputs,
                model_bindings=model_bindings,
                tool_bindings=tool_bindings,
                session=session,
                _events_out=_events_out,
            )
    check(program)
    workflow = next(
        (item for item in program.workflows if item.name == workflow_name), None
    )
    if workflow is None:
        raise ValueError(f"Unknown workflow {workflow_name!r}")
    tools = {tool.name: tool for tool in program.tools}
    agents = {agent.name: agent for agent in program.agents}
    models = {model.name: model for model in program.models}
    workflows = {item.name: item for item in program.workflows}
    providers = dict(model_bindings or {})
    host_tools = dict(tool_bindings or {})
    if set(host_tools) - set(tools):
        raise ValueError("Host supplied bindings for undeclared tools")
    if set(providers) - set(models):
        raise ValueError("Host supplied bindings for undeclared models")
    environment: dict[str, Any] = dict(inputs or {})
    expected_inputs = {parameter.name for parameter in workflow.parameters}
    if set(environment) != expected_inputs:
        raise ValueError(
            f"Workflow requires input parameters {sorted(expected_inputs)!r}"
        )
    for parameter in workflow.parameters:
        validate(
            environment[parameter.name], schema_for_record(program, parameter.type_name)
        )
    json.dumps(environment, allow_nan=False)
    if session is not None and not _scope:
        session.bind(
            {
                "program": asdict(program),
                "workflow": workflow_name,
                "inputs": environment,
                "manifest": asdict(manifest) if manifest else None,
                "mcp_commands": mcp_commands,
            }
        )
    # Preflight every capability before invoking any tool in the workflow.
    pending = list(workflow.steps)
    visited_workflows = {workflow.name}
    while pending:
        step = pending.pop(0)
        if isinstance(step.call, WorkflowCall):
            name = step.call.workflow_name
            if name not in visited_workflows:
                visited_workflows.add(name)
                pending.extend(workflows[name].steps)
            continue
        if isinstance(step.call, AgentCall):
            asked_agent = agents[step.call.agent_name]
            if asked_agent.model not in providers:
                raise PermissionError(
                    f"Agent {asked_agent.name!r} has no authorized model binding"
                )
            for name in asked_agent.can:
                permitted = tools[name]
                if permitted.access != "observe":
                    raise PermissionError(
                        "Workflow ask currently permits observe tools only"
                    )
                if (
                    name not in host_tools
                    and permitted.source not in (mcp_commands or {})
                    and permitted.source != "local_mock"
                ):
                    raise PermissionError(
                        f"Agent tool {name!r} has no authorized host binding"
                    )
            continue
        if isinstance(step.call, ModelCall):
            if step.call.model_name not in providers:
                raise PermissionError(
                    f"Model {step.call.model_name!r} has no authorized host binding"
                )
            continue
        tool = tools[step.call.tool_name]
        if tool.access == "execute" and session is None:
            raise PermissionError(
                f"Workflow tool {tool.name!r} requires a verified approval record"
            )
    events = [
        _event(
            "run_start",
            workflow=workflow.name,
            trace_version=2,
            program_hash=hashlib.sha256(
                json.dumps(asdict(program), sort_keys=True).encode()
            ).hexdigest(),
            inputs=environment.copy(),
            output_schema=schema_for_record(program, workflow.output_type),
        )
    ]
    result: dict[str, Any] = {}
    for step in workflow.steps:
        arguments = {
            name: _evaluate_expression(expression, environment)
            for name, expression in step.call.arguments
        }
        scope = (*_scope, workflow.name, step.name)
        action = None
        if isinstance(step.call, WorkflowCall):
            step_output = workflows[step.call.workflow_name].output_type
        elif isinstance(step.call, ModelCall):
            step_output = models[step.call.model_name].output
        elif isinstance(step.call, AgentCall):
            step_output = models[agents[step.call.agent_name].model or ""].output
        else:
            step_output = tools[step.call.tool_name].output
        if session is not None and not isinstance(step.call, WorkflowCall):
            cached = session.cached(
                scope, arguments, events[0], schema_for_record(program, step_output)
            )
            if cached is not None:
                result = cached[-1]["result"]
                environment[step.binding_name] = result
                events.extend(cached)
                continue
            mutation = (
                tools[step.call.tool_name].name
                if isinstance(step.call, ToolCall)
                and tools[step.call.tool_name].access == "execute"
                else None
            )
            # Check arguments before claiming approval or recording a started step.
            if isinstance(step.call, ToolCall):
                validate(
                    arguments,
                    schema_for_tool_input(program, tools[step.call.tool_name].input),
                )
            action = session.start(scope, mutation, arguments)
        step_offset = len(events)
        events.append(_event("step_start", step=step.name))
        if isinstance(step.call, WorkflowCall):
            child = workflows[step.call.workflow_name]
            events.append(
                _event("workflow_call", workflow=child.name, arguments=arguments)
            )
            child_events: list[dict[str, Any]] = []
            result = run(
                program,
                child.name,
                None,
                inputs=arguments,
                model_bindings=providers,
                tool_bindings=host_tools,
                mcp_commands=mcp_commands,
                manifest=manifest,
                _events_out=child_events,
                session=session,
                _scope=scope,
            )
            events.append(
                _event(
                    "workflow_return",
                    workflow=child.name,
                    result=result,
                    events=child_events,
                )
            )
        elif isinstance(step.call, AgentCall):
            asked_agent = agents[step.call.agent_name]
            assert asked_agent.model is not None
            model = models[asked_agent.model]
            input_schema = schema_for_record(program, model.input)
            output_schema = schema_for_record(program, model.output)
            validate(arguments, input_schema)
            call_event = _event(
                "agent_call",
                agent=asked_agent.name,
                model=model.name,
                arguments=arguments,
                input_schema=input_schema,
                output_schema=output_schema,
                max_steps=asked_agent.max_steps,
                tools={
                    name: {
                        "input_schema": schema_for_tool_input(
                            program, tools[name].input
                        ),
                        "output_schema": schema_for_record(program, tools[name].output),
                    }
                    for name in asked_agent.can
                },
            )
            events.append(call_event)

            def execute_agent_tool(
                declaration: ToolDecl, values: dict[str, Any]
            ) -> Any:
                if declaration.access != "observe":
                    raise PermissionError(
                        "Workflow ask currently permits observe tools only"
                    )
                if declaration.name in host_tools:
                    return host_tools[declaration.name](declaration, values)
                outcome, _ = _call_declared_tool(
                    declaration,
                    values,
                    schema_for_tool_input(program, declaration.input),
                    schema_for_record(program, declaration.output),
                    mcp_commands,
                    manifest,
                )
                return outcome

            result, agent_response = call_agent(
                program,
                asked_agent,
                providers[model.name],
                execute_agent_tool,
                arguments,
                output_schema,
            )
            return_event = _event(
                "agent_return", agent=asked_agent.name, result=result, **agent_response
            )
            validate_agent_transcript(call_event, return_event)
            events.append(return_event)
        elif isinstance(step.call, ModelCall):
            model = models[step.call.model_name]
            input_schema = schema_for_record(program, model.input)
            output_schema = schema_for_record(program, model.output)
            validate(arguments, input_schema)
            events.append(
                _event(
                    "model_call",
                    model=model.name,
                    arguments=arguments,
                    input_schema=input_schema,
                    output_schema=output_schema,
                )
            )
            result, response = call_model(
                providers[model.name], arguments, output_schema
            )
            events.append(
                _event("model_return", model=model.name, result=result, **response)
            )
        else:
            tool = tools[step.call.tool_name]
            agent = agents[step.call.agent_name]
            input_schema = schema_for_tool_input(program, tool.input)
            output_schema = schema_for_record(program, tool.output)
            validate(arguments, input_schema)
            events.append(
                _event(
                    "tool_call",
                    agent=agent.name,
                    tool=tool.name,
                    source=tool.source,
                    operation=tool.operation,
                    access=tool.access,
                    arguments=arguments,
                )
            )
            if tool.name in host_tools:
                outcome, transport = host_tools[tool.name](tool, arguments), "host"
            else:
                outcome, transport = _call_declared_tool(
                    tool, arguments, input_schema, output_schema, mcp_commands, manifest
                )
            validate(outcome, output_schema)
            result = cast(dict[str, Any], outcome)
            events.append(
                _event(
                    "tool_return", tool=tool.name, transport=transport, result=result
                )
            )
        environment[step.binding_name] = result
        events.append(
            _event(
                "step_complete",
                step=step.name,
                binding=step.binding_name,
                result=result,
            )
        )
        if session is not None and not isinstance(step.call, WorkflowCall):
            session.complete(scope, events[step_offset:], action)
    events.append(_event("run_complete", workflow=workflow.name, result=result))

    if _events_out is not None:
        _events_out.extend(events)
    if trace_path is None:
        return result
    path = Path(trace_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(item, sort_keys=True, allow_nan=False) for item in events)
        + "\n",
        encoding="utf-8",
    )
    return result


def replay(trace_path: str | Path) -> dict:
    return replay_trace(trace_path)
