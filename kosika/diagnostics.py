"""Structured, machine-readable diagnostics for Kosika source.

Human-readable error strings are the wrong interface for a tool that has to
*react* to a failure rather than merely show it. A coding assistant cannot tell
whether ``expects 'DiskStatus', got 'Text'`` should be fixed by widening the
annotation, casting the value, or changing the callee -- but it can act on a
stable code plus ``expected``/``actual`` fields.

This module converts the existing ``ValueError``-based failures from the lexer,
parser, and checker into :class:`Diagnostic` values that carry:

* a stable ``code`` that survives message wording changes,
* an LSP-compatible ``span``,
* the owning declaration and step, and
* structured ``data`` such as ``expected`` and ``actual``.

Two deliberate constraints keep this useful rather than merely elaborate:

1. The codes are a closed registry (:data:`CODES`). An unmapped message becomes
   ``kosika/internal-error`` instead of being force-fit into a neighbouring
   category, so a new upstream message can never be silently mislabelled.
2. Spans for checker errors are *anchored*, not invented. The checker reports no
   source positions, so the span is resolved to the declaration or step the
   message names. When no anchor can be resolved the span is ``None`` and the
   client is told so via ``data.anchor_missing`` rather than being handed a
   misleading position.

The lexer, parser, and checker are unchanged; this is an adapter over their
existing public errors.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Mapping, Sequence

from .checker import CheckError, check
from .lexer import Token, lex
from .modules import ModuleLoadContext, load_program
from .parser import ParseError, parse

DIAGNOSTIC_SCHEMA = "kosika.diagnostics/v1"


class Severity(IntEnum):
    """LSP-compatible diagnostic severities."""

    ERROR = 1
    WARNING = 2
    INFORMATION = 3
    HINT = 4


#: The closed set of diagnostic codes. Order is irrelevant; membership is what
#: matters, and :func:`code_for` only ever returns one of these values.
CODES: Mapping[str, str] = {
    # -- Lexical ---------------------------------------------------------
    "lex/unexpected-character": "The source contains a character the lexer cannot start a token with.",
    # -- Syntactic -------------------------------------------------------
    "parse/expected-token": "A required token was not present.",
    "parse/expected-identifier": "A declaration or binding required an identifier.",
    "parse/unnamed-argument": "A call argument needs a name.",
    "check/unnamed-argument": "A call's single argument cannot be named for it.",
    "parse/invalid-type": "A type is written with an invalid suffix.",
    "parse/empty-block": "A braced block declared neither a statement nor a result.",
    "parse/invalid-declaration": "A declaration was malformed.",
    "parse/limit-exceeded": "A declaration exceeded a fixed syntactic nesting limit.",
    "parse/unsupported-module": "The source used a module feature that cannot be resolved here.",
    # -- Declaration structure ------------------------------------------
    "check/duplicate-declaration": "A declaration name is declared more than once.",
    "check/duplicate-field": "A record or declaration lists a field more than once.",
    "check/duplicate-parameter": "A declaration lists a parameter more than once.",
    "check/duplicate-pattern": "A match expression repeats a pattern.",
    "check/missing-field": "A record literal omits required fields.",
    "check/missing-argument": "A call omits required arguments.",
    "check/unknown-argument": "A call supplies an argument that is not declared.",
    "check/unknown-field": "A record or declaration uses a field that is not declared.",
    # -- Names -----------------------------------------------------------
    "check/unknown-type": "A type name is not declared.",
    "check/unknown-record": "A record name is not declared.",
    "check/unknown-function": "A function name is not in scope.",
    "check/unknown-workflow": "A workflow name is not in scope.",
    "check/unknown-model": "An agent references a model that is not declared.",
    "check/return-outside-function": "An early return is used outside a function.",
    "check/return-type": "An early return has the wrong type for its function.",
    "check/otherwise-execute": "A fallback is used around a world-changing tool.",
    "check/otherwise-type": "A step and its fallback return different types.",
    "check/value-step": "A value stands where a step needs a call.",
    "check/try": "A try and its otherwise give different types, or fail was given something that is not Text.",
    "check/method": "A record's own function has a name it cannot have.",
    "check/parallel": "A step in a parallel block cannot run apart from the others.",
    "check/record-update": "A record update does not fit the record it copies.",
    "check/retry": "A step's retry is not allowed or its count is out of range.",
    "check/verify": "A step's verify is not a Text function or is on a step it cannot check.",
    "check/escalate": "A step's escalate does not name another declared model, or is on a step that cannot use one.",
    "check/vote": "A step's vote is not allowed or its count is not permitted.",
    "check/budget": "A workflow's budget repeats a kind or is out of range.",
    "check/test": "A test written in the program is not well formed.",
    "check/memory": "A memory is declared in a way that cannot be honoured.",
    "check/board": "A board is declared or signed in a way that cannot be honoured.",
    "check/step-match": "A step that chooses between calls is not well formed.",
    "check/invalid-system-tool": "A shell or files tool is declared incorrectly.",
    "check/thinking-evidence": "A thinking step cannot be asked for evidence.",
    "check/thinking-model-required": "A thinking step has no single model to ask.",
    "check/thinking-record-clash": "A declared record clashes with a built-in one.",
    "check/thinking-unknown-record": "A thinking step extracts an undeclared record.",
    "check/shadowed-binding": "A local binding shadows an enclosing binding.",
    "check/assignment-to-immutable": "An assignment targets a binding that is not declared mutable.",
    "check/assignment-to-unknown": "An assignment targets a name that is not in scope.",
    "check/unknown-variable": "A local binding is not in scope.",
    # -- Types -----------------------------------------------------------
    "check/type-mismatch": "A value's type does not match the required type.",
    "check/condition-type": "A condition did not produce a Bool.",
    "check/branch-type-mismatch": "Branches of a conditional or match produced different types.",
    "check/match-type-mismatch": "A match pattern or subject type is not comparable.",
    "check/return-type-mismatch": "A function body's type differs from its declared output.",
    "check/array-index-type": "An array index was not a Number.",
    "check/non-array-index": "A value that is not an array was indexed.",
    "check/unknown-operator": "An operator is not defined for the given operand types.",
    "check/operator-type-mismatch": "An operator's operands are of a type it does not accept.",
    "check/inference-failure": "A literal's type could not be inferred from context.",
    "check/unsupported-expression": "An expression form is recognised but not implemented.",
    "check/unknown-method": "A method is not in the built-in set for that type.",
    "check/method-arguments": "A method call has the wrong argument names.",
    "check/exhaustiveness": "A match does not cover every case.",
    "check/unreachable-arm": "A match arm can never be reached.",
    "check/missing-return": "A workflow step did not return its declared binding.",
    "check/shadowed-input": "A local binding shadows a workflow input parameter.",
    "check/iteration-type": "A loop was applied to a value that is not an array.",
    "check/field-access": "A field was accessed on a value that does not have it.",
    "check/record-context": "A literal appeared where a specific record type is required.",
    "check/duplicate-literal-field": "A record literal sets one field more than once.",
    "check/duplicate-argument": "A call supplies one argument more than once.",
    "check/invalid-assignment": "An assignment's value type does not match the target's type.",
    "check/unsupported-literal": "A literal value has no representation in the type system.",
    "check/not-a-function": "A value used as a function is not a function value.",
    # -- Capabilities ----------------------------------------------------
    # These are the categories an editing agent most needs to tell apart, so
    # each is distinct rather than folded into a shared "invalid" bucket.
    "check/capability-denied": "An agent attempted a capability it does not hold.",
    "check/approval-required": "A mutation requires approval that was not declared.",
    "check/plan-first": "An agent that plans first lacks a model or the turns to plan.",
    "check/unknown-agent": "An agent name is not declared.",
    "check/unknown-tool": "A tool name is not declared.",
    "check/agent-conflict": "An agent both allows and denies the same capability.",
    "check/invalid-limit": "A declared numeric bound is outside its permitted range.",
    "parse/invalid-limit": "A count or limit literal is not within its permitted range.",
    "check/invalid-model": "A model declaration is invalid.",
    "parse/invalid-model": "A model declaration contains an invalid or unknown field.",
    "check/invalid-tool": "A tool declaration is invalid.",
    "check/empty-instructions": "An agent declares empty instructions.",
    "check/invalid-swarm": "A swarm declaration is incomplete or invalid.",
    "check/empty-workflow": "A workflow has no steps, or duplicate step names.",
    "check/recursion-limit": "A recursive call exceeds or lacks the declared recurse limit.",
    # -- Syntactic declarations -----------------------------------------
    "parse/unsupported-statement": "A statement form is recognised but not supported.",
    "parse/invalid-match-pattern": "A match pattern is not a scalar literal or '_'.",
    "parse/invalid-import": "An import does not name a quoted local .kos path.",
    # -- Modules ---------------------------------------------------------
    "module/not-found": "An imported module does not exist.",
    "module/outside-root": "An import escapes the granted local module root.",
    "module/cycle": "The local module graph contains an import cycle.",
    "module/limit-exceeded": "The module graph exceeds its file, depth, or size bound.",
    "module/invalid-export": "A module exports a duplicate or non-local declaration.",
    "module/private-symbol": "A module references a symbol its imports do not export.",
    "module/conflict": "Two modules contribute the same declaration name.",
    "module/invalid-encoding": "A module is not valid UTF-8 source.",
    # -- Fallback --------------------------------------------------------
    "kosika/internal-error": "A failure that does not map to a known diagnostic code.",
}


def is_known_code(code: str) -> bool:
    """True when ``code`` belongs to the closed registry."""

    return code in CODES


@dataclass(frozen=True)
class Span:
    """A zero-based, half-open source range, matching the LSP convention."""

    start_line: int
    start_character: int
    end_line: int
    end_character: int

    def to_lsp(self) -> dict[str, dict[str, int]]:
        return {
            "start": {"line": self.start_line, "character": self.start_character},
            "end": {"line": self.end_line, "character": self.end_character},
        }

    def to_dict(self) -> dict[str, Any]:
        return self.to_lsp()


@dataclass(frozen=True)
class Diagnostic:
    """One machine-readable problem found in a source document."""

    code: str
    message: str
    severity: Severity = Severity.ERROR
    span: Span | None = None
    path: str | None = None
    owner: str | None = None
    owner_kind: str | None = None
    data: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the JSON shape clients and the LSP both understand."""

        return {
            "code": self.code,
            "severity": int(self.severity),
            "message": self.message,
            "range": self.span.to_lsp() if self.span is not None else None,
            "path": self.path,
            "owner": self.owner,
            "owner_kind": self.owner_kind,
            "data": dict(self.data),
        }

    def to_lsp(self) -> dict[str, Any]:
        """Serialise as an LSP ``Diagnostic``."""

        payload: dict[str, Any] = {
            "severity": int(self.severity),
            "source": "kosika",
            "code": self.code,
            "message": self.message,
        }
        if self.span is not None:
            payload["range"] = self.span.to_lsp()
        data = dict(self.data)
        if self.path is not None:
            data.setdefault("path", self.path)
        if data:
            payload["data"] = data
        return payload


