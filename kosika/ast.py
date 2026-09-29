from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BreakExpr:
    """Exit the innermost enclosing ``for`` or ``repeat`` loop."""


@dataclass(frozen=True)
class ContinueExpr:
    """Skip to the next iteration of the innermost enclosing ``for`` or ``repeat``."""


@dataclass(frozen=True)
class RepeatExpr:
    """Bounded loop: ``repeat <count> times { <body> }``.

    Evaluates ``<body>`` up to ``<count>`` times and returns the last
    result. ``break`` and ``continue`` are valid inside the body.
    """
    count: Expression
    body: Expression


@dataclass(frozen=True)
class TryExpr:
    """Fallible block: ``try { <body> } retry <n>``.

    If ``<body>`` raises a runtime error, it is retried up to ``<n>`` times.
    Without retry, a single failure is still caught and re-raised as a
    TryError so callers can match on it.
    """
    body: Expression
    retry: int = 0


@dataclass(frozen=True)
class ModelVarExpr:
    """Reference to model: metadata of the most recent model call in the
    current step. Valid only inside step bodies."""


@dataclass(frozen=True)
class ToolVarExpr:
    """Reference to ``tool`` — metadata of the most recent tool call in the
    current step. Valid only inside step bodies."""


@dataclass(frozen=True)
class FunctionExpr:
    """An anonymous function literal used as a first-class value.

    ``fn(<params>) [ -> <output_type>] { <body> }`` evaluates to a closure
    that captures the current environment. The output type is optional; when
    omitted it is inferred from the body the same way as a named function.
    """
    parameters: tuple[Field, ...]
    output_type: str | None
    body: Expression


@dataclass(frozen=True)
class MethodCallExpr:
    """A method call on a Text or array value: ``<target>.<method>(<args>)``.

    Methods are dispatched by the target type and the method name at both
    check and runtime. Records keep using ``FieldExpr`` for field access.
    """
    target: Expression
    method: str
    arguments: tuple[tuple[str, Expression], ...]


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
class SwarmDecl:
    """A named group of agents that work one shared workflow.

    A swarm is a declaration of cooperation, not extra authority: it names
    agents that already exist, the workflow they run together, and the number
    of review rounds allowed. ``approval_required`` is always true for a
    declared swarm; the parser refuses a swarm that does not state
    ``require approval``. Human approval is a property of the declaration, so
    it cannot be removed by a model, a tool, or a later configuration change.
    """

    name: str
    agents: tuple[str, ...]
    workflow: str
    max_rounds: int = 1
    approval_required: bool = True


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


@dataclass(frozen=True)
class FunctionCallExpr:
    function_name: str
    arguments: tuple[tuple[str, "Expression"], ...] = ()


@dataclass(frozen=True)
class FunctionRefExpr:
    function_name: str
    captures: tuple[tuple[str, "Expression"], ...] = ()


@dataclass(frozen=True)
class UnaryExpr:
    operator: str
    operand: "Expression"


@dataclass(frozen=True)
class BinaryExpr:
    left: "Expression"
    operator: str
    right: "Expression"


@dataclass(frozen=True)
class ConditionalExpr:
    condition: "Expression"
    when_true: "Expression"
    when_false: "Expression"


@dataclass(frozen=True)
class MatchArm:
    pattern: LiteralExpr | None
    value: "Expression"


@dataclass(frozen=True)
class MatchExpr:
    subject: "Expression"
    arms: tuple[MatchArm, ...]


@dataclass(frozen=True)
class ForExpr:
    """Bounded iteration: ``for <item_name> in <sequence> { <body> }``.

    Evaluates ``sequence`` (which must resolve to an array type ``T[]``),
    then evaluates ``body`` once for each element with ``item_name`` bound
    in scope, collecting the results into a new array (functional map).
    ``limit`` caps the number of iterations (1–512); it is a compile-time
    constant, not a runtime variable.
    """

    item_name: str
    sequence: "Expression"
    body: "Expression"
    limit: int = 128


Expression = (
    LiteralExpr
    | VariableExpr
    | FieldExpr
    | ArrayExpr
    | RecordExpr
    | IndexExpr
    | FunctionCallExpr
    | FunctionRefExpr
    | FunctionExpr
    | MethodCallExpr
    | UnaryExpr
    | BinaryExpr
    | ConditionalExpr
    | MatchExpr
    | ForExpr
)


