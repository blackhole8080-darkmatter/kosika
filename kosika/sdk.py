"""Build a Kosika program from Python.

The SDK does not define a second language. Every builder call records a
declaration, ``to_source()`` renders those declarations as ordinary ``.kos``
text, and ``build()`` runs that text through the same parser and checker as a
hand-written file. A program built here is therefore exactly as checked, as
bounded, and as replayable as one written by hand, and it can be saved as a
``.kos`` file at any time.

The SDK grants nothing. ``run()`` takes the same explicit model and tool
bindings as ``kosika.runtime.run``.

    from kosika.sdk import App, ref

    app = App()
    question = app.record("Question", text="Text")
    answer = app.record("Answer", text="Text")
    assistant = app.model("assistant", input=question, output=answer)
    main = app.workflow("main", output=answer, question=question)
    main.use_model("respond", assistant, text=ref("question.text"))

    program = app.build()
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .ast import Program
from .checker import check
from .parser import parse
from .runtime import run as _run

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_ACCESS = ("observe", "analyze", "prepare", "execute")
_MODEL_PARAMETERS = (
    "temperature",
    "max_output_tokens",
    "top_p",
    "frequency_penalty",
    "presence_penalty",
    "stop",
)
_AGENT_LIMITS = ("max_steps", "max_tool_calls", "max_model_calls", "deadline_seconds")


class SdkError(ValueError):
    """A builder call cannot be turned into Kosika source."""


@dataclass(frozen=True)
class Ref:
    """A Kosika expression used as-is, such as ``question.text``."""

    expression: str


def ref(expression: str) -> Ref:
    """Refer to a workflow parameter, an earlier step result, or any expression."""

    if not isinstance(expression, str) or not expression.strip():
        raise SdkError("ref() needs a non-empty Kosika expression")
    if "\n" in expression or "\r" in expression:
        raise SdkError("ref() takes a single-line Kosika expression")
    return Ref(expression.strip())


@dataclass(frozen=True)
class Declared:
    """A handle to something already declared on an :class:`App`."""

    kind: str
    name: str

    def __str__(self) -> str:
        return self.name


def _identifier(value: str, what: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.match(value):
        raise SdkError(f"{what} must be a Kosika identifier, got {value!r}")
    return value


def _name(value: Declared | str, kind: str) -> str:
    if isinstance(value, Declared):
        if value.kind != kind:
            raise SdkError(f"Expected a {kind}, got {value.kind} {value.name!r}")
        return value.name
    return _identifier(value, f"A {kind} name")


def _type_name(value: Declared | str) -> str:
    if isinstance(value, Declared):
        if value.kind != "record":
            raise SdkError(f"A type must be a record, got {value.kind} {value.name!r}")
        return value.name
    if not isinstance(value, str) or not value.strip():
        raise SdkError(f"A type must be a record or a type name, got {value!r}")
    if "\n" in value or "\r" in value:
        raise SdkError("A type name is a single line")
    return value.strip()


def _literal(value: Any) -> str:
    """Render a Python value as a Kosika expression."""

    if isinstance(value, Ref):
        return value.expression
    if isinstance(value, Declared):
        raise SdkError(
            f"{value.kind} {value.name!r} is a declaration, not a value; "
            "use ref(...) for a workflow parameter or step result"
        )
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise SdkError("Kosika numbers must be finite")
        text = repr(abs(value))
        if "e" in text.lower():
            text = format(abs(value), "f")
        return f"-{text}" if value < 0 else text
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_literal(item) for item in value) + "]"
    if isinstance(value, Mapping):
        fields = ", ".join(
            f"{_identifier(key, 'A record field')}: {_literal(item)}"
            for key, item in value.items()
        )
        return "{" + fields + "}"
    raise SdkError(f"Cannot write {type(value).__name__} as a Kosika value")


def _arguments(arguments: Mapping[str, Any]) -> str:
    return ", ".join(
        f"{_identifier(key, 'An argument name')}: {_literal(value)}"
        for key, value in arguments.items()
    )


class Workflow:
    """Sequential steps. Each step method returns a :class:`Ref` to its result.

    Keyword arguments are the call's named arguments. ``bind`` is reserved: it
    names the step's result, which otherwise takes the step's name.
    """

    def __init__(
        self,
        app: App,
        name: str,
        output: str,
        parameters: dict[str, str],
        require_approval: bool,
    ) -> None:
        self._app = app
        self.name = name
        self.output = output
        self.parameters = parameters
        self.require_approval = require_approval
        self._steps: list[tuple[str, str, str | None, str]] = []
        self._bound: set[str] = set(parameters)

    def _add(
        self, step: str, call: str, bind: str | None, type_name: str | None
    ) -> Ref:
        _identifier(step, "A step name")
        if any(existing == step for existing, *_ in self._steps):
            raise SdkError(f"Workflow {self.name!r} already has a step {step!r}")
        binding = _identifier(bind or step, "A step result name")
        if binding in self._bound:
            raise SdkError(
                f"Workflow {self.name!r} already binds {binding!r}; pass bind= "
                "to name this step's result"
            )
        self._bound.add(binding)
        self._steps.append((step, binding, type_name, call))
        return Ref(binding)

    def use_model(
        self,
        step: str,
        model: Declared | str,
        /,
        *,
        bind: str | None = None,
        **arguments: Any,
    ) -> Ref:
        """Ask a declared model once and bind its typed output."""

        name = _name(model, "model")
        return self._add(
            step,
            f"use_model {name}({_arguments(arguments)})",
            bind,
            self._app._model_outputs.get(name),
        )

    def ask(
        self,
        step: str,
        agent: Declared | str,
        /,
        *,
        bind: str | None = None,
        **arguments: Any,
    ) -> Ref:
        """Run a declared agent's bounded loop and bind its typed answer."""

        name = _name(agent, "agent")
        return self._add(
            step,
            f"ask {name}({_arguments(arguments)})",
            bind,
            self._app._agent_outputs.get(name),
        )

    def execute(
        self,
        step: str,
        agent: Declared | str,
        tool: Declared | str,
        /,
        *,
        bind: str | None = None,
        **arguments: Any,
    ) -> Ref:
        """Call one tool directly on behalf of an agent allowed to use it."""

        tool_name = _name(tool, "tool")
        return self._add(
            step,
            f"execute {_name(agent, 'agent')}.{tool_name}({_arguments(arguments)})",
            bind,
            self._app._tool_outputs.get(tool_name),
        )

    def call(
        self,
        step: str,
        workflow: Workflow | Declared | str,
        /,
        *,
        bind: str | None = None,
        **arguments: Any,
    ) -> Ref:
        """Call another workflow and bind its result."""

        name = (
            workflow.name
            if isinstance(workflow, Workflow)
            else _name(workflow, "workflow")
        )
        return self._add(
            step,
            f"call {name}({_arguments(arguments)})",
            bind,
            self._app._workflow_outputs.get(name),
        )

    def function(
        self,
        step: str,
        function: str,
        /,
        *,
        returns: Declared | str | None = None,
        bind: str | None = None,
        **arguments: Any,
    ) -> Ref:
        """Call a pure function declared with :meth:`App.source`."""

        return self._add(
            step,
            f"{_identifier(function, 'A function name')}({_arguments(arguments)})",
            bind,
            _type_name(returns) if returns is not None else None,
        )

    def _render(self) -> str:
        if not self._steps:
            raise SdkError(f"Workflow {self.name!r} has no steps")
        parameters = ", ".join(
            f"{name}: {type_name}" for name, type_name in self.parameters.items()
        )
        lines = [f"workflow {self.name}({parameters}) -> {self.output} {{"]
        for step, binding, type_name, call in self._steps:
            annotation = f": {type_name}" if type_name else ""
            lines += [
                f"  step {step} {{",
                f"    let {binding}{annotation} = {call}",
                f"    return {binding}",
                "  }",
            ]
        if self.require_approval:
            lines.append("  require approval")
        lines.append("}")
        return "\n".join(lines)


