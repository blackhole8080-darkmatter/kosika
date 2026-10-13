from __future__ import annotations

import hashlib
import json
import math
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping, TypedDict

from .agent_trace import write_agent_trace
from .approval_ledger import ApprovalLedger, ApprovalLedgerError
from .ast import AgentDecl, ModelDecl, Program, ToolDecl
from .checker import check
from .controls import ExecutionError
from .execution import ExecutionControl, ExecutionLimits
from .model import (
    ChatMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelTool,
    ModelToolCall,
    ModelUsage,
)
from .model_steps import strict_json
from .schema import (
    SchemaValidationError,
    schema_for_record,
    schema_for_tool_input,
    validate,
)
from .skills import SkillDefinition, SkillRouter, SkillSelection

if TYPE_CHECKING:
    from .durable import AgentExecutionSession


class AgentLoopError(RuntimeError):
    """Raised when a model-driven agent run cannot continue safely."""

    def __init__(self, message: str, *, code: str = "agent"):
        super().__init__(message)
        self.code = code


class ApprovalRequiredError(AgentLoopError):
    """Raised when an execute-capability tool lacks matching approval."""


class _AgentLimits(TypedDict):
    max_steps: int
    max_tool_calls: int
    max_model_calls: int
    deadline_seconds: float | None


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
    selected_skills: tuple[str, ...] = ()
    structured_output: dict[str, Any] | None = None
    trace_events: tuple[dict[str, Any], ...] = ()


