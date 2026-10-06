from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class BreakExpr:
    """Exit the innermost enclosing ``for`` or ``repeat`` loop."""


@dataclass(frozen=True)
class ReturnExpr:
    """Leave the enclosing function early with ``value`` as its result.

    Written `return value` inside an `if`, `match` arm, or loop body. The
    function's last line is still its ordinary `return`, which is stored in
    ``FunctionDecl.return_value`` and is not one of these.
    """

    value: "Expression"


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
    max_tool_calls: int = 256
    max_model_calls: int = 64
    instructions: str | None = None
    deadline_seconds: float | None = None


@dataclass(frozen=True)
class SwarmDecl:
    """A named group of agents declared to work one shared workflow.

    A swarm is a declaration of cooperation, not extra authority: it names
    agents that already exist, the workflow they run together, and the number
    of review rounds allowed. ``approval_required`` is always true for a
    declared swarm; the parser refuses a swarm that does not state
    ``require approval``. Human approval is a property of the declaration, so
    it cannot be removed by a model, a tool, or a later configuration change.

    A declared swarm is not a workflow entry point: ``run`` does not accept one.
    It runs only through ``kosika.swarm.run_swarm`` (``kosika swarm`` on the
    command line), under one approval bound to the swarm's plan hash. The
    declaration itself grants nothing and runs nothing.
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
    # Optional `where <condition>`: elements for which it is false are skipped,
    # so the loop filters as well as maps. None for every loop without one.
    condition: "Expression | None" = None
    # True when the loop was written with `limit N`. Only then is `limit`
    # enforced; a loop without one is bounded by the run's loop budget.
    capped: bool = False


@dataclass(frozen=True)
class Block:
    """A braced scope holding statements and producing a final value.

    ``result`` is None for a statement-only body, which evaluates to Null. Only
    produced when at least one statement is present, so a body written before
    blocks existed keeps the identical AST.
    """

    statements: tuple[LocalBinding | Assignment | Expression, ...]
    result: Expression | None = None


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
    | RepeatExpr
    | TryExpr
    | BreakExpr
    | ReturnExpr
    | ContinueExpr
    | ModelVarExpr
    | Block
)


def expression_function_calls(
    expression: Expression | Assignment,
) -> tuple[FunctionCallExpr | FunctionRefExpr | FunctionExpr, ...]:
    calls: list[FunctionCallExpr | FunctionRefExpr | FunctionExpr] = []

    def visit(value: Expression | Assignment) -> None:
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
        elif isinstance(value, ReturnExpr):
            visit(value.value)
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
            if value.condition is not None:
                visit(value.condition)
            visit(value.body)
        elif isinstance(value, RepeatExpr):
            visit(value.count)
            visit(value.body)
        elif isinstance(value, TryExpr):
            visit(value.body)
        elif isinstance(value, Assignment):
            visit(value.value)
        elif isinstance(value, Block):
            for statement in value.statements:
                if isinstance(statement, LocalBinding):
                    visit(statement.value)
                else:
                    visit(statement)
            if value.result is not None:
                visit(value.result)

    visit(expression)
    return tuple(calls)


@dataclass(frozen=True)
class ToolCall:
    agent_name: str
    tool_name: str
    arguments: tuple[tuple[str, Expression], ...] = ()


@dataclass(frozen=True)
class ModelParameters:
    temperature: float | None = None
    max_output_tokens: int | None = None
    top_p: float | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    stop: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelDecl:
    name: str
    input: str
    output: str
    deployment: str | None = None
    parameters: ModelParameters = ModelParameters()
    # Set only on the models generated for thinking steps (`classify`,
    # `extract`, `summarize`, `decide`). `provider` names the declared model
    # whose host binding, deployment, and parameters the step uses.
    provider: str | None = None
    instruction: str | None = None
    verb: str | None = None
    # The evidence rule of a step written `with evidence`; see
    # `kosika.evidence`. None for every other model.
    evidence: str | None = None
    # `default model NAME`: the model a thinking step uses when it names none
    # and the program declares several.
    default: bool = False


@dataclass(frozen=True)
class ThinkCall:
    """A thinking step as written, before it is lowered to a model call.

    ``classify <subject> into <extra>``, ``extract <record> from <subject>``,
    ``summarize <subject>``, and ``decide <subject> given <extra>``, each with
    an optional ``using <model>``. `kosika.thinking.lower_thinking` replaces
    every one with an ordinary :class:`ModelCall`, so nothing after parsing
    sees this node.
    """

    verb: str
    subject: "Expression"
    extra: "Expression | None" = None
    record: str | None = None
    model: str | None = None
    # `with evidence`: the answer must quote the text it was given.
    evidence: bool = False
    # `using a, b`: further models that take turns answering a `vote`.
    more_models: tuple[str, ...] = ()


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
    # Between parsing and `lower_thinking` a step may briefly hold a ThinkCall;
    # every program handed to the checker or runtime holds only these five.
    call: ToolCall | ModelCall | AgentCall | WorkflowCall | FunctionCall
    binding_name: str = "result"
    binding_type: str | None = None
    return_name: str = "result"
    # `step = call otherwise fallback`: the call made instead when the first
    # one fails in a recoverable way. None for every step without one.
    fallback: ToolCall | ModelCall | AgentCall | WorkflowCall | FunctionCall | None = (
        None
    )
    # `step = call retry 2`: how many more times a model is asked when its
    # answer is rejected, each time shown the answer and why it failed.
    retries: int = 0
    # `step = call vote 3`: how many times the model is asked. The step
    # succeeds only when more than half of the answers agree.
    votes: int = 0
    # The models that take turns answering a `vote`, when a thinking step
    # names several with `using a, b`. Empty when one model answers them all.
    voters: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowDecl:
    name: str
    output_type: str
    steps: tuple[Step, ...]
    approval_required: bool = False
    parameters: tuple[Field, ...] = ()
    # `budget 6 model calls, 30 seconds`: ceilings this workflow puts on its
    # own run, as (kind, amount) pairs with kind one of "model_calls",
    # "tool_calls", or "seconds". A budget only ever tightens the limits of
    # whoever runs the program.
    budget: tuple[tuple[str, int | float], ...] = ()


@dataclass(frozen=True)
class Assignment:
    """Write to an enclosing `var`. The checker guarantees the target exists and
    is mutable; the AST does not repeat that."""

    target: str
    value: Expression


@dataclass(frozen=True)
class LocalBinding:
    name: str
    # None means "infer from the initialiser". The checker resolves it; the
    # runtime then has no declared type to validate against.
    type_name: str | None
    value: Expression
    mutable: bool = False


# A function body statement is an assignment or a value-discarding expression.
# Declarations are split out into FunctionDecl.bindings by the parser, so they
# never appear here.
Statement = Assignment | Expression


@dataclass(frozen=True)
class FunctionDecl:
    name: str
    parameters: tuple[Field, ...]
    output_type: str
    return_value: Expression
    bindings: tuple[LocalBinding, ...] = ()
    nested: tuple[FunctionDecl, ...] = ()
    recursion_limit: int | None = None
    # Value-discarding statements: loops, conditionals, and assignments.
    # Empty for every program written before statements existed.
    statements: tuple[Statement, ...] = ()
    # Source order of the body, as ("binding" | "statement", index) pairs. The
    # parser records it only when declarations and statements are interleaved;
    # empty means every binding precedes every statement, which is also the
    # order of every program written before interleaving was honoured.
    order: tuple[tuple[str, int], ...] = ()


def function_body(function: FunctionDecl) -> tuple[LocalBinding | Statement, ...]:
    """The body's declarations and statements in the order they execute."""

    if not function.order:
        return (*function.bindings, *function.statements)
    return tuple(
        function.bindings[index] if kind == "binding" else function.statements[index]
        for kind, index in function.order
    )


