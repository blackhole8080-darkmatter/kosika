"""Build a repair corpus from the repository's own Kosika sources.

A model can only learn to *repair* Kosika if it has seen failures paired with
the right fix. This module manufactures that supervision from material the
repository already has: valid example programs, and a checker that reports each
failure as a structured diagnostic.

The central design rule is that **a mutation never predicts its own label**.
A mutation operator proposes an edit; the checker then runs, and whatever
diagnostic it *actually* reports becomes the ground truth recorded for that
case. If a mutation turns out to be a no-op, the case is dropped rather than
recorded with an invented expectation. This is what keeps the corpus honest:
labels are observed tool output, not the mutation author's guess.

Each case therefore records the mutation *intent* and the *observed* code
separately. They frequently differ, and that difference is data rather than
error -- it records where the checker's real behaviour diverges from the
intent a reader would assume from the operator's name.

Only sources that currently analyse clean are mutated, so a recorded
diagnostic is unambiguously attributable to the mutation rather than
inherited from the original program.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .diagnostics import CODES, Diagnostic, analyze, report

CORPUS_SCHEMA = "kosika.corpus/v1"

#: Directories that hold Kosika sources worth harvesting.
CORPUS_ROOTS: tuple[str, ...] = ("examples",)

#: A declaration header runs from the keyword to its opening brace, whatever
#: sits between them. The header is not just a name: a function carries
#: ``name(params) -> Output`` and a tool carries ``source mcp "id"``, so the
#: pattern must tolerate a tail rather than requiring ``name {``.
_DECLARATION_RE = re.compile(
    r"^(?P<indent>\s*)(?P<kind>record|tool|agent|model|workflow|fn|swarm)"
    r"\s+(?P<name>\w+)[^{]*\{"
)
_FIELD_RE = re.compile(r"^(?P<indent>\s+)(?P<name>\w+)\s*:\s*(?P<type>[\w\[\]]+)\s*$")


def source_lines(source: str) -> list[str]:
    """Split a program into lines without discarding the trailing terminator.

    ``str.splitlines`` drops the final newline, so a program ending in a blank
    line does not survive a join/split round trip: ``"a\\n\\n"`` splits into two
    entries, but joining them writes a trailing separator, and the blank line
    reappears on the next split. Splitting on the separator instead makes
    ``"\\n".join(source_lines(x)) == x`` exact, which is what lets a stored
    repair restore a program byte for byte rather than approximately.
    """

    return source.split("\n")


@dataclass(frozen=True)
class Declaration:
    """One top-level declaration located by line."""

    kind: str
    name: str
    line: int
    end_line: int
    body: tuple[str, ...]


@dataclass(frozen=True)
class CorpusCase:
    """One corpus entry: a program and the diagnostic it is expected to raise.

    ``diagnostic`` is ``None`` for a valid program, which is a first-class case
    rather than an absence: knowing when *not* to change anything is part of
    the behaviour being taught.
    """

    case_id: str
    origin: str
    mutation: str
    source: str
    diagnostic: Mapping[str, Any] | None = None
    intended: frozenset[str] | None = None
    matches_intent: bool | None = None
    fix_source: str | None = None
    fix_edit: Mapping[str, Any] | None = None

    @property
    def is_valid(self) -> bool:
        return self.diagnostic is None

    @property
    def is_repairable(self) -> bool:
        """True when the case carries a reference repair."""

        return self.fix_source is not None

    @property
    def code(self) -> str | None:
        return None if self.diagnostic is None else self.diagnostic["code"]

    @property
    def is_mislabelled(self) -> bool:
        """True when the observed code contradicts the operator's claim.

        Such a case is usually a broken operator rather than a language
        surprise, and is exactly the kind of defect that would teach a model
        something false about the language.
        """

        return self.matches_intent is False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.case_id,
            "origin": self.origin,
            "mutation": self.mutation,
            "language": "kosika",
            "valid": self.is_valid,
            "source": self.source,
            "expected_diagnostic": self.diagnostic,
            "intended_codes": sorted(self.intended) if self.intended else None,
            "matches_intent": self.matches_intent,
            "fix_source": self.fix_source,
            "fix_edit": dict(self.fix_edit) if self.fix_edit else None,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


@dataclass
class _Builder:
    """Accumulates cases and tracks which codes the corpus exercises."""

    cases: list[CorpusCase] = field(default_factory=list)
    seen_codes: set[str] = field(default_factory=set)
    discarded: int = 0
    multi_diagnostic: int = 0

    def record(
        self,
        case_id: str,
        origin: str,
        mutation: Mutation,
        original: str,
        diagnostics: Sequence[Diagnostic],
    ) -> None:
        """Record a mutated program, keeping it only if it fails cleanly.

        A mutation that still analyses clean is a no-op and is dropped: keeping
        it would teach "this edit changes nothing", which is noise. A mutation
        that raises several errors is dropped too, because the corpus labels a
        single expected failure and an ambiguous case would teach the wrong
        thing.

        The reference repair is the inverse of the mutation, so it is always
        available and always restores a program that checks clean.
        """

        if not diagnostics:
            self.discarded += 1
            return
        if len(diagnostics) > 1:
            self.multi_diagnostic += 1
            self.discarded += 1
            return
        diagnostic = diagnostics[0]
        code = diagnostic.code
        self.seen_codes.add(code)
        fix_source, fix_edit = mutation.repair(original)
        self.cases.append(
            CorpusCase(
                case_id=case_id,
                origin=origin,
                mutation=mutation.intent,
                source=mutation.source,
                diagnostic=diagnostic.to_dict(),
                intended=mutation.intended,
                matches_intent=mutation.matches(code),
                fix_source=fix_source,
                fix_edit=fix_edit.to_dict(),
            )
        )


# ---------------------------------------------------------------------------
# Declaration discovery
# ---------------------------------------------------------------------------


def declarations(source: str) -> list[Declaration]:
    """Locate every top-level declaration and its body lines.

    A deliberately small line scanner rather than a full parse: it only needs
    declaration headers and brace depth, and it keeps this module independent
    of AST shape changes elsewhere in the project.
    """

    lines = source_lines(source)
    found: list[Declaration] = []
    index = 0
    while index < len(lines):
        match = _DECLARATION_RE.match(lines[index])
        if match is None:
            index += 1
            continue
        depth = lines[index].count("{") - lines[index].count("}")
        end = index
        while end + 1 < len(lines) and depth > 0:
            end += 1
            depth += lines[end].count("{") - lines[end].count("}")
        found.append(
            Declaration(
                kind=match.group("kind"),
                name=match.group("name"),
                line=index,
                end_line=end,
                body=tuple(lines[index + 1 : end]),
            )
        )
        index = end + 1
    return found


def _find_line(
    lines: Sequence[str], pattern: re.Pattern[str], start: int, end: int
) -> int | None:
    for index in range(start, end + 1):
        if pattern.search(lines[index]):
            return index
    return None


# ---------------------------------------------------------------------------
# Mutation operators
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Edit:
    """A line-oriented patch.

    Modelled as "replace ``removed`` lines at ``start`` with ``inserted``" so
    that an insertion, a deletion, and a replacement are one uniform shape. The
    inverse of an edit is always computable, which is what lets the corpus
    store a reference repair as a small patch rather than a whole second copy
    of the program.
    """

    start: int
    removed: int
    inserted: tuple[str, ...] = ()

    def apply(self, lines: Sequence[str]) -> list[str]:
        return [
            *lines[: self.start],
            *self.inserted,
            *lines[self.start + self.removed :],
        ]

    def revert(self, lines: Sequence[str]) -> "Edit":
        """Return the edit that undoes this one against ``lines``.

        ``lines`` are the lines this edit was applied to.
        """

        return Edit(
            start=self.start,
            removed=len(self.inserted),
            inserted=tuple(lines[self.start : self.start + self.removed]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "removed": self.removed,
            "inserted": list(self.inserted),
        }


@dataclass(frozen=True)
class Mutation:
    """A proposed edit, plus the diagnostic codes it is meant to provoke.

    ``intended`` is the operator author's claim about the outcome. It exists so
    that a broken operator cannot quietly poison the corpus: when the observed
    code falls outside ``intended``, the case is recorded but flagged, and the
    mismatch is reported. An operator that genuinely cannot predict its
    outcome -- syntax damage in particular -- declares ``None`` and is exempt.

    Without this, an operator that corrupts the source instead of editing it
    yields a confident, correct-looking label describing the operator's own
    bug, which is worse than having no case at all.
    """

    intent: str
    source: str
    edit: Edit
    intended: frozenset[str] | None = None

    def matches(self, code: str | None) -> bool | None:
        if self.intended is None or code is None:
            return None
        return code in self.intended

    def repair(self, original: str) -> tuple[str, Edit]:
        """Return the fixed source and the edit that produces it.

        The repair is the inverse of this mutation, so applying it to the
        mutated source restores the original program exactly.
        """

        original_lines = source_lines(original)
        inverse = self.edit.revert(original_lines)
        fixed = "\n".join(inverse.apply(source_lines(self.source)))
        return fixed, inverse


def _replace(
    lines: Sequence[str], start: int, end: int, replacement: Sequence[str]
) -> tuple[str, Edit]:
    """Splice ``replacement`` over lines ``start``..``end`` and describe the edit.

    Returns the new source together with the :class:`Edit` that produced it, so
    an operator can never record a mutation whose repair is not derivable.
    """

    edit = Edit(start=start, removed=end - start + 1, inserted=tuple(replacement))
    return "\n".join(edit.apply(lines)), edit


def _mutate(
    intent: str,
    lines: Sequence[str],
    start: int,
    end: int,
    replacement: Sequence[str],
    intended: frozenset[str] | None,
) -> Mutation:
    """Build a mutation together with the edit that produced it.

    Every operator goes through here, so no case can enter the corpus without a
    derivable repair.
    """

    mutated, edit = _replace(lines, start, end, replacement)
    return Mutation(intent, mutated, edit, intended)


def _record_operators(decl: Declaration, lines: Sequence[str]) -> Iterator[Mutation]:
    # Each entry is an offset plus the field it matched, so a matched field is
    # always available: the list is filtered on the match succeeding.
    fields = [
        (offset, matched)
        for offset, body in enumerate(decl.body)
        if (matched := _FIELD_RE.match(body)) is not None
    ]
    if not fields:
        return

    # Dropping or adding a field surfaces wherever the record is constructed,
    # and which error that is depends on the construct: a record literal, a
    # tool argument, or a field access.
    literal_codes = frozenset(
        {
            "check/missing-field",
            "check/unknown-field",
            "check/missing-argument",
            "check/unknown-argument",
            "check/field-access",
        }
    )

    # Drop a field: any literal constructing this record then misses it.
    offset, _match = fields[-1]
    target = decl.line + 1 + offset
    yield _mutate("record.drop-field", lines, target, target, [], literal_codes)

    # Add a field that no record declares, so construction rejects it. The
    # existing field is kept: replacing it would make this operator
    # indistinguishable from dropping the field.
    last_offset, last_match = fields[-1]
    last_line = decl.line + 1 + last_offset
    yield _mutate(
        "record.add-unknown-field",
        lines,
        last_line,
        last_line,
        [lines[last_line], f"{last_match.group('indent')}zz_undeclared: Text"],
        literal_codes,
    )

    # Duplicate a field declaration.
    offset, _match = fields[0]
    line = decl.line + 1 + offset
    yield _mutate(
        "record.duplicate-field",
        lines,
        line,
        line,
        [lines[line], lines[line]],
        frozenset({"check/duplicate-field"}),
    )

    # Retype a field so every use of it mismatches. The precise error depends
    # on how the field is used: compared, summed, or iterated.
    offset, match = fields[0]
    line = decl.line + 1 + offset
    retype = "Number" if match.group("type") != "Number" else "Text"
    yield _mutate(
        "record.retype-field",
        lines,
        line,
        line,
        [f"{match.group('indent')}{match.group('name')}: {retype}"],
        frozenset(
            {
                "check/type-mismatch",
                "check/operator-type-mismatch",
                "check/iteration-type",
                "check/field-access",
            }
        ),
    )


def _tool_operators(decl: Declaration, lines: Sequence[str]) -> Iterator[Mutation]:
    start, end = decl.line, decl.end_line
    # Removing a tool declaration leaves every declared agent and call site
    # referring to something that no longer exists.
    if end > start:
        yield from _drop_declaration(decl, lines)
    access = _find_line(lines, re.compile(r"^\s*access\s*:"), start, end)
    if access is not None:
        # An unrecognised access level is rejected outright.
        yield _mutate(
            "tool.invalid-access",
            lines,
            access,
            access,
            ["  access: superuser"],
            frozenset({"check/invalid-tool"}),
        )
    output = _find_line(lines, re.compile(r"^\s*output\s*:"), start, end)
    if output is not None:
        yield _mutate(
            "tool.unknowable-output",
            lines,
            output,
            output,
            ["  output: MissingRecord"],
            frozenset({"check/unknown-record", "check/unknown-type"}),
        )


def _agent_operators(decl: Declaration, lines: Sequence[str]) -> Iterator[Mutation]:
    start, end = decl.line, decl.end_line
    model = _find_line(lines, re.compile(r"^\s*model\s*:"), start, end)
    if model is not None:
        yield _mutate(
            "agent.unknown-model",
            lines,
            model,
            model,
            ["  model: no_such_model"],
            frozenset({"check/unknown-model"}),
        )
    can = _find_line(lines, re.compile(r"^\s*can\s*:"), start, end)
    if can is not None:
        granted = re.findall(r"\w+", lines[can].split(":", 1)[1])
        if granted:
            deny = _find_line(lines, re.compile(r"^\s*cannot\s*:"), start, end)
            # Granting a tool the agent also forbids is a contradictory policy.
            # The conflicting entry must be a tool the agent really holds: a
            # `cannot` list typically names capabilities that were never
            # declared, and granting one of those fails as an unknown tool long
            # before the conflict is reached.
            if deny is not None:
                yield _mutate(
                    "agent.allow-and-deny",
                    lines,
                    deny,
                    deny,
                    [f"  cannot: [{granted[0]}]"],
                    frozenset({"check/agent-conflict"}),
                )
            # Withdrawing every granted capability makes the declared tool call
            # a capability violation, which is the failure mode that matters
            # most for an editing agent.
            yield _mutate(
                "agent.revoke-capability",
                lines,
                can,
                can,
                ["  can: []"],
                frozenset({"check/capability-denied"}),
            )
    steps = _find_line(lines, re.compile(r"^\s*max_steps\s*:"), start, end)
    if steps is not None:
        yield _mutate(
            "agent.step-limit-out-of-range",
            lines,
            steps,
            steps,
            ["  max_steps: 9999"],
            frozenset({"check/invalid-limit"}),
        )


def _model_operators(decl: Declaration, lines: Sequence[str]) -> Iterator[Mutation]:
    start, end = decl.line, decl.end_line
    for key, value in (
        ("temperature", "temperature: 9.5"),
        ("top_p", "top_p: 4.5"),
        ("max_output_tokens", "max_output_tokens: 0"),
    ):
        line = _find_line(lines, re.compile(rf"^\s*{key}\s*:"), start, end)
        if line is not None:
            yield _mutate(
                f"model.{key.replace('_', '-')}-out-of-range",
                lines,
                line,
                line,
                [f"      {value}"],
                frozenset({"check/invalid-limit"}),
            )
    deployment = _find_line(lines, re.compile(r"^\s*deployment\s*:"), start, end)
    if deployment is not None:
        yield _mutate(
            "model.empty-deployment",
            lines,
            deployment,
            deployment,
            ['  deployment: ""'],
            frozenset({"check/invalid-model"}),
        )


def _workflow_operators(decl: Declaration, lines: Sequence[str]) -> Iterator[Mutation]:
    steps = [
        offset
        for offset, body in enumerate(decl.body)
        if re.match(r"^\s*step\s+\w+\s*\{", body)
    ]
    if not steps:
        return
    first_line = decl.line + 1 + steps[0]
    first = re.match(r"^(\s*step\s+)(\w+)", lines[first_line])
    if len(steps) >= 2 and first is not None:
        # Rename the second step onto the first step's name. Renaming a step to
        # something new is a no-op in Kosika, because step names are labels
        # rather than referenced bindings.
        second_line = decl.line + 1 + steps[1]
        second = re.match(r"^(\s*step\s+)(\w+)", lines[second_line])
        if second is not None:
            # Replace only the name span. Rebuilding the whole line would drop
            # the opening brace and turn a duplicate-name error into a
            # spurious parse error.
            renamed = (
                lines[second_line][: second.start(2)]
                + first.group(2)
                + lines[second_line][second.end(2) :]
            )
            yield _mutate(
                "workflow.duplicate-step-name",
                lines,
                second_line,
                second_line,
                [renamed],
                frozenset({"check/empty-workflow"}),
            )
    # Removing every step leaves a workflow with nothing to do. A swarm that
    # names the workflow is reported first, so that outcome is included.
    block = list(range(decl.line + 1 + steps[0], decl.end_line))
    if block:
        yield _mutate(
            "workflow.drop-all-steps",
            lines,
            block[0],
            block[-1],
            [],
            frozenset({"check/empty-workflow", "check/invalid-swarm"}),
        )
    # A step that never returns cannot supply the workflow's result. Kosika's
    # parser requires a trailing result expression in a step, so this surfaces
    # as a syntax failure rather than a checker diagnostic.
    returns = [
        offset
        for offset, body in enumerate(decl.body)
        if re.match(r"^\s*return\b", body)
    ]
    if returns:
        target = decl.line + 1 + returns[-1]
        yield _mutate(
            "workflow.step-without-return",
            lines,
            target,
            target,
            [],
            frozenset({"parse/expected-token", "check/missing-return"}),
        )


def _drop_declaration(decl: Declaration, lines: Sequence[str]) -> Iterator[Mutation]:
    """Delete a whole declaration, so every reference to it becomes unknown.

    This is the broadest single operator: one edit reaches the unknown-record,
    unknown-type, unknown-function, unknown-tool and unknown-model families
    depending on what referred to it.
    """

    if decl.end_line > decl.line:
        yield _mutate(
            f"declaration.drop-{decl.kind}",
            lines,
            decl.line,
            decl.end_line,
            [],
            frozenset(
                {
                    "check/unknown-function",
                    "check/unknown-record",
                    "check/unknown-type",
                    "check/unknown-tool",
                    "check/unknown-model",
                    "check/unknown-agent",
                    "check/unknown-workflow",
                }
            ),
        )


def _fn_operators(decl: Declaration, lines: Sequence[str]) -> Iterator[Mutation]:
    start, end = decl.line, decl.end_line
    # Narrowing a mutable binding to an immutable one makes the later
    # assignment to it illegal.
    var_line = _find_line(lines, re.compile(r"^(\s*)var\s+(\w+)"), start, end)
    if var_line is not None:
        match = re.match(r"^(\s*)var(\s+\w+.*)$", lines[var_line])
        if match is not None:
            yield _mutate(
                "fn.freeze-mutable-binding",
                lines,
                var_line,
                var_line,
                [f"{match.group(1)}let{match.group(2)}"],
                frozenset({"check/assignment-to-immutable"}),
            )
    # Note: re-declaring a local is *not* a shadowing case. Kosika reports it
    # as rebinding an immutable binding, and a genuinely shadowed binding needs
    # a nested block, which the corpus's examples do not contain. An earlier
    # version of this operator tried to force it and produced a mix of rebind
    # and syntax errors, so it was removed rather than relabelled.
    # Dropping the final match arm leaves the match non-exhaustive.
    arm = _find_line(lines, re.compile(r"^\s*case\b|^\s*_"), start, end)
    if arm is None:
        arm = _find_line(lines, re.compile(r"^\s*\d+\s*\{"), start, end)
    if arm is not None:
        yield _mutate(
            "fn.non-exhaustive-match",
            lines,
            arm,
            arm,
            [],
            frozenset(
                {
                    "check/exhaustiveness",
                    "check/branch-type-mismatch",
                    "parse/expected-token",
                    "parse/invalid-declaration",
                }
            ),
        )


_OPERATORS = {
    "record": _record_operators,
    "tool": _tool_operators,
    "agent": _agent_operators,
    "model": _model_operators,
    "workflow": _workflow_operators,
    "fn": _fn_operators,
}


def mutations(source: str) -> Iterator[Mutation]:
    """Yield every mutation applicable to one valid program.

    Includes lexical and structural mutations that do not depend on any
    declaration, because syntax failures are a large share of what a learner
    meets first. Those declare no intended code: which syntax error truncation
    produces depends on where the cut lands.
    """

    lines = source_lines(source)
    for decl in declarations(source):
        operator = _OPERATORS.get(decl.kind)
        if operator is None:
            continue
        yield from operator(decl, lines)

    if lines:
        # A stray character is the most common first syntax error.
        yield _mutate(
            "lex.unexpected-character",
            lines,
            len(lines),
            len(lines) - 1,
            ["@@@"],
            frozenset({"lex/unexpected-character"}),
        )
        # Losing a closing brace unterminates the final declaration.
        for index in range(len(lines) - 1, -1, -1):
            if lines[index].strip() == "}":
                yield _mutate(
                    "parse.unbalanced-brace",
                    lines,
                    index,
                    index,
                    [lines[index].replace("}", "", 1)],
                    None,
                )
                break
        # Truncation is a realistic "file was cut short" failure.
        if len(lines) > 3:
            cut = len(lines) // 2
            yield _mutate("parse.truncated", lines, cut, len(lines) - 1, [], None)


# ---------------------------------------------------------------------------
# Corpus assembly
# ---------------------------------------------------------------------------


def corpus_sources(root: Path) -> list[Path]:
    """Every Kosika source under the corpus roots, in stable order."""

    found: list[Path] = []
    for relative in CORPUS_ROOTS:
        base = root / relative
        if base.is_dir():
            found.extend(sorted(base.rglob("*.kos")))
    return found


def build_corpus(root: Path | None = None) -> list[CorpusCase]:
    """Build the corpus, including a valid case for every clean source."""

    base = root or Path(__file__).resolve().parent.parent
    builder = _Builder()
    for path in corpus_sources(base):
        origin = path.relative_to(base).as_posix()
        source = path.read_text(encoding="utf-8")
        if analyze(source, path=origin):
            # A source that does not analyse clean is not valid supervision;
            # recording it would label a pre-existing failure as a mutation.
            continue
        builder.cases.append(
            CorpusCase(
                case_id=f"{origin}::valid",
                origin=origin,
                mutation="none",
                source=source,
                diagnostic=None,
            )
        )
        for index, mutation in enumerate(mutations(source)):
            if mutation.source == source:
                continue
            builder.record(
                case_id=f"{origin}::{mutation.intent}#{index}",
                origin=origin,
                mutation=mutation,
                original=source,
                diagnostics=analyze(mutation.source, path=origin),
            )
    return builder.cases


def corpus_summary(cases: Sequence[CorpusCase]) -> dict[str, Any]:
    """Summarise what the corpus covers, including the gaps."""

    codes = sorted({case.code for case in cases if case.code is not None})
    by_mutation: dict[str, int] = {}
    for case in cases:
        by_mutation[case.mutation] = by_mutation.get(case.mutation, 0) + 1
    mislabelled = [case for case in cases if case.is_mislabelled]
    return {
        "schema": CORPUS_SCHEMA,
        "cases": len(cases),
        "valid_cases": sum(1 for case in cases if case.is_valid),
        "repair_cases": sum(1 for case in cases if not case.is_valid),
        "repairable_cases": sum(1 for case in cases if case.is_repairable),
        "distinct_codes": len(codes),
        "registered_codes": len(CODES),
        "codes": codes,
        "uncovered_codes": sorted(set(CODES) - set(codes)),
        "mislabelled_cases": len(mislabelled),
        "mislabelled_by_mutation": sorted({case.mutation for case in mislabelled}),
        "by_mutation": dict(sorted(by_mutation.items())),
        "origins": sorted({case.origin for case in cases}),
    }


def to_jsonl(cases: Sequence[CorpusCase]) -> str:
    """Serialise the corpus as newline-delimited JSON."""

    return "".join(f"{case.to_json()}\n" for case in cases)


__all__ = [
    "CORPUS_SCHEMA",
    "CODES",
    "CorpusCase",
    "Declaration",
    "Edit",
    "Mutation",
    "build_corpus",
    "corpus_sources",
    "corpus_summary",
    "declarations",
    "mutations",
    "report",
    "source_lines",
    "to_jsonl",
]