# ---------------------------------------------------------------------------
# Message classification
# ---------------------------------------------------------------------------

_LOCATION_RE = re.compile(r"at (\d+):(\d+)")
_QUOTED_NAME_RE = re.compile(r"'([A-Za-z_][A-Za-z0-9_]*)'")
_EXPECT_GOT_RE = re.compile(
    r"expects (?P<expected>'[^']*'|\S+?), got (?P<actual>'[^']*'|\S+)"
)
_PARSE_EXPECTED_RE = re.compile(
    r"^Expected (?P<expected>.+?) at \d+:\d+"
    r"(?:, got (?P<actual>.*))?$"
)
_OPERATOR_RE = re.compile(
    r"operator '(?P<operator>[^']+)' expects [^,]+, "
    r"got '(?P<left>[^']+)' and '(?P<right>[^']+)'"
)
_QUOTED_RE = re.compile(r"'([^']*)'")
#: e.g. "Step 'x' is missing record fields ['name']" or "has unknown tool arguments ['x']".
_FIELD_LIST_RE = re.compile(
    r"(?P<kind>missing|unknown) (?P<subject>record fields|tool arguments)"
    r" \[(?P<items>[^\]]*)\]"
)
#: Capability messages name both the agent and the tool; an agent repairing the
#: failure needs that pair, not just prose.
_CAPABILITY_RE = re.compile(
    r"agent '([^']+)' (?:cannot use|can use) tool '([^']+)'"
    r"|(can use unknown tool) '([^']+)'"
    r"|uses unknown (?:tool|agent) '([^']+)'"
)