def function_calls(
    function: FunctionDecl,
) -> tuple[FunctionCallExpr | FunctionRefExpr | FunctionExpr, ...]:
    expressions = (
        *[binding.value for binding in function.bindings],
        *function.statements,
        function.return_value,
    )
    return tuple(
        call for value in expressions for call in expression_function_calls(value)
    )


def static_function_references(
    function: FunctionDecl,
) -> tuple[FunctionCallExpr | FunctionRefExpr, ...]:
    """Resolve local call shadowing in declaration order, never for explicit refs."""
    bound = {parameter.name for parameter in function.parameters}
    references: list[FunctionCallExpr | FunctionRefExpr] = []
    for name, expression in (
        *(
            (item.name, item.value) if isinstance(item, LocalBinding) else ("", item)
            for item in function_body(function)
        ),
        ("", function.return_value),
    ):
        references.extend(
            call
            for call in expression_function_calls(expression)
            if isinstance(call, (FunctionRefExpr, FunctionCallExpr))
            and (isinstance(call, FunctionRefExpr) or call.function_name not in bound)
        )
        for call in expression_function_calls(expression):
            if isinstance(call, FunctionExpr):
                for ref in static_function_references(
                    FunctionDecl(
                        "_",
                        call.parameters,
                        call.output_type or "Null",
                        call.body,
                        (),
                        (),
                        None,
                    )
                ):
                    if (
                        isinstance(ref, FunctionRefExpr)
                        or ref.function_name not in bound
                    ):
                        references.append(ref)
        bound.add(name)
    return tuple(references)


