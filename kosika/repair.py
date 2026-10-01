"""Repair Kosika programs with a model, behind the benchmark's solver seam.

This module is the adapter that turns a :class:`~kosika.model.ModelProvider`
into a :data:`~kosika.benchmark.Solver`, so a model can be scored by exactly
the same harness as the identity and oracle baselines.

Three decisions shape the design.

**The model is asked for a search/replace patch, never line numbers.** Models
are good at quoting text they have seen and bad at counting lines, so a patch
that quotes the code to change and the code to put in its place is both easier
to produce and impossible to mis-index. The adapter locates the quoted text and
derives the line edit itself, so a model can never address a line that does not
exist. The search text must appear *exactly once*; an ambiguous or absent match
is rejected and retried rather than guessed at.

**Two prompt strategies exist so the benchmark can choose between them.** A
model that is shown only the failing region has less to read but may lack the
surrounding declaration it needs; a model shown the whole program has the
context but spends far more output tokens regenerating it. Which wins is an
empirical question, so both are implemented and selectable.

**Repair is a bounded loop, not a single shot.** A first attempt that produces
a plausible but insufficient edit should be fed the *new* diagnostic rather
than abandoned, because that is what a person or an agent does. Every attempt
is recorded, so a low score can be attributed: a solver that never produced a
applicable patch is a very different failure from one that patched repeatedly
and converged on the wrong fix.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from .corpus import CorpusCase, Edit, source_lines
from .editor_features import MAX_SOURCE_BYTES
from .model import ChatMessage, ModelProvider, ModelRequest, ModelResponse

#: Markers delimiting a replacement block. The doubled angle brackets are
#: chosen to be unlikely in Kosika source while remaining easy for a model to
#: reproduce exactly.
SEARCH_MARKER = "<<<<<<< SEARCH"
DIVIDER_MARKER = "======="
REPLACE_MARKER = ">>>>>>> REPLACE"

#: Bounds on what a model may do to a program.
MAX_INSERTED_LINES = 40
MAX_REPLACEMENTS = 5

SYSTEM_PROMPT = (
    "You repair Kosika programs. Kosika is a language with explicit records, "
    "models, tools, agents, and workflows.\n"
    "You will be given a program and the diagnostic it produces. Reply with "
    "one or more replacement blocks and nothing else.\n"
    "Each block is exactly:\n"
    f"{SEARCH_MARKER}\n"
    "the exact existing lines to replace\n"
    f"{DIVIDER_MARKER}\n"
    "the lines that should take their place\n"
    f"{REPLACE_MARKER}\n"
    "The search text must appear exactly once in the program. Preserve the "
    "indentation of the code you replace. Do not explain your change."
)

DIAGNOSTIC_GUIDE = """Diagnostic codes you will see:
- check/capability-denied: the agent used a tool it does not hold. Grant it in
  the agent's `can:` list, or use a different tool.
- check/agent-conflict: the agent both allows and denies the same capability.
  Remove the entry from one of the lists.
- check/unknown-model: the agent names a model that is not declared.
- check/unknown-tool: a tool is used that is not declared.
- check/invalid-tool: a tool declares an unsupported `access` level. Use
  observe, analyze, prepare, or execute.
- check/invalid-limit: a declared number is outside its allowed range.
- check/invalid-model: a model declaration is invalid, for example an empty
  `deployment`.
- check/unknown-record: a record type is used that is not declared.
- check/unknown-function: a function is called that is not declared.
- check/missing-field: a record literal omits a field the record declares.
- check/unknown-field: a record literal sets a field the record does not declare.
- check/duplicate-field: a record declares the same field twice.
- check/empty-workflow: a workflow has no steps, or two steps share a name.
- check/type-mismatch: a value's type does not match the required type.
- parse/expected-token, parse/expected-identifier, parse/invalid-declaration,
  lex/unexpected-character: a syntax error. Balance the braces and brackets and
  make every declaration header well formed."""


class PatchError(ValueError):
    """Raised when a model's response is not an applicable patch."""


#: How much of a model response to keep in an attempt record. Enough to see a
#: formatting mistake, small enough that a report stays readable.
RESPONSE_EXCERPT = 600


def excerpt(content: str | None, limit: int = RESPONSE_EXCERPT) -> str:
    """Trim a response for the attempt record."""

    if not content:
        return ""
    return content if len(content) <= limit else content[:limit] + "\n...[truncated]"


class RepairStrategy(str, Enum):
    """What the model is shown.

    ``CONTEXTUAL`` shows the diagnostic's region with surrounding lines.
    ``FULL`` shows the entire program.
    """

    CONTEXTUAL = "contextual"
    FULL = "full"