#: Ordered rules. The first match wins, so more specific patterns are listed
#: before the broader ones they would otherwise be shadowed by.
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Lexical and syntactic first: these are raised before checking runs.
    ("lex/unexpected-character", re.compile(r"^Unexpected character at \d+:\d+")),
    (
        "parse/expected-identifier",
        re.compile(r"^Expected (identifier|string or identifier) at"),
    ),
    ("parse/expected-token", re.compile(r"^Expected \S+ at \d+:\d+")),
    ("parse/empty-block", re.compile(r"^Empty block")),
    ("parse/limit-exceeded", re.compile(r"(nesting|depth) exceeds \d+")),
    (
        "parse/unsupported-module",
        re.compile(r"^Unresolved imports: use load_program"),
    ),
    # Local module loading. These rules run before checker fallbacks because a
    # module error may include quoted declarations that look like type prose.
    ("parse/unnamed-argument", re.compile(r"^Argument at .* needs a name")),
    ("check/unnamed-argument", re.compile(r"argument needs a name; write name")),
    ("parse/invalid-type", re.compile(r"^A type is optional once")),
    ("module/outside-root", re.compile(r"Import outside the granted .* module root")),
    ("module/cycle", re.compile(r"^Import cycle at ")),
    (
        "module/limit-exceeded",
        re.compile(r"^Module graph exceeds|^Module exceeds 256 KiB"),
    ),
    ("module/invalid-export", re.compile(r"exports must name unique local")),
    ("module/private-symbol", re.compile(r"unavailable or private symbols")),
    ("module/conflict", re.compile(r"^Conflicting module declaration")),
    # Declaration structure.
    ("check/unknown-method", re.compile(r"unknown method: ")),
    ("check/method-arguments", re.compile(r"method '\w+' takes \(")),
    ("check/duplicate-declaration", re.compile(r"^Duplicate \w+ declaration")),
    ("check/duplicate-field", re.compile(r"^Duplicate \w+ field|has duplicate fields")),
    ("check/duplicate-parameter", re.compile(r"[Dd]uplicate\b.*\bparameter")),
    ("check/duplicate-pattern", re.compile(r"^Duplicate match pattern")),
    ("check/missing-field", re.compile(r"is missing record fields")),
    (
        "check/missing-argument",
        re.compile(r"is missing tool arguments|^Tool .* is missing:"),
    ),
    ("check/unknown-argument", re.compile(r"has unknown tool arguments")),
    (
        "check/unknown-field",
        re.compile(r"has unknown record fields|^Unknown \w+ field"),
    ),
    # Names.
    ("check/unknown-type", re.compile(r"^Unknown field type")),
    (
        "check/unknown-record",
        re.compile(
            r"^Unknown record type"
            r"|returns unknown record"
            r"|accepts unknown record"
            r"|requires a known record"
        ),
    ),
    (
        "check/unknown-function",
        re.compile(r"uses unknown function|^Unknown function"),
    ),
    (
        "check/unknown-workflow",
        re.compile(r"^Unknown workflow|^Recursive workflow call"),
    ),
    ("check/unknown-model", re.compile(r"uses unknown model")),
    ("check/return-outside-function", re.compile(r"uses return outside a function")),
    ("check/return-type", re.compile(r"early return expects ")),
    ("check/otherwise-execute", re.compile(r"cannot use otherwise with execute")),
    ("check/otherwise-type", re.compile(r"otherwise branch returns ")),
    ("check/value-step", re.compile(r"has a value where a call is needed")),
    ("check/try", re.compile(r" try gives | fail expects a Text reason")),
    ("check/method", re.compile(r"^Record '[^']+' method ")),
    ("check/parallel", re.compile(r"cannot run in parallel: ")),
    ("check/record-update", re.compile(r" record update ")),
    ("check/retry", re.compile(r"cannot retry: ")),
    ("check/verify", re.compile(r"cannot verify: ")),
    ("check/escalate", re.compile(r"cannot escalate: ")),
    ("check/vote", re.compile(r"cannot vote: ")),
    ("check/budget", re.compile(r"^Workflow '[^']+' budget ")),
    ("check/memory", re.compile(r"^Memory ")),
    ("check/board", re.compile(r"^Board ")),
    ("check/step-match", re.compile(r"^Step '[^']+' match ")),
    ("check/test", re.compile(r"^Test '.*' line \d+: ")),
    ("check/invalid-system-tool", re.compile(r"^Invalid system tool: ")),
    ("check/thinking-evidence", re.compile(r"cannot give evidence: ")),
    ("check/thinking-model-required", re.compile(r"needs a model for ")),
    ("check/thinking-record-clash", re.compile(r"is built in as ")),
    ("check/thinking-unknown-record", re.compile(r"extracts unknown record ")),
    ("check/shadowed-binding", re.compile(r"shadows an enclosing binding")),
    (
        "check/assignment-to-immutable",
        re.compile(r"cannot assign to .*: declare it with 'var'|cannot rebind"),
    ),
    ("check/assignment-to-unknown", re.compile(r"assigns to unknown variable")),
    # Types.
    ("check/condition-type", re.compile(r"condition expects 'Bool'")),
    (
        "check/return-type-mismatch",
        re.compile(
            r"body returns .*but declared output is"
            r"|but (final \w+|call) returns"
            r"|^(Function|Workflow) .* returns .*, got "
        ),
    ),
    (
        "check/branch-type-mismatch",
        re.compile(
            r"^Match branches must have the same result type|conditional branches differ"
        ),
    ),
    (
        "check/match-type-mismatch",
        re.compile(r"^Match pattern type differs|^Match subjects must be scalar"),
    ),
    ("check/array-index-type", re.compile(r"array index expects 'Number'")),
    ("check/non-array-index", re.compile(r"cannot index non-array type")),
    ("check/unknown-operator", re.compile(r"has unknown operator")),
    (
        "check/operator-type-mismatch",
        re.compile(r"operator .* expects .* operands, got |requires matching "),
    ),
    ("check/inference-failure", re.compile(r"cannot infer the type of")),
    (
        "check/unsupported-expression",
        re.compile(r"unsupported expression|method call .* is not"),
    ),
    ("check/exhaustiveness", re.compile(r"^Non-exhaustive match")),
    ("check/unreachable-arm", re.compile(r"^Unreachable match arm")),
    # Capability and approval. These are the errors an editing agent most needs
    # to distinguish, so each gets its own code rather than a shared bucket.
    (
        "check/capability-denied",
        re.compile(r"^Capability violation:|plans first but can change nothing"),
    ),
    ("check/plan-first", re.compile(r"plans first, so ")),
    ("parse/invalid-declaration", re.compile(r"^Agent plan must be `first` or `off`")),
    (
        "check/approval-required",
        re.compile(r"needs approval before execute tool|must require approval for"),
    ),
    (
        "check/unknown-agent",
        re.compile(r"uses unknown agent|requires a known agent with a model"),
    ),
    ("check/unknown-tool", re.compile(r"uses unknown tool|can use unknown tool")),
    ("check/agent-conflict", re.compile(r"both allows and denies")),
    # Declaration limits and validity.
    (
        "check/invalid-limit",
        re.compile(
            r"must be between \d"
            r"|^Recursion limit must be"
            r"|requires recurse limits"
            r"|^Swarm max_rounds must be"
        ),
    ),
    (
        "parse/invalid-limit",
        re.compile(
            r"limit must be an integer from"
            r"|retry count must be an integer"
            r"|^repeat count must be an integer"
            r"|must be a positive number"
            r"|must be a positive integer"
            r"|^Model \d+ must be a number"
        ),
    ),
    (
        "check/invalid-model",
        re.compile(
            r"^Model .* deployment must not be empty|^Model .* stop must contain"
        ),
    ),
    (
        "parse/invalid-model",
        re.compile(
            r"^Invalid or duplicate model field"
            r"|^Invalid model field"
            r"|^Duplicate model parameter"
            r"|^Unknown model parameter"
            r"|^Model .* requires (known )?input and output records"
            r"|requires (known )?input and output records"
        ),
    ),
    ("check/invalid-tool", re.compile(r"^Tool .* has invalid access level")),
    ("check/empty-instructions", re.compile(r"instructions must not be empty")),
    ("check/invalid-swarm", re.compile(r"^Swarm |^Duplicate swarm")),
    ("check/empty-workflow", re.compile(r"has no steps|has duplicate step names")),
    ("check/missing-return", re.compile(r"must return its binding")),
    ("check/shadowed-input", re.compile(r"cannot shadow workflow input")),
    ("check/iteration-type", re.compile(r"for loop requires an array")),
    (
        "check/field-access",
        re.compile(r"cannot access field|references unknown field .* on "),
    ),
    (
        "check/record-context",
        re.compile(r"literal requires an? \w*-typed field"),
    ),
    (
        "check/duplicate-literal-field",
        re.compile(r"has duplicate record literal fields"),
    ),
    ("check/duplicate-argument", re.compile(r"has duplicate tool arguments")),
    ("check/invalid-assignment", re.compile(r"assigns .* to .* of type ")),
    (
        "check/not-a-function",
        re.compile(
            r"is not a function value"
            r"|^Function values are not allowed"
            r"|^Unlowered nested functions|^Unlowered thinking steps"
        ),
    ),
    (
        "check/recursion-limit",
        re.compile(r"^Recursive function call|requires recurse limits"),
    ),
    ("check/unsupported-literal", re.compile(r"^Unsupported literal value")),
    ("parse/unsupported-statement", re.compile(r"is not supported at \d+:\d+")),
    (
        "parse/invalid-match-pattern",
        re.compile(r"^Match patterns must be scalar"),
    ),
    (
        "parse/invalid-import",
        re.compile(r"^Import requires a quoted local|^Import paths must be relative"),
    ),
    (
        "parse/invalid-declaration",
        re.compile(r"^Unexpected (declaration|workflow item)"),
    ),
    # Generic fallbacks: typed mismatches that share one shape.
    ("check/type-mismatch", re.compile(r"expects .*, got ")),
    # Name resolution that appears in step context.
    ("check/unknown-variable", re.compile(r"references unknown variable")),
)

