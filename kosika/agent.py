from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .approval_ledger import ApprovalLedger, ApprovalLedgerError
from .ast import AgentDecl, Program, ToolDecl
from .checker import check
from .model import ChatMessage, ModelProvider, ModelRequest, ModelTool
from .schema import (
    SchemaValidationError,
    schema_for_record,
    schema_for_tool_input,
    validate,
)


class AgentLoopError(RuntimeError):
    """Raised when a model-driven agent run cannot continue safely."""


class ApprovalRequiredError(AgentLoopError):
    """Raised when an execute-capability tool lacks matching approval."""


@dataclass(frozen=True)
class ToolBinding:
    declaration: ToolDecl
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


@dataclass(frozen=True)
class ApprovalRecord:
    plan_hash: str
    approved_by: str
    reason: str = ""
    action_hash: str | None = None


@dataclass(frozen=True)
class AgentRunResult:
    content: str
    plan_hash: str
    model_steps: int
    messages: tuple[ChatMessage, ...]


ToolExecutor = Callable[[ToolDecl, dict[str, Any]], Any]


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def plan_hash(
    agent_name: str,
    task: str,
    bindings: tuple[ToolBinding, ...],
) -> str:
    payload = {
        "agent": agent_name,
        "task": task,
        "tools": [
            {
                "name": binding.declaration.name,
                "source": binding.declaration.source,
                "operation": binding.declaration.operation,
                "access": binding.declaration.access,
                "inputSchema": binding.input_schema,
                "outputSchema": binding.output_schema,
            }
            for binding in bindings
        ],
    }
    return hashlib.sha256(_canonical(payload)).hexdigest()


def action_hash(plan_hash_value: str, tool_name: str, arguments: dict[str, Any]) -> str:
    """Bind one concrete model tool call to its checked agent plan."""
    payload = {
        "plan_hash": plan_hash_value,
        "tool": tool_name,
        "arguments": arguments,
    }
    try:
        return hashlib.sha256(_canonical(payload)).hexdigest()
    except (TypeError, ValueError) as exc:
        raise AgentLoopError("Model tool arguments are not JSON values") from exc