ToolExecutor = Callable[[ToolDecl, dict[str, Any]], Any]
#: Called with every change a planning agent asked for in one reply, in
#: order, before any of them happens. It returns to let them all happen, or
#: raises to stop: to wait for a person, or because the answer was no.
PlanApprover = Callable[[list[tuple[ToolDecl, dict[str, Any]]]], None]
#: Called with the same list first. What it returns is what is wrong with
#: the plan, in words for the model; an empty list means nothing is.
PlanReviewer = Callable[[list[tuple[ToolDecl, dict[str, Any]]]], list[str]]
#: How many times a plan found wanting goes back to the model.
PLAN_RETRIES = 2


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def plan_hash(
    agent_name: str,
    task: str,
    bindings: tuple[ToolBinding, ...],
    skills: tuple[SkillDefinition, ...] = (),
    agent_config: Mapping[str, Any] | None = None,
    input_value: Mapping[str, Any] | None = None,
) -> str:
    payload: dict[str, Any] = {
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
    if skills:
        payload["skills"] = [
            {
                "id": skill.skill_id,
                "version": skill.version,
                "digest": skill.digest,
            }
            for skill in skills
        ]
    if agent_config is not None:
        payload["agentConfig"] = agent_config
    if input_value is not None:
        payload["input"] = dict(input_value)
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


def describe_tool(tool: ToolDecl) -> str:
    """What a tool is for, in words a model can choose by.

    A model picks a tool from its name and this sentence, so the sentence
    says what the tool does and what to put in it.
    """

    source, operation = tool.source, tool.operation
    asks = " The person is asked before it happens." if tool.access == "execute" else ""
    if source == "system:files":
        return {
            "list": "List the names of the files and folders in a folder. `path` "
            'is relative to the granted folder; use "." for the top. Folder '
            "names end with a slash.",
            "read": "Read a whole text file. `path` is relative to the granted "
            "folder, such as notes/todo.txt.",
            "peek": "Read the beginning of a text file, up to 4000 characters. "
            "`path` is relative to the granted folder. Use this to look at a "
            "file without reading all of it.",
            "search": "Search the text files under a folder for the words in "
            '`query`. `path` is the folder, "." for the top. It gives back '
            "matching lines as path:line: text, best first. Use a few key "
            "words, not a sentence.",
            "write": "Write `text` to the file at `path`, replacing it." + asks,
            "create": "Create a new text file at `path` holding `text`. It "
            "never replaces an existing file." + asks,
            "write_all": "Write every file in `files`, each to its `path` with "
            "its `text`, replacing what is there." + asks,
        }.get(operation, f"File operation {operation}.")
    if source == "system:shell":
        return (
            f"Run the program {operation} with `args`, a list of its arguments, "
            "inside the granted folder. It gives back the exit code and what "
            "the program printed." + asks
        )
    if source == "system:kosika":
        return (
            "Check a Kosika program given as text in `source` and run the "
            "tests written in it. It says whether it is ok and, if not, the "
            "first thing wrong."
        )
    if source == "system:web":
        return (
            "Read one web page. `url` is a full https address on a site the "
            "person allowed. It gives back the page's title and text."
        )
    if source.startswith("memory:"):
        name = source.removeprefix("memory:")
        return (
            f"Look up what is stored under `key` in memory {name}."
            if operation == "recall"
            else f"Store `value` under `key` in memory {name}."
        )
    if source.startswith("board:"):
        name = source.removeprefix("board:")
        return (
            f"Read every post on board {name}."
            if operation == "read"
            else f"Post to board {name}, where the other agents can read it."
        )
    return f"MCP operation {source}/{operation}"


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
        max_steps: int | None = None,
        max_tool_calls: int | None = None,
        max_model_calls: int | None = None,
        deadline_seconds: float | None = None,
        cancel: threading.Event | None = None,
        approval_ledger: ApprovalLedger | None = None,
        skill_router: SkillRouter | None = None,
        skill_policy: str = "disabled",
        explicit_skills: tuple[str, ...] = (),
        max_skills: int = 6,
        tell_failures: bool = False,
        defer_approval: bool = False,
        tool_descriptions: Mapping[str, str] | None = None,
        approve_plan: PlanApprover | None = None,
        review_plan: PlanReviewer | None = None,
    ) -> None:
        #: Looks at a plan before a person is asked about it. A plan it finds
        #: wanting goes back to the model, PLAN_RETRIES times at most.
        self.review_plan = review_plan
        #: Set for an agent that plans first. The changes asked for in one
        #: reply are its plan: they are handed over whole before any happens,
        #: and the agent gets no second set of changes.
        self.approve_plan = approve_plan
        #: Words for a tool that only its host knows, such as what an MCP
        #: server says its own tool does. They replace the general sentence.
        self.tool_descriptions = dict(tool_descriptions or {})
        #: When set, the loop does not judge approvals itself: whoever runs
        #: the tools does, for every call that changes the world. A workflow
        #: step uses this, because its run already gates each such call on a
        #: recorded approval and can pause for a person.
        self.defer_approval = defer_approval
        #: When set, a look that goes wrong is told to the model instead of
        #: ending the run: a missing file or a bad argument. The model reads `{"error": ...}` as that tool's result
        #: and can try something else. This never applies to a tool that
        #: changes the world, whose failure may mean it half happened, and a
        #: request for a tool the agent was never given still ends the run.
        self.tell_failures = tell_failures
        for name, value in (
            ("max_steps", max_steps),
            ("max_tool_calls", max_tool_calls),
            ("max_model_calls", max_model_calls),
        ):
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(f"Agent loop {name} must be a positive integer")
        if deadline_seconds is not None and (
            type(deadline_seconds) not in (int, float)
            or not math.isfinite(deadline_seconds)
            or not 0 < deadline_seconds <= 86_400
        ):
            raise ValueError(
                "Agent loop deadline_seconds must be finite and between 0 and 86400"
            )
        if max_skills <= 0:
            raise ValueError("Agent loop max_skills must be positive")
        if skill_router is None and (skill_policy != "disabled" or explicit_skills):
            raise ValueError("Skill routing requires a skill router")
        self.program = program
        self.provider = provider
        self.execute_tool = execute_tool
        self.input_schemas = dict(input_schemas or {})
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.max_model_calls = max_model_calls
        self.deadline_seconds = deadline_seconds
        self.cancel = cancel if cancel is not None else threading.Event()
        self.approval_ledger = approval_ledger
        self.skill_router = skill_router
        self.skill_policy = skill_policy
        self.explicit_skills = tuple(explicit_skills)
        self.max_skills = max_skills

    def _skills_for(self, task: str) -> SkillSelection:
        if self.skill_router is None:
            return SkillSelection(policy="disabled")
        return self.skill_router.select(
            task,
            explicit=self.explicit_skills,
            policy=self.skill_policy,
            max_skills=self.max_skills,
        )

    def _find_model(self, name: str) -> ModelDecl:
        for model in self.program.models:
            if model.name == name:
                return model
        raise AgentLoopError(f"Unknown Kosika model {name!r}")

    def _model_for(self, agent: AgentDecl) -> ModelDecl | None:
        return self._find_model(agent.model) if agent.model is not None else None

    def _limits_for(self, agent: AgentDecl) -> _AgentLimits:
        return {
            "max_steps": self.max_steps
            if self.max_steps is not None
            else agent.max_steps,
            "max_tool_calls": self.max_tool_calls
            if self.max_tool_calls is not None
            else agent.max_tool_calls,
            "max_model_calls": self.max_model_calls
            if self.max_model_calls is not None
            else agent.max_model_calls,
            "deadline_seconds": (
                self.deadline_seconds
                if self.deadline_seconds is not None
                else agent.deadline_seconds
            ),
        }

    @staticmethod
    def _config_for(
        agent: AgentDecl,
        model: ModelDecl | None,
        limits: _AgentLimits,
    ) -> dict[str, Any]:
        model_config = None
        if model is not None:
            model_config = {
                "name": model.name,
                "deployment": model.deployment,
                "parameters": {
                    "temperature": model.parameters.temperature,
                    "max_output_tokens": model.parameters.max_output_tokens,
                    "top_p": model.parameters.top_p,
                    "frequency_penalty": model.parameters.frequency_penalty,
                    "presence_penalty": model.parameters.presence_penalty,
                    "stop": model.parameters.stop,
                },
            }
        return {
            "instructions": agent.instructions,
            "model": model_config,
            "limits": dict(limits),
        }

    @staticmethod
    def _prepare_input(
        program: Program,
        model: ModelDecl | None,
        input_value: Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any], str] | None:
        if input_value is None:
            return None
        if model is None:
            raise AgentLoopError("Structured agent input requires a declared model")
        if not isinstance(input_value, Mapping):
            raise AgentLoopError("Structured agent input must be a JSON object")
        value = dict(input_value)
        try:
            validate(value, schema_for_record(program, model.input))
        except SchemaValidationError as exc:
            raise AgentLoopError(f"Invalid structured agent input: {exc}") from exc
        try:
            return value, json.dumps(value, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise AgentLoopError("Structured agent input must be JSON values") from exc

    @staticmethod
    def _structured_output(
        program: Program,
        model: ModelDecl | None,
        content: str | None,
    ) -> dict[str, Any]:
        if model is None or not isinstance(content, str):
            raise AgentLoopError("Structured agent output must be JSON text")
        try:
            value = strict_json(content)
            validate(value, schema_for_record(program, model.output))
        except (TypeError, ValueError, SchemaValidationError) as exc:
            raise AgentLoopError(f"Invalid structured agent output: {exc}") from exc
        if not isinstance(value, dict):
            raise AgentLoopError("Structured agent output must be a JSON object")
        return value

    def plan_hash_for(
        self,
        agent_name: str,
        task: str = "Run the agent.",
        *,
        input_value: Mapping[str, Any] | None = None,
    ) -> str:
        """Return the deterministic approval hash for an agent task."""
        check(self.program)
        agent = self._find_agent(agent_name)
        model = self._model_for(agent)
        prepared_input = self._prepare_input(self.program, model, input_value)
        limits = self._limits_for(agent)
        selection = self._skills_for(task)
        return plan_hash(
            agent.name,
            task,
            self._bindings_for(agent),
            selection.selected,
            self._config_for(agent, model, limits),
            input_value=prepared_input[0] if prepared_input is not None else None,
        )

    @staticmethod
    def _message_from_checkpoint(value: Any) -> ChatMessage:
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("role"), str)
            or not isinstance(value.get("content", ""), str)
        ):
            raise AgentLoopError(
                "Agent journal contains an invalid message",
                code="journal_invalid",
            )
        raw_calls = value.get("tool_calls", [])
        if not isinstance(raw_calls, list):
            raise AgentLoopError(
                "Agent journal contains invalid message tool calls",
                code="journal_invalid",
            )
        calls: list[ModelToolCall] = []
        for raw_call in raw_calls:
            if (
                not isinstance(raw_call, dict)
                or not isinstance(raw_call.get("id"), str)
                or not isinstance(raw_call.get("name"), str)
                or not isinstance(raw_call.get("arguments"), dict)
            ):
                raise AgentLoopError(
                    "Agent journal contains an invalid tool call",
                    code="journal_invalid",
                )
            calls.append(
                ModelToolCall(raw_call["id"], raw_call["name"], raw_call["arguments"])
            )
        return ChatMessage(
            role=value.get("role", ""),
            content=value.get("content", ""),
            tool_calls=tuple(calls),
            tool_call_id=value.get("tool_call_id"),
            name=value.get("name"),
        )

    @classmethod
    def _response_from_checkpoint(cls, value: Any) -> ModelResponse:
        if not isinstance(value, dict):
            raise AgentLoopError(
                "Agent journal contains an invalid model response",
                code="journal_invalid",
            )
        content = value.get("content")
        finish_reason = value.get("finish_reason", "stop")
        if content is not None and not isinstance(content, str):
            raise AgentLoopError(
                "Agent journal contains invalid model content",
                code="journal_invalid",
            )
        if not isinstance(finish_reason, str):
            raise AgentLoopError(
                "Agent journal contains an invalid finish reason",
                code="journal_invalid",
            )
        message = cls._message_from_checkpoint(
            {
                "role": "assistant",
                "content": content or "",
                "tool_calls": value.get("tool_calls", []),
            }
        )
        usage = value.get("usage")
        if usage is not None and not isinstance(usage, dict):
            raise AgentLoopError(
                "Agent journal contains invalid model usage",
                code="journal_invalid",
            )
        try:
            model_usage = ModelUsage(**usage) if usage is not None else None
        except TypeError as exc:
            raise AgentLoopError(
                "Agent journal contains invalid model usage",
                code="journal_invalid",
            ) from exc
        return ModelResponse(
            content=content,
            tool_calls=message.tool_calls,
            finish_reason=finish_reason,
            usage=model_usage,
        )

    @classmethod
    def _result_from_checkpoint(
        cls, payload: dict[str, Any], plan_hash_value: str
    ) -> AgentRunResult:
        messages = payload.get("messages")
        selected_skills = payload.get("selected_skills", [])
        trace_events = payload.get("trace_events", [])
        structured_output = payload.get("structured_output")
        if (
            not isinstance(messages, list)
            or not isinstance(selected_skills, list)
            or not all(isinstance(skill, str) for skill in selected_skills)
            or not isinstance(trace_events, list)
            or not all(isinstance(event, dict) for event in trace_events)
            or not isinstance(payload.get("content"), str)
            or type(payload.get("model_steps")) is not int
            or (
                structured_output is not None
                and not isinstance(structured_output, dict)
            )
        ):
            raise AgentLoopError(
                "Agent journal contains an invalid completed result",
                code="journal_invalid",
            )
        return AgentRunResult(
            content=payload["content"],
            plan_hash=plan_hash_value,
            model_steps=payload["model_steps"],
            messages=tuple(cls._message_from_checkpoint(item) for item in messages),
            selected_skills=tuple(selected_skills),
            structured_output=structured_output,
            trace_events=tuple(trace_events),
        )

    def run(
        self,
        agent_name: str,
        task: str = "Run the agent.",
        *,
        input_value: Mapping[str, Any] | None = None,
        system_prompt: str | None = None,
        approval: ApprovalRecord | None = None,
        trace_path: str | Path | None = None,
        session: "AgentExecutionSession | None" = None,
    ) -> AgentRunResult:
        if session is None:
            return self._run(
                agent_name,
                task,
                input_value=input_value,
                system_prompt=system_prompt,
                approval=approval,
                trace_path=trace_path,
            )
        try:
            with session.lock():
                return self._run(
                    agent_name,
                    task,
                    input_value=input_value,
                    system_prompt=system_prompt,
                    approval=approval,
                    trace_path=trace_path,
                    session=session,
                )
        except ExecutionError as exc:
            raise AgentLoopError(str(exc), code=exc.code) from exc

    def _run(
        self,
        agent_name: str,
        task: str = "Run the agent.",
        *,
        input_value: Mapping[str, Any] | None = None,
        system_prompt: str | None = None,
        approval: ApprovalRecord | None = None,
        trace_path: str | Path | None = None,
        session: "AgentExecutionSession | None" = None,
    ) -> AgentRunResult:
        selection = self._skills_for(task)
        agent = self._find_agent(agent_name)
        model = self._model_for(agent)
        prepared_input = self._prepare_input(self.program, model, input_value)
        limits = self._limits_for(agent)
        bindings = self._bindings_for(agent)
        current_plan_hash = plan_hash(
            agent.name,
            task,
            bindings,
            selection.selected,
            self._config_for(agent, model, limits),
            input_value=prepared_input[0] if prepared_input is not None else None,
        )
        tools = tuple(
            ModelTool(
                name=binding.declaration.name,
                description=self.tool_descriptions.get(binding.declaration.name)
                or describe_tool(binding.declaration),
                input_schema=binding.input_schema,
            )
            for binding in bindings
        )
        input_schema = (
            schema_for_record(self.program, model.input)
            if model is not None and prepared_input is not None
            else None
        )
        output_schema = (
            schema_for_record(self.program, model.output)
            if model is not None and prepared_input is not None
            else None
        )
        trace_events: list[dict[str, Any]] = [
            {
                "event": "agent_run_start",
                "trace_version": 1,
                "agent": agent.name,
                "task": task,
                "plan_hash": current_plan_hash,
                "input": prepared_input[0] if prepared_input is not None else None,
                "input_schema": input_schema,
                "output_schema": output_schema,
                "limits": dict(limits),
                "selected_skills": list(selection.ids),
                "tools": {
                    binding.declaration.name: {
                        "input_schema": binding.input_schema,
                        "output_schema": binding.output_schema,
                    }
                    for binding in bindings
                },
            }
        ]
        messages: list[ChatMessage] = []
        if agent.instructions:
            messages.append(ChatMessage(role="system", content=agent.instructions))
        if system_prompt:
            messages.append(ChatMessage(role="system", content=system_prompt))
        skill_context = selection.render_context()
        if skill_context:
            messages.append(ChatMessage(role="system", content=skill_context))
        messages.append(
            ChatMessage(
                role="user",
                content=prepared_input[1] if prepared_input is not None else task,
            )
        )
        execution = ExecutionControl(
            ExecutionLimits(
                max_steps=int(limits["max_steps"]),
                max_model_calls=int(limits["max_model_calls"]),
                max_tool_calls=int(limits["max_tool_calls"]),
                deadline_seconds=limits["deadline_seconds"],
            ),
            cancel=self.cancel,
        )
        controlled_provider = execution.provider(self.provider)
        journal_metadata = {
            "agent": agent.name,
            "task": task,
            "input": prepared_input[0] if prepared_input is not None else None,
            "system_prompt": system_prompt,
            "plan_hash": current_plan_hash,
        }
        approval_used = False
        plan_done = False
        plans_sent_back = 0
        resume_response: ModelResponse | None = None
        resume_step = 0
        resume_tool_index = 0

        def checkpoint_payload(
            *,
            step: int,
            response: ModelResponse | None = None,
            tool_index: int = 0,
        ) -> dict[str, Any]:
            return {
                "metadata": journal_metadata,
                "trace_events": trace_events,
                "messages": [asdict(message) for message in messages],
                "step": step,
                "response": asdict(response) if response is not None else None,
                "tool_index": tool_index,
                "used": dict(execution.counts),
                "approval_used": approval_used,
            }

        if session is not None:
            saved = session.bind(current_plan_hash, journal_metadata)
            if saved is not None:
                state = saved.get("state")
                payload = saved.get("payload")
                if not isinstance(payload, dict):
                    raise ExecutionError(
                        "journal_invalid", "Agent journal checkpoint is invalid"
                    )
                if state == "completed":
                    result = self._result_from_checkpoint(payload, current_plan_hash)
                    if trace_path is not None:
                        write_agent_trace(trace_path, list(result.trace_events))
                    return result
                if state in {"model_pending", "tool_pending"}:
                    raise ExecutionError(
                        "uncertain_outcome",
                        "An in-flight model or tool call requires reconciliation; "
                        "it will not be repeated",
                    )
                if state not in {"ready", "model_response", "tool_result"}:
                    raise ExecutionError(
                        "journal_invalid",
                        f"Unsupported agent journal state {state!r}",
                    )
                if "trace_events" in payload:
                    raw_events = payload.get("trace_events")
                    raw_messages = payload.get("messages")
                    if not isinstance(raw_events, list) or not isinstance(
                        raw_messages, list
                    ):
                        raise ExecutionError(
                            "journal_invalid", "Agent journal messages are invalid"
                        )
                    trace_events = raw_events
                    messages = [
                        self._message_from_checkpoint(item) for item in raw_messages
                    ]
                    used = payload.get("used")
                    if not isinstance(used, dict):
                        raise ExecutionError(
                            "journal_invalid", "Agent journal counters are invalid"
                        )
                    execution.restore(used)
                    approval_used = payload.get("approval_used", False)
                    if type(approval_used) is not bool:
                        raise ExecutionError(
                            "journal_invalid", "Agent journal approval state is invalid"
                        )
                    resume_step = payload.get("step", 0)
                    resume_tool_index = payload.get("tool_index", 0)
                    if (
                        type(resume_step) is not int
                        or type(resume_tool_index) is not int
                    ):
                        raise ExecutionError(
                            "journal_invalid", "Agent journal position is invalid"
                        )
                    if state in {"model_response", "tool_result"}:
                        resume_response = self._response_from_checkpoint(
                            payload.get("response")
                        )
                elif state != "ready":
                    raise ExecutionError(
                        "journal_invalid", "Agent journal checkpoint is incomplete"
                    )
            else:
                session.checkpoint("ready", checkpoint_payload(step=0))

        first_step = (
            resume_step + 1
            if resume_response is None and resume_step > 0
            else (resume_step if resume_response is not None else 1)
        )
        for step in range(first_step, int(limits["max_steps"]) + 1):
            resuming_response = resume_response is not None
            if not resuming_response and execution.counts["model_calls"] >= int(
                limits["max_model_calls"]
            ):
                raise AgentLoopError(
                    "Agent loop exceeded maximum model calls "
                    f"({limits['max_model_calls']})",
                    code="budget",
                )
            if resuming_response:
                response = resume_response
                resume_response = None
                assert response is not None
            else:
                try:
                    execution.consume("steps")
                    request = ModelRequest(
                        messages=tuple(messages),
                        tools=tools,
                        model=model.deployment if model is not None else None,
                        temperature=(
                            model.parameters.temperature if model is not None else None
                        ),
                        max_output_tokens=(
                            model.parameters.max_output_tokens
                            if model is not None
                            else None
                        ),
                        top_p=model.parameters.top_p if model is not None else None,
                        frequency_penalty=(
                            model.parameters.frequency_penalty
                            if model is not None
                            else None
                        ),
                        presence_penalty=(
                            model.parameters.presence_penalty
                            if model is not None
                            else None
                        ),
                        stop=model.parameters.stop if model is not None else (),
                    )
                    trace_events.append(
                        {
                            "event": "agent_model_request",
                            "step": step,
                            **asdict(request),
                        }
                    )
                    if session is not None:
                        session.checkpoint(
                            "model_pending",
                            checkpoint_payload(step=step),
                        )
                    response = controlled_provider.complete(request)
                    trace_events.append(
                        {
                            "event": "agent_model_response",
                            "step": step,
                            **asdict(response),
                        }
                    )
                    if session is not None:
                        session.checkpoint(
                            "model_response",
                            checkpoint_payload(step=step, response=response),
                        )
                except ExecutionError as exc:
                    raise AgentLoopError(str(exc), code=exc.code) from exc
            if resuming_response:
                resume_tool_index = max(0, resume_tool_index)
            else:
                resume_tool_index = 0
            assistant = ChatMessage(
                role="assistant",
                content=response.content or "",
                tool_calls=response.tool_calls,
            )
            if not resuming_response or resume_tool_index == 0:
                messages.append(assistant)
            if not response.tool_calls:
                structured_output = (
                    self._structured_output(self.program, model, response.content)
                    if prepared_input is not None
                    else None
                )
                complete_event = {
                    "event": "agent_run_complete",
                    "agent": agent.name,
                    "plan_hash": current_plan_hash,
                    "content": response.content or "",
                    "structured_output": structured_output,
                    "model_steps": step,
                    "messages": [asdict(message) for message in messages],
                    "selected_skills": list(selection.ids),
                }
                trace_events.append(complete_event)
                if session is not None:
                    session.checkpoint(
                        "completed",
                        {
                            "metadata": journal_metadata,
                            "content": response.content or "",
                            "structured_output": structured_output,
                            "model_steps": step,
                            "messages": [asdict(message) for message in messages],
                            "selected_skills": list(selection.ids),
                            "trace_events": trace_events,
                        },
                    )
                if trace_path is not None:
                    write_agent_trace(trace_path, trace_events)
                return AgentRunResult(
                    content=response.content or "",
                    plan_hash=current_plan_hash,
                    model_steps=step,
                    messages=tuple(messages),
                    selected_skills=selection.ids,
                    structured_output=structured_output,
                    trace_events=tuple(trace_events),
                )
            if step == limits["max_steps"]:
                raise AgentLoopError(
                    f"Agent loop exceeded maximum model steps ({limits['max_steps']})"
                )
            # Reject the whole model batch before any tool can have an effect.
            pending: list[tuple[Any, ToolBinding | None, str | None]] = []
            execute_count = 0
            action_to_claim: tuple[str, str] | None = None
            if not 0 <= resume_tool_index <= len(response.tool_calls):
                raise AgentLoopError(
                    "Agent journal tool position is invalid", code="journal_invalid"
                )
            for call in response.tool_calls[resume_tool_index:]:
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
                try:
                    self._validate_input(binding, call.arguments)
                except AgentLoopError as exc:
                    if (
                        not self.tell_failures
                        or binding.declaration.access == "execute"
                    ):
                        raise
                    pending.append((call, binding, str(exc)))
                    continue
                if binding.declaration.access == "execute" and not self.defer_approval:
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
                pending.append((call, binding, None))
            if execution.counts["tool_calls"] + len(pending) > int(
                limits["max_tool_calls"]
            ):
                raise AgentLoopError(
                    "Agent loop exceeded maximum tool calls "
                    f"({limits['max_tool_calls']})",
                    code="budget",
                )
            planned = [
                (binding.declaration, call.arguments)
                for call, binding, problem in pending
                if binding is not None
                and problem is None
                and binding.declaration.access == "execute"
            ]
            if self.approve_plan is not None and planned:
                if plan_done:
                    # Its plan was approved and carried out. Anything more
                    # was never shown to the person, so it does not happen:
                    # the model is told so and can still give its answer.
                    pending = [
                        (
                            call,
                            binding,
                            "Your plan has already been carried out. No more "
                            "changes can be made. Give your answer now."
                            if binding is not None
                            and problem is None
                            and binding.declaration.access == "execute"
                            else problem,
                        )
                        for call, binding, problem in pending
                    ]
                else:
                    wrong = (
                        self.review_plan(planned)
                        if self.review_plan is not None
                        and plans_sent_back < PLAN_RETRIES
                        else []
                    )
                    if wrong:
                        # Nothing is shown to the person and nothing happens:
                        # the model is told what is wrong and plans again.
                        plans_sent_back += 1
                        told = (
                            "Not done. Your plan was not shown to the person "
                            "because: "
                            + "; ".join(wrong)
                            + ". Request every change again in one reply, "
                            "with this put right."
                        )
                        pending = [
                            (
                                call,
                                binding,
                                told
                                if binding is not None
                                and problem is None
                                and binding.declaration.access == "execute"
                                else problem,
                            )
                            for call, binding, problem in pending
                        ]
                    else:
                        self.approve_plan(planned)
                        plan_done = True
            if session is not None and pending:
                session.checkpoint(
                    "tool_pending",
                    checkpoint_payload(
                        step=step,
                        response=response,
                        tool_index=resume_tool_index,
                    ),
                )
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
            for offset, (call, binding, problem) in enumerate(pending):
                current_tool_index = resume_tool_index + offset
                try:
                    execution.consume("tool_calls")
                except ExecutionError as exc:
                    raise AgentLoopError(str(exc), code=exc.code) from exc
                try:
                    execution.checkpoint()
                except ExecutionError as exc:
                    raise AgentLoopError(str(exc), code=exc.code) from exc
                changes = (
                    binding is not None and binding.declaration.access == "execute"
                )
                is_execute = changes and not self.defer_approval
                try:
                    failure: Any
                    if problem is not None or binding is None:
                        failure = {"error": problem}
                        result = failure
                    else:
                        try:
                            result = self.execute_tool(
                                binding.declaration, call.arguments
                            )
                            self._validate_output(binding, result)
                        except (ExecutionError, PermissionError, AgentLoopError):
                            raise
                        except Exception as exc:
                            if changes or not self.tell_failures:
                                raise
                            failure = {"error": str(exc)[:300]}
                            result = failure
                    trace_events.append(
                        {
                            "event": "agent_tool_call",
                            "step": step,
                            "id": call.id,
                            "name": call.name,
                            "arguments": call.arguments,
                            "result": result,
                        }
                    )
                except Exception as exc:
                    if is_execute:
                        assert self.approval_ledger is not None
                        assert action_to_claim is not None
                        try:
                            self.approval_ledger.finish(action_to_claim[0], error=exc)
                        except ApprovalLedgerError as ledger_exc:
                            raise AgentLoopError(
                                "Tool failed and approval outcome could not be recorded: "
                                f"{ledger_exc}"
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
                if session is not None:
                    session.checkpoint(
                        "tool_result",
                        checkpoint_payload(
                            step=step,
                            response=response,
                            tool_index=current_tool_index + 1,
                        ),
                    )
            if session is not None:
                session.checkpoint(
                    "ready",
                    checkpoint_payload(step=step),
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
