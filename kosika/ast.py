from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Field:
    name: str
    type_name: str


@dataclass(frozen=True)
class RecordDecl:
    name: str
    fields: tuple[Field, ...]


@dataclass(frozen=True)
class ToolDecl:
    name: str
    source: str
    operation: str
    access: str
    output: str
    input: str | None = None


@dataclass(frozen=True)
class AgentDecl:
    name: str
    can: tuple[str, ...]
    cannot: tuple[str, ...]
    model: str | None = None
    max_steps: int = 8


@dataclass(frozen=True)
class LiteralExpr:
    value: object


@dataclass(frozen=True)
class VariableExpr:
    name: str


@dataclass(frozen=True)
class FieldExpr:
    base: "Expression"
    field_name: str


@dataclass(frozen=True)
class ArrayExpr:
    items: tuple["Expression", ...]


@dataclass(frozen=True)
class RecordExpr:
    fields: tuple[tuple[str, "Expression"], ...]


@dataclass(frozen=True)
class IndexExpr:
    base: "Expression"
    index: "Expression"


Expression = LiteralExpr | VariableExpr | FieldExpr | ArrayExpr | RecordExpr | IndexExpr


@dataclass(frozen=True)
class ToolCall:
    agent_name: str
    tool_name: str
    arguments: tuple[tuple[str, Expression], ...] = ()


@dataclass(frozen=True)
class ModelDecl:
    name: str
    input: str
    output: str


@dataclass(frozen=True)
class ModelCall:
    model_name: str
    arguments: tuple[tuple[str, Expression], ...] = ()


@dataclass(frozen=True)
class AgentCall:
    agent_name: str
    arguments: tuple[tuple[str, Expression], ...] = ()


@dataclass(frozen=True)
class WorkflowCall:
    workflow_name: str
    arguments: tuple[tuple[str, Expression], ...] = ()


@dataclass(frozen=True)
class Step:
    name: str
    call: ToolCall | ModelCall | AgentCall | WorkflowCall
    binding_name: str = "result"
    binding_type: str | None = None
    return_name: str = "result"


@dataclass(frozen=True)
class WorkflowDecl:
    name: str
    output_type: str
    steps: tuple[Step, ...]
    approval_required: bool = False
    parameters: tuple[Field, ...] = ()


@dataclass
class Program:
    records: list[RecordDecl] = field(default_factory=list)
    tools: list[ToolDecl] = field(default_factory=list)
    agents: list[AgentDecl] = field(default_factory=list)
    workflows: list[WorkflowDecl] = field(default_factory=list)
    models: list[ModelDecl] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    exports: list[str] = field(default_factory=list)
    module_hashes: dict[str, str] = field(default_factory=dict)