_UNKNOWN_VARIABLE_TAIL = re.compile(r"references unknown variable '([^']+)'")


def classification_rules() -> tuple[tuple[str, re.Pattern[str]], ...]:
    """The ordered classification templates, in matching order.

    Exposed so :mod:`kosika.diagnostics_source` can verify that every raise
    site in the parser, lexer, and checker is claimed by one of these rules,
    and that every rule is claimed by a real raise site.
    """

    return _RULES


def code_for(message: str) -> str:
    """Map an existing error message onto a stable diagnostic code.

    An unrecognised message returns the explicit fallback code rather than a
    best guess, so a new upstream message can never be mislabelled.
    """

    for code, pattern in _RULES:
        if pattern.search(message):
            return code
    return "kosika/internal-error"


_LOCAL_LABEL_RE = re.compile(r"Step 'function [A-Za-z_]\w* local ([A-Za-z_]\w*)'")
_FUNCTION_STEP_RE = re.compile(r"^function (?P<name>[A-Za-z_][A-Za-z0-9_]*)\b")


def _owner_of(message: str) -> tuple[str | None, str | None]:
    """Extract the declaration name and kind a checker message refers to.

    The checker labels a function body with a synthetic step name such as
    ``"function total"`` or ``"function total local acc"``. Those are not real
    steps, so they are mapped to the enclosing ``fn`` declaration; otherwise
    every error inside a function body would be unanchorable.
    """

    step = re.search(r"Step '([^']+)'", message)
    if step is not None:
        label = step.group(1)
        function = _FUNCTION_STEP_RE.match(label)
        if function is not None:
            return function.group("name"), "function"
        return label, "step"
    # Capitalised labels are declaration-level messages; the lowercase forms are
    # the capability messages, which name the agent and tool involved rather
    # than the declaration that failed.
    for kind, label in (
        ("function", "Function"),
        ("agent", "Agent"),
        ("record", "Record"),
        ("workflow", "Workflow"),
        ("model", "Model"),
        ("tool", "Tool"),
        ("swarm", "Swarm"),
        ("local", "Local"),
        ("agent", "agent"),
    ):
        found = re.search(rf"{label} '([^']+)'", message)
        if found is not None:
            return found.group(1), kind
    return None, None


