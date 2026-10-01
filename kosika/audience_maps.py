"""Audience-aware project maps with explicit evidence and intent sources.

The module distinguishes checked declarations, component-contract claims,
validated execution observations, and human-authored purpose notes. It never
infers intent from identifiers or source structure.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .ast import Program
from .explain import ExplainError, summarize_trace
from .lexer import Token, lex
from .mindmap import build_mindmap
from .modules import load_program
from .schema import schema_for_record, schema_for_tool_input, schemas_equal

AUDIENCE_MAP_SCHEMA = "kosika.audience-map/v1"
AUDIENCES = ("outsider", "overview", "operator", "reviewer", "maintainer")
EVIDENCE_CHECKED = "checked"
EVIDENCE_DECLARED = "declared"
EVIDENCE_OBSERVED = "observed"
EVIDENCE_HUMAN = "human"
EVIDENCE_CLASSES = (
    EVIDENCE_CHECKED,
    EVIDENCE_DECLARED,
    EVIDENCE_OBSERVED,
    EVIDENCE_HUMAN,
)
MAX_PURPOSE_NOTES = 20
MAX_PURPOSE_NOTE_CHARS = 1000


class AudienceMapError(ValueError):
    """Raised when an audience map cannot be built safely."""


@dataclass(frozen=True)
class AudienceEntry:
    id: str
    kind: str
    label: str
    evidence: tuple[str, ...]
    source: str
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "evidence": list(self.evidence),
            "source": self.source,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class AudienceEdge:
    source: str
    target: str
    relation: str
    evidence: str

    def to_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "target": self.target,
            "relation": self.relation,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class AudienceMap:
    audience: str
    question: str
    project: Mapping[str, str]
    entries: tuple[AudienceEntry, ...]
    edges: tuple[AudienceEdge, ...]
    coverage: Mapping[str, int]
    evidence_counts: Mapping[str, int]
    evidence_limits: tuple[str, ...] = ()
    digest: str = ""
    notice: str = ""

    def entries_with(self, evidence: str) -> tuple[AudienceEntry, ...]:
        return tuple(entry for entry in self.entries if evidence in entry.evidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": AUDIENCE_MAP_SCHEMA,
            "audience": self.audience,
            "question": self.question,
            "project": dict(self.project),
            "entries": [entry.to_dict() for entry in self.entries],
            "edges": [edge.to_dict() for edge in self.edges],
            "coverage": dict(self.coverage),
            "evidence_counts": dict(self.evidence_counts),
            "evidence_limits": list(self.evidence_limits),
            "digest": self.digest,
            "notice": self.notice,
        }


# The profiles differ by the nodes/details relevant to each reader.
_PROFILE_QUESTION = {
    "outsider": "What is this system for, and what does it connect to?",
    "overview": "What does this system do, and what does it reach outward to?",
    "operator": "What ran, in what order, through which bindings?",
    "reviewer": "What authority does this program hold, and what was observed?",
    "maintainer": "What is declared where, and how does it compose?",
}
_KIND_FILTERS = {
    "outsider": {
        "module",
        "workflow",
        "model",
        "tool",
        "agent",
        "swarm",
        "component",
    },
    "overview": {
        "module",
        "workflow",
        "record",
        "model",
        "tool",
        "agent",
        "swarm",
        "component",
    },
    "operator": {
        "module",
        "workflow",
        "step",
        "model",
        "tool",
        "agent",
        "swarm",
        "function",
        "component",
    },
    "reviewer": {"module", "workflow", "tool", "agent", "swarm", "model", "component"},
    "maintainer": {
        "module",
        "record",
        "field",
        "function",
        "workflow",
        "step",
        "model",
        "tool",
        "agent",
        "swarm",
        "component",
    },
}
_AUTHORITY_AUDIENCES = {"outsider", "overview", "reviewer", "maintainer"}
_TRACE_AUDIENCES = {"operator", "reviewer"}
_DECLARATION_KEYWORDS = {
    "record": "record",
    "workflow": "workflow",
    "function": "fn",
    "tool": "tool",
    "model": "model",
    "agent": "agent",
    "swarm": "swarm",
}
_CALL_KINDS = {
    "workflow_call": "workflow",
    "model_call": "model",
    "tool_call": "tool",
    "agent_call": "agent",
    "function_call": "function",
}


def _program_hash(program: Program) -> str:
    return hashlib.sha256(
        json.dumps(asdict(program), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _block_end(tokens: list[Token], opening: int) -> int | None:
    depth = 0
    for index in range(opening, len(tokens)):
        if tokens[index].value == "{":
            depth += 1
        elif tokens[index].value == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _named_block(
    tokens: list[Token],
    keyword: str,
    name: str,
    *,
    start: int = 0,
    end: int | None = None,
) -> tuple[int, int, int] | None:
    stop = len(tokens) if end is None else min(end, len(tokens))
    for index in range(start, max(start, stop - 1)):
        if (
            tokens[index].kind != "IDENT"
            or tokens[index].value != keyword
            or tokens[index + 1].kind != "IDENT"
            or tokens[index + 1].value != name
        ):
            continue
        opening = next(
            (
                offset
                for offset in range(index + 2, stop)
                if tokens[offset].value == "{"
            ),
            None,
        )
        if opening is None:
            return None
        closing = _block_end(tokens, opening)
        if closing is not None and closing < stop:
            return index, opening + 1, closing
    return None


def _owner_block(
    tokens: list[Token], keyword: str, name: str
) -> tuple[int, int] | None:
    found = _named_block(tokens, keyword, name)
    return (found[1], found[2]) if found is not None else None


def _source_line(
    tokens: list[Token], kind: str, label: str, attributes: Mapping[str, Any]
) -> int | None:
    if kind == "module":
        return 1
    if kind in _DECLARATION_KEYWORDS:
        keyword = _DECLARATION_KEYWORDS[kind]
        qualified_name = label.split("/")
        block_start = 0
        block_end = len(tokens)
        declaration = None
        for name in qualified_name:
            declaration = _named_block(
                tokens,
                keyword,
                name,
                start=block_start,
                end=block_end,
            )
            if declaration is None:
                return None
            _, block_start, block_end = declaration[0], declaration[1], declaration[2]
        return tokens[declaration[0] + 1].line if declaration is not None else None
    if kind == "step":
        workflow = attributes.get("workflow")
        step_name = label.split(". ", 1)[-1]
        if not isinstance(workflow, str):
            return None
        owner = _owner_block(tokens, "workflow", workflow)
        if owner is None:
            return None
        start, end = owner
        for index in range(start, max(start, end - 1)):
            if (
                tokens[index].kind == "IDENT"
                and tokens[index].value == "step"
                and tokens[index + 1].kind == "IDENT"
                and tokens[index + 1].value == step_name
            ):
                return tokens[index + 1].line
        return None
    if kind == "field":
        record = attributes.get("record")
        if not isinstance(record, str):
            return None
        owner = _owner_block(tokens, "record", record)
        if owner is None:
            return None
        start, end = owner
        for index in range(start, max(start, end - 1)):
            if (
                tokens[index].kind == "IDENT"
                and tokens[index].value == label
                and tokens[index + 1].value == ":"
            ):
                return tokens[index].line
        return None
    return None


def _source_link(
    module: str,
    kind: str,
    label: str,
    root: Path,
    *,
    attributes: Mapping[str, Any] | None = None,
    source_overrides: Mapping[Path, str] | None = None,
) -> str:
    if module == "<memory>":
        return module
    try:
        path = (root / module).resolve(strict=True)
        path.relative_to(root)
        override = next(
            (
                text
                for override_path, text in (source_overrides or {}).items()
                if override_path.resolve() == path
            ),
            None,
        )
        if override is None:
            if path.stat().st_size > 1024 * 1024:
                return module
            source = path.read_text(encoding="utf-8")
        else:
            source = override
        tokens = lex(source)
    except (OSError, UnicodeError, ValueError):
        return module
    line = _source_line(tokens, kind, label, attributes or {})
    return f"{module}:{line}" if line is not None else module


def _merge(entries: Iterable[AudienceEntry]) -> tuple[AudienceEntry, ...]:
    merged: dict[str, AudienceEntry] = {}
    for entry in entries:
        previous = merged.get(entry.id)
        if previous is None:
            merged[entry.id] = entry
            continue
        evidence = tuple(
            item
            for item in EVIDENCE_CLASSES
            if item in set(previous.evidence) | set(entry.evidence)
        )
        detail = dict(previous.detail)
        if entry.kind == "purpose":
            detail.update(entry.detail)
        elif EVIDENCE_OBSERVED in entry.evidence:
            observations = list(detail.get("observations", []))
            observations.append(dict(entry.detail))
            detail["observations"] = observations
        merged[entry.id] = AudienceEntry(
            id=previous.id,
            kind=previous.kind,
            label=previous.label,
            evidence=evidence,
            source=previous.source,
            detail=detail,
        )
    return tuple(
        sorted(
            merged.values(),
            key=lambda item: (EVIDENCE_CLASSES.index(item.evidence[0]), item.id),
        )
    )


def _digest(entries: tuple[AudienceEntry, ...], edges: tuple[AudienceEdge, ...]) -> str:
    payload = {
        "entries": [entry.to_dict() for entry in entries],
        "edges": [edge.to_dict() for edge in edges],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False).encode(
            "utf-8"
        )
    ).hexdigest()


def build_audience_map(
    source: str | Path,
    *,
    audience: str,
    root: str | Path | None = None,
    trace_path: str | Path | None = None,
    contracts: Iterable[Any] = (),
    purpose: Iterable[str] = (),
    source_overrides: Mapping[Path, str] | None = None,
) -> AudienceMap:
    """Build an evidence-labelled map tailored to one audience."""

    if audience not in _PROFILE_QUESTION:
        raise AudienceMapError(
            f"Unknown audience {audience!r}; expected one of {', '.join(AUDIENCES)}"
        )
    entry_path = Path(source).resolve()
    boundary = Path(root).resolve() if root else entry_path.parent
    overrides = {
        Path(path).resolve(): text for path, text in (source_overrides or {}).items()
    }
    try:
        program = load_program(entry_path, root=boundary, source_overrides=overrides)
    except (OSError, ValueError) as exc:
        raise AudienceMapError(f"Could not load project for mapping: {exc}") from exc

    base_map = build_mindmap(program)
    kinds = _KIND_FILTERS[audience]
    base_nodes = [node for node in base_map["nodes"] if node["kind"] in kinds]
    entries: list[AudienceEntry] = []
    for node in base_nodes:
        attrs = dict(node.get("attributes") or {})
        if audience == "outsider":
            for key in (
                "input",
                "output",
                "parameters",
                "fields",
                "binding",
                "binding_type",
                "return_name",
            ):
                attrs.pop(key, None)
        elif audience != "maintainer":
            for key in ("fields", "binding", "binding_type", "return_name"):
                attrs.pop(key, None)
        if audience not in _AUTHORITY_AUDIENCES:
            for key in (
                "access",
                "capabilities",
                "cannot",
                "approval_required",
                "source",
                "operation",
            ):
                attrs.pop(key, None)
        module = str(node["module"])
        kind = str(node["kind"])
        label = str(node["label"])
        entries.append(
            AudienceEntry(
                id=str(node["id"]),
                kind=kind,
                label=label,
                evidence=(EVIDENCE_CHECKED,),
                source=_source_link(
                    module,
                    kind,
                    label,
                    boundary,
                    attributes=attrs,
                    source_overrides=overrides,
                ),
                detail=attrs,
            )
        )
    checked_entry_ids = {entry.id for entry in entries}
    generic_output_edges: list[AudienceEdge] = []
    if audience == "outsider":
        workflow_sources = {
            entry.id: entry.source for entry in entries if entry.kind == "workflow"
        }
        for workflow in program.workflows:
            workflow_id = f"workflow:{workflow.name}"
            if workflow_id not in workflow_sources:
                continue
            result_id = f"result:{workflow.name}"
            entries.append(
                AudienceEntry(
                    id=result_id,
                    kind="result",
                    label="workflow result",
                    evidence=(EVIDENCE_CHECKED,),
                    source=workflow_sources[workflow_id],
                )
            )
            # Keep the checked output relationship visible without exposing the
            # record name or schema to the outsider profile.
            generic_output_edges.append(
                AudienceEdge(workflow_id, result_id, "output", EVIDENCE_CHECKED)
            )
    included = {entry.id for entry in entries}
    edges: list[AudienceEdge] = [
        AudienceEdge(
            str(edge["source"]),
            str(edge["target"]),
            str(edge["relation"]),
            EVIDENCE_CHECKED,
        )
        for edge in base_map["edges"]
        if edge["source"] in included and edge["target"] in included
    ]
    edges.extend(generic_output_edges)

    checked_hash = _program_hash(program)
    limits: list[str] = []
    if trace_path is not None:
        try:
            summary = summarize_trace(trace_path, expected_program_hash=checked_hash)
        except (ExplainError, OSError, ValueError) as exc:
            raise AudienceMapError(f"Trace could not be validated: {exc}") from exc
        if summary.get("program_hash_match") is not True:
            limit = (
                "trace-program-hash-mismatch"
                if summary.get("program_hash_match") is False
                else "trace-program-hash-unavailable"
            )
            limits.append(limit)
        elif audience not in _TRACE_AUDIENCES:
            limits.append("trace-details-not-included-for-audience")
        else:
            run_workflow = summary.get("workflow")
            trace_id = (
                f"run:{run_workflow}" if isinstance(run_workflow, str) else "run:unknown"
            )
            run_detail: dict[str, Any] = {
                "workflow": run_workflow,
                "eventCount": summary.get("event_count"),
            }
            if audience == "reviewer":
                run_detail["result"] = summary.get("result")
            entries.append(
                AudienceEntry(
                    id=trace_id,
                    kind="run",
                    label=f"run {run_workflow}" if run_workflow else "run",
                    evidence=(EVIDENCE_OBSERVED,),
                    source=f"trace:{summary.get('path')}",
                    detail=run_detail,
                )
            )
            if isinstance(run_workflow, str):
                edges.append(
                    AudienceEdge(
                        trace_id,
                        f"workflow:{run_workflow}",
                        "observed",
                        EVIDENCE_OBSERVED,
                    )
                )
            for step in summary.get("steps", []):
                if not isinstance(step, Mapping):
                    continue
                path = str(step.get("step") or "")
                kind = str(step.get("kind") or "")
                target = str(step.get("target") or "")
                if not path or not target:
                    continue
                step_id = f"observed:{path}"
                entries.append(
                    AudienceEntry(
                        id=step_id,
                        kind="step",
                        label=path,
                        evidence=(EVIDENCE_OBSERVED,),
                        source=f"trace:{summary.get('path')}",
                        detail={"call": kind, "target": target},
                    )
                )
                edges.append(
                    AudienceEdge(
                        trace_id, step_id, "observed-invokes", EVIDENCE_OBSERVED
                    )
                )
                binding_kind = _CALL_KINDS.get(kind)
                if binding_kind:
                    edges.append(
                        AudienceEdge(
                            step_id,
                            f"{binding_kind}:{target}",
                            "invoked",
                            EVIDENCE_OBSERVED,
                        )
                    )

    component_entries: list[AudienceEntry] = []
    for contract in contracts:
        tool = next(
            (item for item in program.tools if item.name == contract.tool_name), None
        )
        if (
            tool is None
            or tool.source != f"component:{contract.component_id}"
            or tool.operation != contract.operation
            or tool.access != contract.access
            or not schemas_equal(
                schema_for_tool_input(program, tool.input), contract.input_schema
            )
            or not schemas_equal(
                schema_for_record(program, tool.output), contract.output_schema
            )
        ):
            raise AudienceMapError(
                f"Component contract {contract.component_id!r} does not match a checked tool"
            )
        detail: dict[str, Any] = {
            "version": contract.version,
            "tool": contract.tool_name,
            "operation": contract.operation,
            "protocol": contract.implementation.protocol,
            "artifactSha256": contract.implementation.sha256,
        }
        if audience in _AUTHORITY_AUDIENCES:
            detail.update(
                {
                    "access": contract.access,
                    "effects": list(contract.effects),
                    "capabilities": list(contract.capabilities),
                }
            )
        component_id = f"component:{contract.component_id}"
        component_entries.append(
            AudienceEntry(
                id=component_id,
                kind="component",
                label=contract.component_id,
                evidence=(EVIDENCE_DECLARED,),
                source=f"contract:{contract.component_id}@{contract.version}",
                detail=detail,
            )
        )
        edges.append(
            AudienceEdge(
                component_id,
                f"tool:{contract.tool_name}",
                "implements",
                EVIDENCE_DECLARED,
            )
        )
    entries.extend(component_entries)

    notes = tuple(purpose)
    if len(notes) > MAX_PURPOSE_NOTES or any(
        not isinstance(note, str)
        or not note.strip()
        or len(note.strip()) > MAX_PURPOSE_NOTE_CHARS
        for note in notes
    ):
        raise AudienceMapError(
            "Purpose notes must be non-empty, at most 1000 characters, with no more than 20 notes"
        )
    normalized_notes = tuple(note.strip() for note in notes)
    if len(set(normalized_notes)) != len(normalized_notes):
        raise AudienceMapError("Duplicate human purpose notes are not allowed")
    try:
        entry_module = entry_path.relative_to(boundary).as_posix()
    except ValueError:
        entry_module = entry_path.name
    for index, note in enumerate(normalized_notes, 1):
        purpose_id = f"purpose:{index}"
        entries.append(
            AudienceEntry(
                purpose_id,
                "purpose",
                note,
                (EVIDENCE_HUMAN,),
                "human:purpose-note",
                {"independently_verified": False},
            )
        )
        edges.append(
            AudienceEdge(
                f"module:{entry_module}", purpose_id, "human-context", EVIDENCE_HUMAN
            )
        )
    if notes:
        limits.append("human-purpose-not-independently-verified")

    merged = _merge(entries)
    included = {entry.id for entry in merged}
    filtered_edges = tuple(
        sorted(
            {
                edge
                for edge in edges
                if edge.source in included and edge.target in included
            },
            key=lambda edge: (edge.source, edge.target, edge.relation, edge.evidence),
        )
    )
    counts = {
        name: sum(name in item.evidence for item in merged) for name in EVIDENCE_CLASSES
    }
    coverage = {
        "checked_total": len(base_map["nodes"]),
        "checked_shown": sum(item.id in checked_entry_ids for item in merged),
        "entries": len(merged),
        "edges": len(filtered_edges),
    }
    notice = (
        "Checked facts come from Kosika's type-checked structure. Component contracts "
        "are claims, not proof of component behavior. Observed facts come only from "
        "a completed trace with a matching program hash. Purpose notes are explicit "
        "human context and are not independently verified. No intent is inferred."
    )
    return AudienceMap(
        audience,
        _PROFILE_QUESTION[audience],
        {"root": str(boundary), "entry": entry_module},
        merged,
        filtered_edges,
        coverage,
        counts,
        tuple(limits),
        _digest(merged, filtered_edges),
        notice,
    )


def verify(audience_map: AudienceMap) -> dict[str, Any]:
    problems: list[str] = []
    ids = {entry.id for entry in audience_map.entries}
    if len(ids) != len(audience_map.entries):
        problems.append("duplicate entry ids")
    for entry in audience_map.entries:
        if not entry.evidence or any(
            item not in EVIDENCE_CLASSES for item in entry.evidence
        ):
            problems.append(f"entry {entry.id} has invalid evidence")
        if not entry.source:
            problems.append(f"entry {entry.id} has no source link")
        if EVIDENCE_HUMAN in entry.evidence and (
            entry.kind != "purpose"
            or entry.source != "human:purpose-note"
            or entry.detail.get("independently_verified") is not False
        ):
            problems.append(f"entry {entry.id} mislabels human evidence")
    for edge in audience_map.edges:
        if edge.evidence not in EVIDENCE_CLASSES:
            problems.append(f"edge {edge.source}->{edge.target} has invalid evidence")
        if edge.source not in ids or edge.target not in ids:
            problems.append(
                f"edge {edge.source}->{edge.target} references omitted entry"
            )
    rejected_trace_limits = {
        "trace-program-hash-mismatch",
        "trace-program-hash-unavailable",
    }
    if rejected_trace_limits.intersection(audience_map.evidence_limits) and (
        audience_map.entries_with(EVIDENCE_OBSERVED)
    ):
        problems.append("unmatched trace contributed observed entries")
    if _digest(audience_map.entries, audience_map.edges) != audience_map.digest:
        problems.append("map digest does not match contents")
    return {
        "schema": AUDIENCE_MAP_SCHEMA,
        "ok": not problems,
        "problems": problems,
        "audience": audience_map.audience,
        "entries": len(audience_map.entries),
        "digest": audience_map.digest,
    }


def to_mermaid(audience_map: AudienceMap) -> str:
    def node_id(identifier: str) -> str:
        return "n_" + hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:16]

    def safe(value: str) -> str:
        return (
            value.replace("\r", " ")
            .replace("\n", " ")
            .replace('"', "&quot;")
            .replace("|", "&#124;")
        )

    lines = [
        "flowchart TD",
        f"    %% {audience_map.audience}: {safe(audience_map.question)}",
    ]
    for entry in audience_map.entries:
        evidence = "+".join(entry.evidence)
        lines.append(
            f'    {node_id(entry.id)}["{safe(entry.kind + ": " + entry.label)} [{evidence}] - {safe(entry.source)}"]'
        )
    for edge in audience_map.edges:
        lines.append(
            f"    {node_id(edge.source)} -->|{safe(edge.relation)} [{edge.evidence}]| {node_id(edge.target)}"
        )
    return "\n".join(lines) + "\n"


def to_text(audience_map: AudienceMap) -> str:
    lines = [
        f"Audience: {audience_map.audience}",
        f"Question: {audience_map.question}",
        f"Project: {audience_map.project['entry']}",
        f"Coverage: {audience_map.coverage['checked_shown']} of {audience_map.coverage['checked_total']} checked entries shown",
        "Evidence counts: "
        + ", ".join(
            f"{name}={count}" for name, count in audience_map.evidence_counts.items()
        ),
    ]
    purpose = [entry for entry in audience_map.entries if entry.kind == "purpose"]
    if purpose:
        lines.append("Purpose (human-provided; not independently verified):")
        lines.extend(f"- {entry.label}" for entry in purpose)
    if audience_map.evidence_limits:
        lines.append("Evidence limits: " + ", ".join(audience_map.evidence_limits))
    lines.append("Map entries:")
    for entry in audience_map.entries:
        if entry.kind != "purpose":
            lines.append(
                f"- [{'+'.join(entry.evidence)}] {entry.kind}: {entry.label} ({entry.source})"
            )
            if entry.kind == "run" and "result" in entry.detail:
                rendered_result = json.dumps(
                    entry.detail["result"],
                    sort_keys=True,
                    ensure_ascii=False,
                    allow_nan=False,
                )
                lines.append(f"  Recorded result (observed): {rendered_result}")
    if audience_map.edges:
        lines.append("Relationships:")
        lines.extend(
            f"- {edge.source} --{edge.relation} [{edge.evidence}]--> {edge.target}"
            for edge in audience_map.edges
        )
    lines.extend(("", audience_map.notice))
    return "\n".join(lines)


__all__ = [
    "AUDIENCES",
    "AUDIENCE_MAP_SCHEMA",
    "AudienceEdge",
    "AudienceEntry",
    "AudienceMap",
    "AudienceMapError",
    "EVIDENCE_CHECKED",
    "EVIDENCE_CLASSES",
    "EVIDENCE_DECLARED",
    "EVIDENCE_HUMAN",
    "EVIDENCE_OBSERVED",
    "build_audience_map",
    "to_mermaid",
    "to_text",
    "verify",
]