# ---------------------------------------------------------------------------
# Patch parsing
# ---------------------------------------------------------------------------

_BLOCK_RE = re.compile(
    re.escape(SEARCH_MARKER)
    + r"\n(?P<search>.*?)\n"
    + re.escape(DIVIDER_MARKER)
    + r"\n(?P<replace>.*?)\n"
    + re.escape(REPLACE_MARKER),
    re.DOTALL,
)
_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+-]*\n?")


@dataclass(frozen=True)
class Replacement:
    """One search/replace block."""

    search: str
    replace: str

    def to_dict(self) -> dict[str, str]:
        return {"search": self.search, "replace": self.replace}


def parse_replacements(text: str) -> tuple[Replacement, ...]:
    """Extract replacement blocks from a model response.

    Tolerates the shapes models actually emit: fenced or unfenced blocks,
    surrounding prose, and trailing commentary. A response with no block at all
    is an error, because silently returning no edits would score as a solver
    that did nothing rather than as a formatting failure worth retrying.
    """

    if not isinstance(text, str) or not text.strip():
        raise PatchError("Model returned an empty response")
    cleaned = _FENCE_RE.sub("", text)
    blocks = _BLOCK_RE.findall(cleaned)
    if not blocks:
        raise PatchError("Response contained no replacement block")
    if len(blocks) > MAX_REPLACEMENTS:
        raise PatchError(
            f"Response contained {len(blocks)} blocks, limit is {MAX_REPLACEMENTS}"
        )
    replacements = []
    for search, replace in blocks:
        if not search.strip():
            raise PatchError("Replacement block has empty search text")
        added = len([line for line in replace.splitlines() if line.strip()])
        if added > MAX_INSERTED_LINES:
            raise PatchError(
                f"Replacement inserts {added} lines, limit is {MAX_INSERTED_LINES}"
            )
        replacements.append(Replacement(search=search, replace=replace))
    return tuple(replacements)


def _unique_offset(lines: Sequence[str], needle: Sequence[str]) -> int | None:
    """Return the index where ``needle`` occurs exactly once, else ``None``."""

    if not needle:
        return None
    width = len(needle)
    hits = [
        index
        for index in range(len(lines) - width + 1)
        if list(lines[index : index + width]) == list(needle)
    ]
    return hits[0] if len(hits) == 1 else None