def _unquote(value: str) -> str:
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] == "'" else value


def _type_data(message: str) -> dict[str, Any]:
    """Pull repair-relevant names and expected/actual values from a failure."""

    data: dict[str, Any] = {}
    capability = _CAPABILITY_RE.search(message)
    if capability is not None:
        if capability.group(1) is not None:
            data["agent"] = capability.group(1)
            data["tool"] = capability.group(2)
        elif capability.group(4) is not None:
            data["tool"] = capability.group(4)
        elif capability.group(5) is not None:
            data["name"] = capability.group(5)
    fields = _FIELD_LIST_RE.search(message)
    if fields is not None:
        noun = "fields" if fields.group("subject").endswith("fields") else "arguments"
        data[f"{fields.group('kind')}_{noun}"] = _QUOTED_RE.findall(
            fields.group("items")
        )
    operator = _OPERATOR_RE.search(message)
    if operator is not None:
        data["operator"] = operator.group("operator")
        data["left"] = operator.group("left")
        data["right"] = operator.group("right")
    parsed = _PARSE_EXPECTED_RE.search(message)
    if parsed is not None:
        data["expected"] = _unquote(parsed.group("expected"))
        if parsed.group("actual") is not None:
            data["actual"] = _unquote(parsed.group("actual"))
        return data
    match = _EXPECT_GOT_RE.search(message)
    if match is not None:
        data["expected"] = _unquote(match.group("expected"))
        data["actual"] = _unquote(match.group("actual"))
        return data
    branches = re.search(r"branches differ: '([^']+)' and '([^']+)'", message)
    if branches is not None:
        data["branches"] = [branches.group(1), branches.group(2)]
    returns = re.search(
        r"body returns '([^']+)', but declared output is '([^']+)'", message
    )
    if returns is not None:
        data["actual"] = returns.group(1)
        data["expected"] = returns.group(2)
    declared = re.search(
        r"^(?:Function|Workflow) .* returns '([^']+)', got '([^']+)'", message
    )
    if declared is not None:
        data["expected"] = declared.group(1)
        data["actual"] = declared.group(2)
    binding = re.search(
        r"binding '[^']+' is '([^']+)', but call returns '([^']+)'", message
    )
    if binding is not None:
        data["expected"] = binding.group(1)
        data["actual"] = binding.group(2)
    final = re.search(
        r"returns '([^']+)', but final (?:tool|call) returns '([^']+)'", message
    )
    if final is not None:
        data["expected"] = final.group(1)
        data["actual"] = final.group(2)
    assignment = re.search(r"assigns '([^']+)' to .* of type '([^']+)'", message)
    if assignment is not None:
        data["actual"] = assignment.group(1)
        data["expected"] = assignment.group(2)
    return data