def expression_function_calls(expression: Expression) -> tuple[FunctionCallExpr | FunctionRefExpr | FunctionExpr, ...]:
    calls: list[FunctionCallExpr | FunctionRefExpr | FunctionExpr] = []

    def visit(value: Expression) -> None:
        if isinstance(value, FunctionCallExpr):
            calls.append(value)
            for _name, argument in value.arguments:
                visit(argument)
        elif isinstance(value, FunctionRefExpr):
            calls.append(value)
            for _, argument in value.captures:
                visit(argument)
        elif isinstance(value, FunctionExpr):
            calls.append(value)
            visit(value.body)
        elif isinstance(value, FieldExpr):
            visit(value.base)
        elif isinstance(value, IndexExpr):
            visit(value.base)
            visit(value.index)
        elif isinstance(value, MethodCallExpr):
            visit(value.target)
            for _name, argument in value.arguments:
                visit(argument)
        elif isinstance(value, ArrayExpr):
            for item in value.items:
                visit(item)
        elif isinstance(value, RecordExpr):
            for _name, item in value.fields:
                visit(item)
        elif isinstance(value, UnaryExpr):
            visit(value.operand)
        elif isinstance(value, BinaryExpr):
            visit(value.left)
            visit(value.right)
        elif isinstance(value, ConditionalExpr):
            visit(value.condition)
            visit(value.when_true)
            visit(value.when_false)
        elif isinstance(value, MatchExpr):
            visit(value.subject)
            for arm in value.arms:
                visit(arm.value)
        elif isinstance(value, ForExpr):
            visit(value.sequence)
            visit(value.body)

    visit(expression)
    return tuple(calls)


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
class FunctionCall:
    function_name: str
    arguments: tuple[tuple[str, Expression], ...] = ()


@dataclass(frozen=True)
class Step:
    name: str
    call: ToolCall | ModelCall | AgentCall | WorkflowCall | FunctionCall
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


@dataclass(frozen=True)
class LocalBinding:
    name: str
    type_name: str
    value: Expression


@dataclass(frozen=True)
class FunctionDecl:
    name: str
    parameters: tuple[Field, ...]
    output_type: str
    return_value: Expression
    bindings: tuple[LocalBinding, ...] = ()
    nested: tuple[FunctionDecl, ...] = ()
    recursion_limit: int | None = None


def function_calls(function: FunctionDecl) -> tuple[FunctionCallExpr | FunctionRefExpr, ...]:
    expressions = (*[binding.value for binding in function.bindings], function.return_value)
    return tuple(call for value in expressions for call in expression_function_calls(value))


def static_function_references(function: FunctionDecl) -> tuple[FunctionCallExpr | FunctionRefExpr, ...]:
    """Resolve local call shadowing in declaration order, never for explicit refs."""
    bound = {parameter.name for parameter in function.parameters}
    references: list[FunctionCallExpr | FunctionRefExpr] = []
    for name, expression in (
        *((binding.name, binding.value) for binding in function.bindings),
        ('', function.return_value),
    ):
        references.extend(
            call for call in expression_function_calls(expression)
            if isinstance(call, (FunctionRefExpr, FunctionCallExpr))
            and (isinstance(call, FunctionRefExpr) or call.function_name not in bound)
        )
        for call in expression_function_calls(expression):
            if isinstance(call, FunctionExpr):
                for ref in static_function_references(
                    FunctionDecl('_', call.parameters, call.output_type or 'Null', call.body, (), (), None)
                ):
                    if isinstance(ref, FunctionRefExpr) or ref.function_name not in bound:
                        references.append(ref)
        bound.add(name)
    return tuple(references)


@dataclass
class Program:
    records: list[RecordDecl] = field(default_factory=list)
    tools: list[ToolDecl] = field(default_factory=list)
    agents: list[AgentDecl] = field(default_factory=list)
    swarms: list[SwarmDecl] = field(default_factory=list)
    workflows: list[WorkflowDecl] = field(default_factory=list)
    functions: list[FunctionDecl] = field(default_factory=list)
    models: list[ModelDecl] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    exports: list[str] = field(default_factory=list)
    module_hashes: dict[str, str] = field(default_factory=dict)
    module_declarations: dict[str, tuple[str, ...]] = field(default_factory=dict)
    module_imports: dict[str, tuple[str, ...]] = field(default_factory=dict)