def apply_replacement(source: str, replacement: Replacement) -> tuple[str, Edit]:
    """Apply one replacement, returning the new source and the line edit.

    The search text must match exactly one region. When it matches none or
    several, the replacement is rejected instead of being applied to an
    arbitrary location: a wrong guess would look like a fix while silently
    corrupting the program.
    """

    lines = source_lines(source)
    needle = source_lines(replacement.search)
    offset = _unique_offset(lines, needle)
    if offset is None:
        occurrences = 0
        width = len(needle)
        if needle:
            occurrences = sum(
                1
                for index in range(len(lines) - width + 1)
                if list(lines[index : index + width]) == list(needle)
            )
        if occurrences == 0:
            raise PatchError("Search text does not appear in the program")
        raise PatchError(f"Search text appears {occurrences} times; it must be unique")
    replacement_lines = source_lines(replacement.replace)
    edit = Edit(
        start=offset,
        removed=len(needle),
        inserted=tuple(replacement_lines),
    )
    result = "\n".join(edit.apply(lines))
    if len(result.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise PatchError("Patched program exceeds the source-size limit")
    return result, edit


# ---------------------------------------------------------------------------
# Prompting
# ---------------------------------------------------------------------------


def _number_program(lines: Sequence[str], start: int) -> str:
    return "\n".join(f"{index + 1:>4} | {line}" for index, line in enumerate(lines))


def _region(
    source: str, diagnostic: Mapping[str, Any] | None, padding: int
) -> tuple[str, int | None]:
    """Return a window of the program around the diagnostic, and its first line.

    The window is raw source, deliberately *not* numbered. An earlier version
    prefixed each line with its number, which reads well to a human but made
    the model quote the prefixes back inside the search text, so every patch
    failed to match. Since the model must reproduce existing text exactly, the
    region has to be byte-identical to the program. The line number is
    reported separately in prose, where it can guide without contaminating a
    quotation.
    """

    lines = source_lines(source)
    span = diagnostic.get("range") if diagnostic else None
    centre = int(span["start"]["line"]) if isinstance(span, Mapping) else 0
    low = max(0, centre - padding)
    high = min(len(lines), centre + padding + 1)
    window = lines[low:high]
    if not window:
        return "\n".join(lines[:40]), 1
    return "\n".join(window), low + 1


def _region_label(first_line: int) -> str:
    return f"(showing lines {first_line} onwards)"


def describe_diagnostic(diagnostic: Mapping[str, Any] | None) -> str:
    """Render a diagnostic for a prompt, including its structured data."""

    if diagnostic is None:
        return "No diagnostic was reported."
    lines = [f"code: {diagnostic['code']}", f"message: {diagnostic['message']}"]
    data = diagnostic.get("data") or {}
    interesting = {
        key: value
        for key, value in data.items()
        if key not in {"phase", "anchor_missing", "unmapped"} and value is not None
    }
    if interesting:
        lines.append(
            "details: " + json.dumps(interesting, sort_keys=True, ensure_ascii=False)
        )
    return "\n".join(lines)


def build_proposal_messages(
    source: str,
    diagnostic: Mapping[str, Any] | None,
    *,
    path: str | None = None,
    strategy: RepairStrategy = RepairStrategy.CONTEXTUAL,
    padding: int = 12,
    feedback: Sequence[str] = (),
) -> tuple[ChatMessage, ...]:
    """Build the conversation for one patch proposal against one source file.

    This is the shared seam between the benchmark solver and the authoring
    loop: both ask for the same search/replace patch against the same quoted
    text, so both are held to the same prompt and the same answer format.
    """

    if strategy is RepairStrategy.FULL:
        shown = source
        lead = "Here is the complete program."
    else:
        shown, first_line = _region(source, diagnostic, padding)
        lead = f"Here is the region around the failure, from line {first_line}."
        span = diagnostic.get("range") if diagnostic else None
        if isinstance(span, Mapping):
            # The region alone does not say what to look at, so the failing
            # line is named explicitly.
            lead += f" The failure is on line {int(span['start']['line']) + 1}."
    user = [
        lead,
        "",
        f"```kosika\n{shown}\n```",
        "",
        "The diagnostic is:",
        describe_diagnostic(diagnostic),
        "",
        DIAGNOSTIC_GUIDE,
    ]
    if path:
        # The file matters for a project with several modules: the patch is
        # applied to the file the diagnostic came from, and only that file.
        user.extend(["", f"This program is the file {path}."])
    if feedback:
        user.extend(["", "Attempts that did not work:"])
        user.extend(f"- {item}" for item in feedback)
    user.extend(
        [
            "",
            f"Reply with {SEARCH_MARKER} / {DIVIDER_MARKER} / {REPLACE_MARKER} blocks.",
            "Copy the search text exactly as it appears above, with no line "
            "numbers, and include enough surrounding lines to be unique.",
        ]
    )
    return (
        ChatMessage(role="system", content=SYSTEM_PROMPT),
        ChatMessage(role="user", content="\n".join(user)),
    )


def build_messages(
    case: CorpusCase,
    diagnostic: Mapping[str, Any] | None,
    *,
    strategy: RepairStrategy = RepairStrategy.CONTEXTUAL,
    padding: int = 12,
    feedback: Sequence[str] = (),
) -> tuple[ChatMessage, ...]:
    """Build the conversation for one repair attempt.

    ``feedback`` carries the outcome of earlier attempts so the model is not
    asked to repeat a patch it already tried.
    """

    return build_proposal_messages(
        case.source,
        diagnostic,
        strategy=strategy,
        padding=padding,
        feedback=feedback,
    )


# ---------------------------------------------------------------------------
# The solver
# ---------------------------------------------------------------------------


@dataclass
class Attempt:
    """One try, and what came of it.

    ``response`` holds an excerpt of what the model actually returned. Without
    it a run that scores zero is indistinguishable between a model that cannot
    reason about the language and a harness that mis-parses a good answer.
    """

    index: int
    applied: bool
    error: str | None = None
    remaining: tuple[str, ...] = ()
    note: str = ""
    response: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "applied": self.applied,
            "error": self.error,
            "remaining": list(self.remaining),
            "note": self.note,
            "response": self.response,
        }


@dataclass
class RepairOutcome:
    """The result of repairing one case."""

    case_id: str
    code: str | None
    solved: bool
    attempts: list[Attempt] = field(default_factory=list)
    usage_prompt_tokens: int = 0
    usage_completion_tokens: int = 0

    @property
    def model_calls(self) -> int:
        return len(self.attempts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.case_id,
            "code": self.code,
            "solved": self.solved,
            "model_calls": self.model_calls,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "prompt_tokens": self.usage_prompt_tokens,
            "completion_tokens": self.usage_completion_tokens,
        }