# ---------------------------------------------------------------------------
# Span anchoring
# ---------------------------------------------------------------------------

_DECLARATION_KEYWORDS = {
    "record": "record",
    "tool": "tool",
    "agent": "agent",
    "model": "model",
    "workflow": "workflow",
    "fn": "function",
    "swarm": "swarm",
}


class _Anchors:
    """Resolves a declaration or step name to its token position."""

    def __init__(self, tokens: Sequence[Token]) -> None:
        self._declarations: dict[str, list[Token]] = {}
        self._steps: dict[str, list[Token]] = {}
        self._positions = {(token.line, token.column): token for token in tokens}
        self._tokens = list(tokens)
        self._build(tokens)

    def _build(self, tokens: Sequence[Token]) -> None:
        depth = 0
        declaring = ""
        for index, token in enumerate(tokens[:-1]):
            if token.value in {"{", "["}:
                depth += 1
            elif token.value in {"}", "]"}:
                depth = max(depth - 1, 0)
            if token.kind != "IDENT":
                continue
            following = tokens[index + 1]
            if depth == 0 and token.value in _DECLARATION_KEYWORDS:
                declaring = token.value
                if following.kind == "IDENT":
                    self._declarations.setdefault(following.value, []).append(following)
            elif token.value == "step" and following.kind == "IDENT":
                self._steps.setdefault(following.value, []).append(following)
            elif depth == 1 and declaring == "workflow" and following.value == "=":
                # The one-line step form: `name = call`.
                self._steps.setdefault(token.value, []).append(token)

    @staticmethod
    def _unique(candidates: Sequence[Token]) -> Token | None:
        return candidates[0] if len(candidates) == 1 else None

    def declaration(self, name: str) -> Token | None:
        return self._unique(self._declarations.get(name, ()))

    def step(self, name: str) -> Token | None:
        return self._unique(self._steps.get(name, ()))

    def token_at(self, line: int, column: int) -> Token | None:
        return self._positions.get((line, column))

    def named_within(self, owner: Token, names: Sequence[str]) -> Token | None:
        """The one token inside ``owner``'s body that spells a named thing.

        A checker message usually names what is wrong: a field, a variable, a
        method. When that name occurs exactly once inside the owning
        declaration or step, that occurrence is where the problem is, and
        pointing at it is still truthful. A name that occurs twice is
        ambiguous, so it is skipped rather than guessed at.
        """

        try:
            start = self._tokens.index(owner)
        except ValueError:
            return None
        body: list[Token] = []
        depth = 0
        opened = False
        one_line_step = (
            start + 1 < len(self._tokens) and self._tokens[start + 1].value == "="
        )
        for token in self._tokens[start + 1 :]:
            if one_line_step:
                if token.line != owner.line:
                    break
                body.append(token)
                continue
            if token.value == "{":
                depth += 1
                opened = True
            elif token.value == "}":
                depth -= 1
                if opened and depth == 0:
                    break
            if opened:
                body.append(token)
        for name in names:
            matches = [
                token
                for token in body
                if token.kind in {"IDENT", "STRING"} and token.value == name
            ]
            if len(matches) == 1:
                return matches[0]
        return None

    def local_binding(self, owner: Token, name: str) -> Token | None:
        """The `let name` or `var name` token inside ``owner``'s body, if unique."""

        try:
            start = self._tokens.index(owner)
        except ValueError:
            return None
        found: list[Token] = []
        depth = 0
        opened = False
        previous = owner
        for token in self._tokens[start + 1 :]:
            if token.value == "{":
                depth += 1
                opened = True
            elif token.value == "}":
                depth -= 1
                if opened and depth == 0:
                    break
            if opened and token.value == name and previous.value in {"let", "var"}:
                found.append(token)
            previous = token
        return found[0] if len(found) == 1 else None

    def owner_token(self, owner: str, owner_kind: str | None) -> Token | None:
        return self.step(owner) if owner_kind == "step" else self.declaration(owner)

    def first_use(self, names: Sequence[str]) -> Token | None:
        """The earliest identifier token spelling any of ``names``.

        An unknown name has no declaration to anchor on, so its first use in
        the source is the truthful place to point: that token is where the
        unresolved reference actually is.
        """

        wanted = set(names)
        uses = [
            token
            for token in self._positions.values()
            if token.kind == "IDENT" and token.value in wanted
        ]
        return min(uses, key=lambda token: (token.line, token.column), default=None)