class AgentLoop:
    """Run a model against only the tools granted to one Kosika agent.

    The loop is intentionally small: one model response can request one or more
    declared tools, each tool is validated before and after execution, and the
    next model request receives the tool results. No model output is executed as
    a command and the loop has a strict step limit.
    """

    def __init__(
        self,
        program: Program,
        provider: ModelProvider,
        execute_tool: ToolExecutor,
        *,
        input_schemas: Mapping[str, Mapping[str, Any]] | None = None,
        max_steps: int = 8,
        approval_ledger: ApprovalLedger | None = None,
    ) -> None:
        if max_steps <= 0:
            raise ValueError("Agent loop max_steps must be positive")
        self.program = program
        self.provider = provider
        self.execute_tool = execute_tool
        self.input_schemas = dict(input_schemas or {})
        self.max_steps = max_steps
        self.approval_ledger = approval_ledger

    def plan_hash_for(self, agent_name: str, task: str) -> str:
        """Return the deterministic approval hash for an agent task."""
        check(self.program)
        agent = self._find_agent(agent_name)
        return plan_hash(agent.name, task, self._bindings_for(agent))

    def run(
        self,
        agent_name: str,
        task: str,
        *,
        system_prompt: str | None = None,
        approval: ApprovalRecord | None = None,
    ) -> AgentRunResult:
        current_plan_hash = self.plan_hash_for(agent_name, task)
        agent = self._find_agent(agent_name)
        bindings = self._bindings_for(agent)
        tools = tuple(
            ModelTool(
                name=binding.declaration.name,
                description=(
                    f"MCP operation {binding.declaration.source}/"
                    f"{binding.declaration.operation}"
                ),
                input_schema=binding.input_schema,
            )
            for binding in bindings
        )
        messages: list[ChatMessage] = []
        if system_prompt:
            messages.append(ChatMessage(role="system", content=system_prompt))
        messages.append(ChatMessage(role="user", content=task))
        approval_used = False

        for step in range(1, self.max_steps + 1):
            response = self.provider.complete(
                ModelRequest(messages=tuple(messages), tools=tools)
            )
            assistant = ChatMessage(
                role="assistant",
                content=response.content or "",
                tool_calls=response.tool_calls,
            )
            messages.append(assistant)
            if not response.tool_calls:
                return AgentRunResult(
                    content=response.content or "",
                    plan_hash=current_plan_hash,
                    model_steps=step,
                    messages=tuple(messages),
                )
            if step == self.max_steps:
                raise AgentLoopError(
                    f"Agent loop exceeded maximum model steps ({self.max_steps})"
                )
            # Reject the whole model batch before any tool can have an effect.
            pending = []
            execute_count = 0
            action_to_claim: tuple[str, str] | None = None
            for call in response.tool_calls:
                binding = next(
                    (
                        candidate
                        for candidate in bindings
                        if candidate.declaration.name == call.name
                    ),
                    None,
                )
                if binding is None:
                    raise AgentLoopError(
                        f"Model requested undeclared tool {call.name!r}"
                    )
                self._validate_input(binding, call.arguments)
                if binding.declaration.access == "execute":
                    execute_count += 1
                    if approval_used or execute_count > 1:
                        raise ApprovalRequiredError(
                            "One approval record authorizes at most one execute call"
                        )
                    current_action_hash = action_hash(
                        current_plan_hash, call.name, call.arguments
                    )
                    action_to_claim = (current_action_hash, call.name)
                    if (
                        approval is None
                        or approval.plan_hash != current_plan_hash
                        or approval.action_hash != current_action_hash
                        or not approval.approved_by.strip()
                    ):
                        raise ApprovalRequiredError(
                            f"Tool {call.name!r} with arguments "
                            f"{json.dumps(call.arguments, sort_keys=True)} "
                            f"requires approval for plan {current_plan_hash} "
                            f"and action {current_action_hash}"
                        )
                pending.append((call, binding))
            if action_to_claim is not None:
                if self.approval_ledger is None:
                    raise ApprovalRequiredError(
                        "Execute tools require a persistent approval ledger"
                    )
                assert approval is not None
                try:
                    self.approval_ledger.claim(
                        action_to_claim[0],
                        current_plan_hash,
                        approval.approved_by,
                        action_to_claim[1],
                    )
                except ApprovalLedgerError as exc:
                    raise ApprovalRequiredError(str(exc)) from exc
                approval_used = True
            for call, binding in pending:
                is_execute = binding.declaration.access == "execute"
                try:
                    result = self.execute_tool(binding.declaration, call.arguments)
                    self._validate_output(binding, result)
                except Exception as exc:
                    if is_execute:
                        assert self.approval_ledger is not None
                        assert action_to_claim is not None
                        try:
                            self.approval_ledger.finish(action_to_claim[0], error=exc)
                        except ApprovalLedgerError as ledger_exc:
                            raise AgentLoopError(
                                f"Tool failed and approval outcome could not be recorded: {ledger_exc}"
                            ) from ledger_exc
                    raise
                if is_execute:
                    assert self.approval_ledger is not None
                    assert action_to_claim is not None
                    try:
                        self.approval_ledger.finish(action_to_claim[0], result=result)
                    except ApprovalLedgerError as exc:
                        raise AgentLoopError(
                            f"Tool ran but approval outcome could not be recorded: {exc}"
                        ) from exc
                messages.append(
                    ChatMessage(
                        role="tool",
                        name=call.name,
                        tool_call_id=call.id,
                        content=json.dumps(result, sort_keys=True),
                    )
                )
        raise AgentLoopError("Agent loop terminated without a model response")

    def _find_agent(self, name: str) -> AgentDecl:
        for agent in self.program.agents:
            if agent.name == name:
                return agent
        raise AgentLoopError(f"Unknown Kosika agent {name!r}")

    def _bindings_for(self, agent: AgentDecl) -> tuple[ToolBinding, ...]:
        tools = {tool.name: tool for tool in self.program.tools}
        records = {record.name for record in self.program.records}
        bindings: list[ToolBinding] = []
        for name in agent.can:
            declaration = tools.get(name)
            if declaration is None:
                raise AgentLoopError(
                    f"Agent {agent.name!r} references unknown tool {name!r}"
                )
            if declaration.output not in records:
                raise AgentLoopError(
                    f"Tool {name!r} returns unknown record {declaration.output!r}"
                )
            bindings.append(
                ToolBinding(
                    declaration=declaration,
                    input_schema=dict(
                        self.input_schemas.get(
                            declaration.name,
                            schema_for_tool_input(self.program, declaration.input),
                        )
                    ),
                    output_schema=schema_for_record(self.program, declaration.output),
                )
            )
        return tuple(bindings)

    @staticmethod
    def _validate_input(binding: ToolBinding, arguments: dict[str, Any]) -> None:
        try:
            validate(arguments, binding.input_schema)
        except SchemaValidationError as exc:
            raise AgentLoopError(
                f"Invalid arguments for model tool {binding.declaration.name!r}: {exc}"
            ) from exc

    @staticmethod
    def _validate_output(binding: ToolBinding, result: Any) -> None:
        try:
            validate(result, binding.output_schema)
        except SchemaValidationError as exc:
            raise AgentLoopError(
                f"Invalid result from model tool {binding.declaration.name!r}: {exc}"
            ) from exc