class ModelRepairSolver:
    """Repair corpus cases by asking a model for patches.

    Implements the benchmark's solver seam, so it is scored by the same harness
    as the identity and oracle baselines.
    """

    name = "model"

    def __init__(
        self,
        provider: ModelProvider,
        *,
        strategy: RepairStrategy = RepairStrategy.CONTEXTUAL,
        max_attempts: int = 2,
        model: str | None = None,
        temperature: float = 0.0,
        max_output_tokens: int = 2048,
        padding: int = 12,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.provider = provider
        self.strategy = strategy
        self.max_attempts = max_attempts
        self.model = model
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.padding = padding
        self.outcomes: list[RepairOutcome] = []

    # -- provider plumbing -------------------------------------------------

    def _complete(self, messages: Sequence[ChatMessage]) -> ModelResponse:
        return self.provider.complete(
            ModelRequest(
                messages=tuple(messages),
                model=self.model,
                temperature=self.temperature,
                max_output_tokens=self.max_output_tokens,
            )
        )

    # -- the loop ----------------------------------------------------------

    def _repair(self, case: CorpusCase) -> tuple[str, RepairOutcome]:
        """Run the bounded repair loop, returning the final source and outcome."""

        from .diagnostics import analyze

        outcome = RepairOutcome(case_id=case.case_id, code=case.code, solved=False)
        current = case.source
        feedback: list[str] = []
        for index in range(self.max_attempts):
            # Re-derive the diagnostic from the *current* source so the model is
            # shown the error that remains, not the one it started with.
            diagnostics = analyze(current, path=case.origin)
            payload = diagnostics[0].to_dict() if diagnostics else case.diagnostic
            messages = build_messages(
                case,
                payload,
                strategy=self.strategy,
                padding=self.padding,
                feedback=tuple(feedback),
            )
            try:
                response = self._complete(messages)
            except Exception as exc:
                outcome.attempts.append(Attempt(index, False, f"provider error: {exc}"))
                break
            if response.usage is not None:
                outcome.usage_prompt_tokens += response.usage.prompt_tokens or 0
                outcome.usage_completion_tokens += response.usage.completion_tokens or 0
            # A reasoning model can spend the whole output budget thinking and
            # return nothing. That is a configuration problem, not a formatting
            # one, so it is reported as such rather than as a missing block.
            if not (response.content or "").strip() and response.reasoning:
                outcome.attempts.append(
                    Attempt(
                        index,
                        False,
                        "model produced only a reasoning trace and no answer; "
                        "disable thinking for structured output",
                    )
                )
                break
            try:
                replacements = parse_replacements(response.content or "")
            except PatchError as exc:
                outcome.attempts.append(
                    Attempt(index, False, str(exc), response=excerpt(response.content))
                )
                feedback.append(f"attempt {index + 1}: {exc}")
                continue
            try:
                for replacement in replacements:
                    current, _edit = apply_replacement(current, replacement)
            except PatchError as exc:
                outcome.attempts.append(Attempt(index, False, str(exc)))
                feedback.append(f"attempt {index + 1}: {exc}")
                continue
            remaining = analyze(current, path=case.origin)
            if not remaining:
                outcome.solved = True
                outcome.attempts.append(Attempt(index, True))
                break
            codes = tuple(d.code for d in remaining)
            outcome.attempts.append(Attempt(index, True, remaining=codes))
            feedback.append(
                f"attempt {index + 1} applied but the program still failed with "
                + ", ".join(codes)
            )
        self.outcomes.append(outcome)
        return current, outcome

    def __call__(self, case: CorpusCase) -> str:
        """Benchmark solver seam: return the program this solver produced."""

        source, _outcome = self._repair(case)
        return source

    def repair(self, case: CorpusCase) -> RepairOutcome:
        """Repair one case and return the detailed outcome.

        Implemented through the callable seam so the two entry points cannot
        drift, and so calling one does not run the provider twice.
        """

        self(case)
        return self.outcomes[-1]


__all__ = [
    "DIVIDER_MARKER",
    "DIAGNOSTIC_GUIDE",
    "MAX_INSERTED_LINES",
    "MAX_REPLACEMENTS",
    "Attempt",
    "ModelRepairSolver",
    "PatchError",
    "REPLACE_MARKER",
    "RepairOutcome",
    "RepairStrategy",
    "Replacement",
    "SEARCH_MARKER",
    "SYSTEM_PROMPT",
    "apply_replacement",
    "build_messages",
    "describe_diagnostic",
    "parse_replacements",
]