def _span_for(
    anchors: _Anchors | None,
    owner: str | None,
    owner_kind: str | None,
    location: tuple[int, int] | None,
) -> tuple[Span | None, bool]:
    """Resolve a span, reporting whether anchoring succeeded.

    Returns ``(span, anchor_missing)``. ``anchor_missing`` is True when no
    position could be resolved, so callers can record that fact instead of
    implying a position they do not have.
    """

    if location is not None:
        line, column = location
        token = anchors.token_at(line, column) if anchors is not None else None
        length = len(token.value) if token is not None else 1
        return Span(line - 1, column - 1, line - 1, column - 1 + length), False
    if anchors is None or owner is None:
        return None, True
    if owner_kind == "step":
        token = anchors.step(owner)
    else:
        token = anchors.declaration(owner)
    if token is None:
        return None, True
    line = max(token.line - 1, 0)
    character = max(token.column - 1, 0)
    return Span(line, character, line, character + len(token.value)), False


def _named_in(message: str, owner: str) -> list[str]:
    """Names a message points at, most specific first.

    Text after "Did you mean" is a suggestion, not something in the source,
    and the owner's own name is where the fallback span already points.
    """

    body = message.split(" Did you mean", 1)[0]
    body = re.sub(r"^Step '[^']*'", "", body)
    names = _QUOTED_NAME_RE.findall(body)
    got = re.search(r"got \(([^)]*)\)", body)
    if got is not None:
        names = [*re.findall(r"[A-Za-z_][A-Za-z0-9_]*", got.group(1)), *names]
    seen: list[str] = []
    for name in names:
        if name != owner and name not in seen:
            seen.append(name)
    return seen


def _location(message: str) -> tuple[int, int] | None:
    match = _LOCATION_RE.search(message)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def anchors_for(tokens: Sequence[Token]) -> _Anchors:
    """Build a declaration/step position index from an already-lexed token stream.

    Exposed so the editor-feature layer can anchor diagnostics without
    re-lexing a document it has already tokenised.
    """

    return _Anchors(tokens)


