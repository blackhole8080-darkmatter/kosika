"""Host-defined, bounded async DAGs of read-only typed Kosika workflows."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .agent import ToolExecutor
from .ast import AgentCall, FunctionCall, ModelCall, Program, ToolCall, WorkflowCall
from .checker import check
from .model import ModelProvider
from .runtime import run
from .schema import schema_for_record, validate
from .trace_replay import replay_trace


@dataclass(frozen=True)
class DagNode:
    name: str
    workflow: str
    inputs: dict[str, Any] = field(default_factory=dict)
    dependencies: dict[str, str] = field(default_factory=dict)


def _validate(program: Program, nodes: list[DagNode]) -> list[list[str]]:
    check(program)
    workflows = {item.name: item for item in program.workflows}
    tools = {item.name: item for item in program.tools}
    agents = {item.name: item for item in program.agents}
    by_name = {node.name: node for node in nodes}
    if not nodes or len(nodes) > 64 or len(by_name) != len(nodes):
        raise ValueError("DAG requires 1..64 uniquely named nodes")
    visited: set[str] = set()

    def read_only(name: str) -> None:
        if name in visited:
            return
        visited.add(name)
        for step in workflows[name].steps:
            if isinstance(step.call, WorkflowCall):
                read_only(step.call.workflow_name)
            elif isinstance(step.call, ToolCall):
                if tools[step.call.tool_name].access != "observe":
                    raise PermissionError("Parallel DAG tools must be observe-only")
            elif isinstance(step.call, AgentCall):
                if any(
                    tools[name].access != "observe"
                    for name in agents[step.call.agent_name].can
                ):
                    raise PermissionError("Parallel agents must be observe-only")

    for node in nodes:
        if (
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", node.name) is None
            or node.workflow not in workflows
        ):
            raise ValueError("Invalid DAG node or workflow name")
        read_only(node.workflow)
        expected = {
            item.name: item.type_name for item in workflows[node.workflow].parameters
        }
        if set(node.inputs) & set(node.dependencies) or set(node.inputs) | set(
            node.dependencies
        ) != set(expected):
            raise ValueError("DAG node parameters must be supplied exactly once")
        for name, value in node.inputs.items():
            validate(value, schema_for_record(program, expected[name]))
        for parameter, dependency in node.dependencies.items():
            if (
                dependency not in by_name
                or by_name[dependency].workflow not in workflows
            ):
                raise ValueError("Unknown DAG dependency")
            if (
                workflows[by_name[dependency].workflow].output_type
                != expected[parameter]
            ):
                raise ValueError("DAG dependency output type mismatch")
    remaining = set(by_name)
    layers = []
    while remaining:
        ready = sorted(
            name
            for name in remaining
            if not set(by_name[name].dependencies.values()) & remaining
        )
        if not ready:
            raise ValueError("DAG dependency cycle")
        layers.append(ready)
        remaining.difference_update(ready)
    return layers


async def run_dag(
    program: Program,
    nodes: list[DagNode],
    output: str | Path,
    *,
    concurrency: int = 4,
    models: Mapping[str, Mapping[str, ModelProvider]] | None = None,
    tools: Mapping[str, Mapping[str, ToolExecutor]] | None = None,
) -> dict[str, dict[str, Any]]:
    if type(concurrency) is not int or not 1 <= concurrency <= 16:
        raise ValueError("DAG concurrency must be 1..16")
    layers = _validate(program, nodes)
    workflows = {item.name: item for item in program.workflows}
    declarations = {item.name: item for item in program.tools}
    agents = {item.name: item for item in program.agents}
    for node in nodes:
        granted_models, granted_tools = (
            (models or {}).get(node.name, {}),
            (tools or {}).get(node.name, {}),
        )
        if set(granted_models) - {model.name for model in program.models} or set(
            granted_tools
        ) - set(declarations):
            raise PermissionError("DAG binding names must be declared")
        pending = [node.workflow]
        visited: set[str] = set()
        while pending:
            name = pending.pop()
            if name in visited:
                continue
            visited.add(name)
            for step in workflows[name].steps:
                call = step.call
                required_tools: tuple[str, ...] = ()
                if isinstance(call, WorkflowCall):
                    pending.append(call.workflow_name)
                elif isinstance(call, FunctionCall):
                    continue
                elif isinstance(call, ModelCall):
                    if call.model_name not in granted_models:
                        raise PermissionError("DAG node lacks a model binding")
                elif isinstance(call, AgentCall):
                    agent = agents[call.agent_name]
                    if agent.model not in granted_models:
                        raise PermissionError("DAG agent lacks a model binding")
                    required_tools = agent.can
                else:
                    required_tools = (call.tool_name,)
                if any(
                    tool not in granted_tools
                    and declarations[tool].source != "local_mock"
                    for tool in required_tools
                ):
                    raise PermissionError("DAG node lacks a tool binding")
    # New directory avoids overwriting another run or replay artifact.
    directory = Path(output)
    directory.mkdir(parents=True, exist_ok=False)
    by_name = {node.name: node for node in nodes}
    results: dict[str, dict[str, Any]] = {}
    semaphore = asyncio.Semaphore(concurrency)

    async def execute(name: str) -> None:
        node = by_name[name]
        inputs = {
            **node.inputs,
            **{
                parameter: results[source]
                for parameter, source in node.dependencies.items()
            },
        }
        async with semaphore:
            results[name] = await asyncio.to_thread(
                run,
                program,
                node.workflow,
                directory / f"{name}.jsonl",
                inputs=inputs,
                model_bindings=(models or {}).get(name),
                tool_bindings=(tools or {}).get(name),
            )

    for layer in layers:
        outcomes = await asyncio.gather(
            *(execute(name) for name in layer), return_exceptions=True
        )
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome
    ordered = {name: results[name] for layer in layers for name in layer}
    (directory / "dag.json").write_text(
        json.dumps(
            {
                "version": 1,
                "nodes": [asdict(node) for node in nodes],
                "results": ordered,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return ordered


def replay_dag(directory: str | Path) -> dict[str, dict[str, Any]]:
    from .durable import digest
    from .model_steps import strict_json

    root = Path(directory)
    manifest = strict_json((root / "dag.json").read_text(encoding="utf-8"))
    if (
        not isinstance(manifest, dict)
        or type(manifest.get("version")) is not int
        or manifest.get("version") != 1
        or not isinstance(manifest.get("nodes"), list)
        or not 1 <= len(manifest["nodes"]) <= 64
    ):
        raise ValueError("Invalid DAG manifest")
    results = {}
    starts = {}
    for node in manifest["nodes"]:
        if (
            not isinstance(node, dict)
            or not isinstance(node.get("name"), str)
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", node["name"]) is None
            or node["name"] in results
        ):
            raise ValueError("Invalid or duplicate DAG node")
        name = node["name"]
        trace = root / f"{name}.jsonl"
        if trace.is_symlink():
            raise ValueError("DAG trace links are not permitted")
        results[name] = replay_trace(trace)
        starts[name] = strict_json(trace.read_text(encoding="utf-8").splitlines()[0])
    for node in manifest["nodes"]:
        if not isinstance(node.get("inputs"), dict) or not isinstance(
            node.get("dependencies"), dict
        ):
            raise ValueError("Invalid DAG inputs")
        if any(
            not isinstance(source, str) or source not in results
            for source in node["dependencies"].values()
        ):
            raise ValueError("Invalid DAG dependency")
        if set(node["inputs"]) & set(node["dependencies"]):
            raise ValueError("Duplicate DAG input source")
        inputs = {
            **node["inputs"],
            **{key: results[source] for key, source in node["dependencies"].items()},
        }
        if digest(inputs) != digest(starts[node["name"]].get("inputs")) or node.get(
            "workflow"
        ) != starts[node["name"]].get("workflow"):
            raise ValueError("DAG input trace mismatch")
    if (
        any(not isinstance(start.get("program_hash"), str) for start in starts.values())
        or len({start.get("program_hash") for start in starts.values()}) != 1
    ):
        raise ValueError("DAG program hashes differ")
    remaining = {
        node["name"]: set(node["dependencies"].values()) for node in manifest["nodes"]
    }
    while remaining:
        ready = [
            name
            for name, dependencies in remaining.items()
            if not dependencies & remaining.keys()
        ]
        if not ready:
            raise ValueError("DAG replay dependency cycle")
        for name in ready:
            del remaining[name]
    if digest(results) != digest(manifest.get("results")):
        raise ValueError("DAG result mismatch")
    return results