@dataclass(frozen=True)
class MemoryDecl:
    """`memory NAME of Record`; see `kosika.memory`."""

    name: str
    record: str


@dataclass(frozen=True)
class TestStep:
    """One line of a test: `name = expression` or `expect expression`."""

    kind: str  # "let" or "expect"
    value: Expression
    name: str = ""
    line: int = 0
    # The expectation as written, for the failure message.
    text: str = ""
    # `name: Type = expression`, needed for a record or empty-list literal.
    type_name: str | None = None


@dataclass(frozen=True)
class TestDecl:
    """`test "name" { ... }`; see `kosika.testing`."""

    name: str
    # `answers MODEL: [...]` lines: the scripted answers of a declared model.
    answers: tuple[tuple[str, Expression], ...] = ()
    steps: tuple[TestStep, ...] = ()
    line: int = 0


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
    # Tests are kept beside the program but are not part of what runs, so
    # `program_payload` leaves them out of every hash.
    tests: list[TestDecl] = field(default_factory=list)
    # `memory NAME of Record`. `kosika.memory.lower_memory` adds the tools
    # and records each one stands for; this list says which memories exist.
    memories: list[MemoryDecl] = field(default_factory=list)


#: Fields added to the tree after traces and approvals were first recorded,
#: with the value each has in a program that does not use the newer feature.
#: `program_payload` leaves these out, so such a program hashes exactly as it
#: did before the field existed.
_ABSENT_DEFAULTS: dict[str, tuple[Any, ...]] = {
    "order": ((), []),
    "condition": (None,),
    "provider": (None,),
    "instruction": (None,),
    "verb": (None,),
    "fallback": (None,),
    "evidence": (None, False),
    "retries": (0,),
    "votes": (0,),
    "voters": ((), []),
    "default": (False,),
    "more_models": ((), []),
    "budget": ((), []),
    "capped": (False,),
    "memories": ((), []),
}


def program_payload(program: Program) -> dict[str, Any]:
    """The JSON-ready form of a program that every program hash is taken over."""

    def prune(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: prune(item)
                for key, item in value.items()
                if not (
                    key in _ABSENT_DEFAULTS
                    and any(
                        type(item) is type(default) and item == default
                        for default in _ABSENT_DEFAULTS[key]
                    )
                )
            }
        if isinstance(value, (list, tuple)):
            return [prune(item) for item in value]
        return value

    payload = prune(asdict(program))
    payload.pop("tests", None)
    return payload