def diagnostic_from_exception(
    exc: Exception,
    *,
    anchors: "_Anchors | None" = None,
    path: str | Path | None = None,
    code: str | None = None,
) -> Diagnostic:
    """Convert one lexer/parser/checker/module error into a diagnostic."""

    message = str(exc)
    source_path = str(path) if path is not None else None
    if source_path is not None:
        prefix = f"{source_path}: "
        if message.startswith(prefix):
            message = message[len(prefix) :]
    resolved_code = code or code_for(message)
    owner, owner_kind = _owner_of(message)
    location = _location(message)
    span, anchor_missing = _span_for(anchors, owner, owner_kind, location)

    data: dict[str, Any] = {}
    if anchors is not None and owner is not None and location is None:
        owner_token = anchors.owner_token(owner, owner_kind)
        named = (
            anchors.named_within(owner_token, _named_in(message, owner))
            if owner_token is not None
            else None
        )
        local = _LOCAL_LABEL_RE.search(message)
        if named is None and owner_token is not None and local is not None:
            # Nothing named is unique, but the checker said which local's
            # initialiser it was in, so point at that declaration.
            named = anchors.local_binding(owner_token, local.group(1))
        if named is not None:
            # A string token's position is its opening quote.
            width = len(named.value) + (2 if named.kind == "STRING" else 0)
            span = Span(
                named.line - 1,
                named.column - 1,
                named.line - 1,
                named.column - 1 + width,
            )
            data["anchor"] = "name"
    if resolved_code == "module/private-symbol":
        names = _QUOTED_NAME_RE.findall(message.split("symbols", 1)[-1])
        data["names"] = names
        use = anchors.first_use(names) if anchors is not None and span is None else None
        if use is not None:
            span = Span(
                use.line - 1,
                use.column - 1,
                use.line - 1,
                use.column - 1 + len(use.value),
            )
    if resolved_code.startswith(("parse/", "lex/")):
        data["phase"] = "parse"
    elif resolved_code.startswith("module/"):
        data["phase"] = "module"
    else:
        data["phase"] = "check"
    if source_path is not None:
        data["path"] = source_path
    if owner is not None:
        data["owner"] = owner
    if owner_kind is not None:
        data["owner_kind"] = owner_kind
    variable = _UNKNOWN_VARIABLE_TAIL.search(message)
    if variable is not None:
        data["name"] = variable.group(1)
    data.update(_type_data(message))
    if span is None:
        data["anchor_missing"] = True
    if resolved_code == "kosika/internal-error":
        data["unmapped"] = True

    return Diagnostic(
        code=resolved_code,
        message=message,
        severity=Severity.ERROR,
        span=span,
        path=source_path,
        owner=owner,
        owner_kind=owner_kind,
        data=data,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def analyze(source: str, *, path: str = "<memory>") -> list[Diagnostic]:
    """Analyse one self-contained source document.

    Imports require :func:`analyze_path`; this entry point reports that
    limitation explicitly instead of returning a false clean result.
    """

    if not isinstance(source, str):
        raise TypeError("Kosika source must be text")

    tokens: list[Token] = []
    try:
        tokens = lex(source)
        program = parse(source)
    except (ParseError, ValueError) as exc:
        anchors = _Anchors(tokens) if tokens else None
        return [diagnostic_from_exception(exc, anchors=anchors, path=path)]

    anchors = _Anchors(tokens)
    try:
        check(program)
    except CheckError as exc:
        return [diagnostic_from_exception(exc, anchors=anchors, path=path)]
    return []


def analyze_path(
    path: str | Path,
    *,
    root: str | Path | None = None,
    source_overrides: Mapping[Path, str] | None = None,
) -> list[Diagnostic]:
    """Check one bounded module graph and locate its first failure."""

    context = ModuleLoadContext()
    try:
        load_program(
            path,
            root=root,
            source_overrides=source_overrides,
            _diagnostic_context=context,
        )
    except (CheckError, ParseError, OSError, UnicodeError, ValueError) as exc:
        message = str(exc)
        owner, owner_kind = _owner_of(message)
        source_path = context.error_path
        if source_path is None and owner is not None:
            source_path = context.owner_path(owner, owner_kind)
        source = context.sources.get(source_path) if source_path is not None else None
        anchors = None
        if source is not None:
            try:
                anchors = _Anchors(lex(source))
            except ValueError:
                anchors = None
        override = None
        if isinstance(exc, FileNotFoundError):
            override = "module/not-found"
        elif isinstance(exc, UnicodeDecodeError):
            override = "module/invalid-encoding"
        return [
            diagnostic_from_exception(
                exc,
                anchors=anchors,
                path=source_path,
                code=override,
            )
        ]
    return []


def _report_payload(diagnostics: Sequence[Diagnostic], path: str) -> dict[str, Any]:
    errors = [item for item in diagnostics if item.severity is Severity.ERROR]
    return {
        "schema": DIAGNOSTIC_SCHEMA,
        "path": path,
        "ok": not errors,
        "error_count": len(errors),
        "codes": sorted({item.code for item in diagnostics}),
        "diagnostics": [item.to_dict() for item in diagnostics],
    }


def report(source: str, *, path: str = "<memory>") -> dict[str, Any]:
    """Return a serialisable single-document diagnostic report."""

    return _report_payload(analyze(source, path=path), path)


def report_path(
    path: str | Path,
    *,
    root: str | Path | None = None,
    source_overrides: Mapping[Path, str] | None = None,
) -> dict[str, Any]:
    """Return a serialisable report for an authoritative module-graph check."""

    return _report_payload(
        analyze_path(path, root=root, source_overrides=source_overrides),
        str(path),
    )


__all__ = [
    "CODES",
    "DIAGNOSTIC_SCHEMA",
    "Diagnostic",
    "Severity",
    "Span",
    "analyze",
    "analyze_path",
    "anchors_for",
    "classification_rules",
    "code_for",
    "diagnostic_from_exception",
    "is_known_code",
    "report",
    "report_path",
]