class App:
    """Collects declarations and renders, checks, and runs them."""

    def __init__(self) -> None:
        self._blocks: list[str | Workflow] = []
        self._names: dict[str, set[str]] = {}
        self._model_outputs: dict[str, str] = {}
        self._agent_outputs: dict[str, str] = {}
        self._tool_outputs: dict[str, str] = {}
        self._workflow_outputs: dict[str, str] = {}

    def _declare(self, kind: str, name: str) -> Declared:
        _identifier(name, f"A {kind} name")
        names = self._names.setdefault(kind, set())
        if name in names:
            raise SdkError(f"{kind.capitalize()} {name!r} is already declared")
        names.add(name)
        return Declared(kind, name)

    def record(self, name: str, /, **fields: Declared | str) -> Declared:
        """Declare a closed record. Field types are records or type names."""

        handle = self._declare("record", name)
        lines = [f"record {name} {{"]
        lines += [
            f"  {_identifier(field, 'A record field')}: {_type_name(type_name)}"
            for field, type_name in fields.items()
        ]
        self._blocks.append("\n".join([*lines, "}"]))
        return handle

    def model(
        self,
        name: str,
        *,
        input: Declared | str,
        output: Declared | str,
        deployment: str | None = None,
        **parameters: Any,
    ) -> Declared:
        """Declare a typed model call. This names no provider and grants none."""

        handle = self._declare("model", name)
        unknown = sorted(set(parameters) - set(_MODEL_PARAMETERS))
        if unknown:
            raise SdkError(f"Unknown model parameter(s): {', '.join(unknown)}")
        output_type = _type_name(output)
        lines = [
            f"model {name} {{",
            f"  input: {_type_name(input)}",
            f"  output: {output_type}",
        ]
        if deployment is not None:
            lines.append(f"  deployment: {_literal(str(deployment))}")
        if parameters:
            lines.append("  parameters: {")
            lines += [
                f"    {key}: {_literal(parameters[key])}"
                for key in _MODEL_PARAMETERS
                if key in parameters
            ]
            lines.append("  }")
        self._blocks.append("\n".join([*lines, "}"]))
        self._model_outputs[name] = output_type
        return handle

    def tool(
        self,
        name: str,
        *,
        source: str,
        output: Declared | str,
        operation: str | None = None,
        access: str = "observe",
        input: Declared | str | None = None,
    ) -> Declared:
        """Declare a tool. The host still has to bind it before it can run."""

        handle = self._declare("tool", name)
        if access not in _ACCESS:
            raise SdkError(f"Tool access must be one of {', '.join(_ACCESS)}")
        if not isinstance(source, str) or not source:
            raise SdkError("A tool needs a source name")
        output_type = _type_name(output)
        lines = [
            f"tool {name} {{",
            f"  source mcp {_literal(source)}",
            f"  operation: {_literal(operation or name)}",
            f"  access: {access}",
        ]
        if input is not None:
            lines.append(f"  input: {_type_name(input)}")
        lines.append(f"  output: {output_type}")
        self._blocks.append("\n".join([*lines, "}"]))
        self._tool_outputs[name] = output_type
        return handle

    def agent(
        self,
        name: str,
        *,
        model: Declared | str | None = None,
        instructions: str | None = None,
        can: tuple[Declared | str, ...] | list[Declared | str] = (),
        cannot: tuple[str, ...] | list[str] = (),
        **limits: int | float,
    ) -> Declared:
        """Declare an agent: its model, the tools it may use, and its limits."""

        handle = self._declare("agent", name)
        unknown = sorted(set(limits) - set(_AGENT_LIMITS))
        if unknown:
            raise SdkError(f"Unknown agent limit(s): {', '.join(unknown)}")
        lines = [f"agent {name} {{"]
        if model is not None:
            model_name = _name(model, "model")
            lines.append(f"  model: {model_name}")
            if model_name in self._model_outputs:
                self._agent_outputs[name] = self._model_outputs[model_name]
        if instructions is not None:
            lines.append(f"  instructions: {_literal(str(instructions))}")
        if can:
            lines.append(f"  can: [{', '.join(_name(item, 'tool') for item in can)}]")
        if cannot:
            denied = ", ".join(
                _identifier(item, "A denied capability") for item in cannot
            )
            lines.append(f"  cannot: [{denied}]")
        lines += [
            f"  {key}: {_literal(limits[key])}"
            for key in _AGENT_LIMITS
            if key in limits
        ]
        self._blocks.append("\n".join([*lines, "}"]))
        return handle

    def workflow(
        self,
        name: str,
        /,
        *,
        output: Declared | str,
        require_approval: bool = False,
        **parameters: Declared | str,
    ) -> Workflow:
        """Start a workflow. Keyword arguments are its typed parameters."""

        self._declare("workflow", name)
        workflow = Workflow(
            self,
            name,
            _type_name(output),
            {
                _identifier(key, "A workflow parameter"): _type_name(value)
                for key, value in parameters.items()
            },
            require_approval,
        )
        self._blocks.append(workflow)
        self._workflow_outputs[name] = workflow.output
        return workflow

    def swarm(
        self,
        name: str,
        *,
        agents: tuple[Declared | str, ...] | list[Declared | str],
        workflow: Workflow | Declared | str,
        max_rounds: int = 1,
    ) -> Declared:
        """Declare a swarm. It always requires approval; see ``kosika.swarm``."""

        handle = self._declare("swarm", name)
        workflow_name = (
            workflow.name
            if isinstance(workflow, Workflow)
            else _name(workflow, "workflow")
        )
        members = ", ".join(_name(item, "agent") for item in agents)
        self._blocks.append(
            "\n".join(
                [
                    f"swarm {name} {{",
                    f"  agents: [{members}]",
                    f"  workflow: {workflow_name}",
                    f"  max_rounds: {_literal(max_rounds)}",
                    "  require approval",
                    "}",
                ]
            )
        )
        return handle

    def source(self, text: str) -> None:
        """Add hand-written Kosika, such as pure ``fn`` declarations."""

        if not isinstance(text, str) or not text.strip():
            raise SdkError("source() needs Kosika text")
        self._blocks.append(text.strip())

    def to_source(self) -> str:
        """Render every declaration as ``.kos`` text."""

        blocks = [
            block._render() if isinstance(block, Workflow) else block
            for block in self._blocks
        ]
        return "\n\n".join(blocks) + "\n"

    def build(self) -> Program:
        """Parse and check the program. Raises the same errors as a ``.kos`` file."""

        program = parse(self.to_source())
        check(program)
        return program

    def save(self, path: str | Path) -> Path:
        """Check the program, then write it as a ``.kos`` file."""

        self.build()
        target = Path(path)
        if target.suffix != ".kos":
            raise SdkError("A Kosika source file must end in .kos")
        target.write_text(self.to_source(), encoding="utf-8")
        return target

    def run(
        self,
        workflow: Workflow | str = "main",
        *,
        inputs: dict[str, Any] | None = None,
        trace: str | Path | None = None,
        **host: Any,
    ) -> dict[str, Any]:
        """Check and run a workflow. ``host`` is passed to ``kosika.runtime.run``."""

        name = workflow.name if isinstance(workflow, Workflow) else workflow
        return _run(self.build(), name, trace, inputs=inputs or {}, **host)
