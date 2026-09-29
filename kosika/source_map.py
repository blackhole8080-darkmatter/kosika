"""Cross-language source maps for Python, JavaScript, and TypeScript.

A source map joins several :mod:`kosika.source_schema` analyses into one
deterministic graph so a reader who does not know Kosika can still see what a
program is made of and what it touches.

The map is structural. It never executes, imports, or transpiles foreign code.
Where a relationship cannot be established from syntax it is reported as
unresolved rather than guessed, and every node carries the analysis
``support_level`` that produced it.
"""

from __future__ import annotations

import hashlib
import os
from collections import Counter, deque
from pathlib import Path
from typing import Any

from .language_adapters import adapter_for_language, adapter_for_suffix
from .source_schema import (
    EDGE_RELATIONS,
    SOURCE_MAP_SCHEMA,
    SourceAdapterError,
    validate_source_analysis,
)

MAX_FILES = 2000
MAX_ENTRIES = 200_000
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_NODES = 20_000
MAX_EDGES = 80_000
MAX_REPORT_BYTES = 8 * 1024 * 1024
MAX_DOC_SUMMARY_CHARS = 160

#: Human-readable labels for a symbol kind, for reader-facing output.
KIND_LABELS: dict[str, str] = {
    "class": "class",
    "constant": "value",
    "enum": "enum",
    "function": "function",
    "interface": "interface",
    "method": "method",
    "module": "module",
    "struct": "struct",
    "type": "type",
}

#: How each source language names an import binding.
_IMPORT_KINDS = frozenset(
    {
        "import",
        "from_import",
        "named_import",
        "require",
        "side_effect_import",
        "re_export",
    }
)


class SourceMapError(ValueError):
    """Raised when a source map cannot be built within explicit limits."""


def _ignored(name: str) -> bool:
    return name.startswith(".") or name in {
        "node_modules",
        "__pycache__",
        "dist",
        "build",
        "target",
        "vendor",
        "venv",
    }


def _discover(root: Path) -> list[Path]:
    pending: deque[Path] = deque([root])
    found: list[Path] = []
    entries_seen = 0
    while pending:
        directory = pending.popleft()
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda item: item.name)
        except OSError as exc:
            raise SourceMapError(
                f"Unable to scan source directory {directory}: {exc}"
            ) from exc
        for entry in entries:
            entries_seen += 1
            if entries_seen > MAX_ENTRIES:
                raise SourceMapError(
                    f"Source root exceeds the {MAX_ENTRIES}-entry discovery limit"
                )
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                if not _ignored(entry.name):
                    pending.append(Path(entry.path))
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            path = Path(entry.path)
            if adapter_for_suffix(path.suffix) is None:
                continue
            if len(found) >= MAX_FILES:
                raise SourceMapError(
                    f"Source root has more than {MAX_FILES} supported files; "
                    "analyze a smaller directory"
                )
            found.append(path)
    return sorted(found, key=lambda item: item.relative_to(root).as_posix())


def _read(
    path: Path, relative: str, total: list[int], max_source_bytes: int
) -> str:
    try:
        with path.open("rb") as source_file:
            raw = source_file.read(max_source_bytes + 1)
    except OSError as exc:
        raise SourceMapError(f"Unable to read {relative}: {exc}") from exc
    if len(raw) > max_source_bytes:
        raise SourceMapError(f"File {relative} exceeds the per-file size limit")
    total[0] += len(raw)
    if total[0] > MAX_TOTAL_BYTES:
        raise SourceMapError(
            f"Source root exceeds the {MAX_TOTAL_BYTES}-byte total source limit"
        )
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceMapError(f"File {relative} is not UTF-8") from exc


def _analyze(
    source: str, relative: str, language: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return the analysis for one file, or a recorded failure. Never raises."""

    adapter = adapter_for_language(language)
    if adapter is None:
        return None, {
            "path": relative,
            "language": language,
            "reason": "No registered source adapter",
        }
    try:
        return adapter.analyze_source(source, path=relative), None
    except SourceAdapterError as exc:
        return None, {"path": relative, "language": language, "reason": str(exc)}


def _module_name(relative: str, language: str) -> str:
    parts = list(Path(relative).with_suffix("").parts)
    if parts and parts[-1] in {"__init__", "index", "mod"}:
        parts.pop()
    if language == "python":
        return ".".join(parts)
    return "/".join(parts)


def _leaf(callee: str) -> str:
    parts = [part for part in callee.replace(" ", "").split(".") if part]
    return parts[-1] if parts else callee


def build_source_map(root: str | Path) -> dict[str, Any]:
    """Build a deterministic cross-language source map for a directory tree."""

    try:
        base = Path(root).resolve(strict=True)
    except OSError as exc:
        raise SourceMapError(f"Source root is unavailable: {exc}") from exc
    if not base.is_dir():
        raise SourceMapError(f"Source root is not a directory: {base}")

    paths = _discover(base)
    total = [0]
    analyses: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    for path in paths:
        relative = path.relative_to(base).as_posix()
        adapter = adapter_for_suffix(path.suffix)
        assert adapter is not None
        language = adapter.language
        source = _read(path, relative, total, adapter.max_source_bytes)
        document, failure = _analyze(source, relative, language)
        if document is None:
            assert failure is not None
            failures.append(failure)
            continue
        validate_source_analysis(document)
        analyses[relative] = document

    nodes: dict[str, dict[str, Any]] = {}
    edges: set[tuple[str, str, str, str]] = set()

    def add_node(identifier: str, **attributes: Any) -> str:
        existing = nodes.get(identifier)
        if existing is None:
            if len(nodes) >= MAX_NODES:
                raise SourceMapError(
                    f"Source map exceeds {MAX_NODES} nodes; analyze a smaller root"
                )
            nodes[identifier] = {"id": identifier, **attributes}
        return identifier

    def connect(source: str, target: str, relation: str, label: str = "") -> None:
        if relation not in EDGE_RELATIONS:
            return
        edges.add((source, target, relation, label))

    # Module nodes come first so every symbol has a stable parent.
    for relative in sorted(analyses):
        document = analyses[relative]
        language = document["language"]
        module = _module_name(relative, language)
        module_id = f"module:{module}"
        add_node(
            module_id,
            kind="module",
            label=module,
            language=language,
            file=relative,
            start_line=1,
            end_line=document["source"]["line_count"],
            doc=None,
            support_level=document["support_level"],
            effects=[
                {
                    "callee": effect["callee"],
                    "line": effect["line"],
                    "kind": effect["kind"],
                    "file": relative,
                }
                for effect in document["observed_effects"]
                if effect.get("symbol") == "<module>"
            ],
        )
        document["_module_id"] = module_id

    # Index symbols so a call observation can be linked to a declaration.
    by_name: dict[str, list[str]] = {}
    for relative in sorted(analyses):
        document = analyses[relative]
        module_id = document["_module_id"]
        module = nodes[module_id]["label"]
        for symbol in document["symbols"]:
            name = symbol["name"]
            # The line number keeps repeated declarations of the same name in
            # one file distinct, which JavaScript and TypeScript both permit.
            identifier = add_node(
                f"symbol:{symbol['qualified_name']}@{module}:{symbol['start_line']}",
                kind=symbol["kind"],
                label=name,
                qualified_name=symbol["qualified_name"],
                language=document["language"],
                file=relative,
                module=module,
                start_line=symbol["start_line"],
                end_line=symbol["end_line"],
                doc=symbol.get("docstring"),
                async_=bool(symbol.get("async")),
                support_level=document["support_level"],
                effects=[
                    {
                        "callee": call["callee"],
                        "line": call["line"],
                        "kind": call["observed_effect"],
                        "file": relative,
                    }
                    for call in symbol["calls"]
                    if call.get("observed_effect")
                ],
            )
            by_name.setdefault(name, []).append(identifier)
            connect(module_id, identifier, "declares", symbol["kind"])

    # Link member symbols to their enclosing class.
    member_ids = {
        node["qualified_name"]: node["id"] for node in nodes.values() if node["kind"] == "class"
    }
    for identifier, node in list(nodes.items()):
        if node["kind"] != "method":
            continue
        parts = node["qualified_name"].split(".")
        if len(parts) < 2:
            continue
        owner_id = member_ids.get(".".join(parts[:-1]))
        if owner_id is not None:
            connect(owner_id, identifier, "member_of", node["label"])
            node["member_of"] = ".".join(parts[:-1])

    # Resolve call observations to declarations where a unique name matches.
    resolved = 0
    ambiguous: set[str] = set()
    unresolved: set[str] = set()
    for relative in sorted(analyses):
        document = analyses[relative]
        module = nodes[document["_module_id"]]["label"]
        for symbol in document["symbols"]:
            source_id = f"symbol:{symbol['qualified_name']}@{module}:{symbol['start_line']}"
            if source_id not in nodes:
                continue
            for call in symbol["calls"]:
                candidates = by_name.get(_leaf(call["callee"]), [])
                if len(candidates) == 1:
                    connect(source_id, candidates[0], "calls", call["callee"])
                    resolved += 1
                elif len(candidates) > 1:
                    ambiguous.add(call["callee"])
                else:
                    unresolved.add(call["callee"])

    for relative in sorted(analyses):
        document = analyses[relative]
        for item in document["imports"]:
            if item["kind"] not in _IMPORT_KINDS:
                continue
            target = _module_name(item["module"], document["language"])
            if not target:
                continue
            target_id = f"module:{target}"
            if target_id in nodes:
                connect(
                    document["_module_id"], target_id, "imports", item["module"]
                )

    languages = Counter(document["language"] for document in analyses.values())
    report = {
        "schema": SOURCE_MAP_SCHEMA,
        "analysis_mode": "bounded_cross_language_structure",
        "support_level": "structural_declarations_imports_and_call_edges",
        "project": {"name": base.name},
        "limits": {
            "max_files": MAX_FILES,
            "max_nodes": MAX_NODES,
            "max_edges": MAX_EDGES,
            "max_total_source_bytes": MAX_TOTAL_BYTES,
        },
        "summary": {
            "file_count": len(analyses),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "language_file_counts": dict(sorted(languages.items())),
            "symbol_kind_counts": dict(
                sorted(Counter(node["kind"] for node in nodes.values()).items())
            ),
            "observed_effect_count": sum(
                len(node.get("effects", ())) for node in nodes.values()
            ),
            "resolved_call_edges": resolved,
            "unreadable_files": len(failures),
        },
        "nodes": [nodes[key] for key in sorted(nodes)],        "edges": [
            {"source": source, "target": target, "relation": relation, "label": label}
            for source, target, relation, label in sorted(edges)
        ],
        "unreadable_files": sorted(failures, key=lambda item: item["path"]),
        "unresolved_callees": sorted(unresolved)[:200],
        "ambiguous_callees": sorted(ambiguous)[:200],
        "limitations": [
            "Only Python, JavaScript, and TypeScript receive structural analysis.",
            "Call targets are linked only when a single declaration shares the name; "
            "ambiguous and unresolved callees are listed rather than guessed.",
            "Import edges link only when the specifier resolves to a discovered file.",
            "Observed effects are a small heuristic set; absence does not prove purity.",
            "A source map describes structure, not behavior or business intent.",
        ],
    }
    report_size = len(str(report))
    if report_size > MAX_REPORT_BYTES:
        raise SourceMapError(
            f"Source map exceeds the {MAX_REPORT_BYTES}-byte limit; analyze a smaller root"
        )
    return report


def to_text(report: dict[str, Any]) -> str:
    """Render a source map as reader-facing plain text.

    The output is written for someone who does not know the source language, so
    it leads with what the program is, then what it declares, then what it can
    reach outside itself.
    """

    summary = report["summary"]
    lines: list[str] = [
        f"Source map for {report['project']['name']}",
        (
            f"Files: {summary['file_count']}  "
            f"declarations: {summary['node_count']}  "
            f"connections: {summary['edge_count']}"
        ),
        "Languages: "
        + ", ".join(
            f"{name} ({count})" for name, count in summary["language_file_counts"].items()
        ),
    ]

    effects_by_kind: dict[str, list[dict[str, Any]]] = {}
    for node in report["nodes"]:
        for effect in node.get("effects", ()):
            # Module-level effects carry "kind"; per-call effects carry
            # "observed_effect". Normalize so both report formats read alike.
            kind = effect.get("kind") or effect.get("observed_effect")
            if not kind:
                continue
            effects_by_kind.setdefault(kind, []).append(
                dict(effect, kind=kind, owner=node["label"])
            )

    if effects_by_kind:
        lines.append("")
        lines.append("External capabilities observed (declared code, not proof of use):")
        for kind in sorted(effects_by_kind):
            rows = effects_by_kind[kind]
            names = sorted({row["callee"] for row in rows})
            shown = ", ".join(names[:6]) + (f", +{len(names) - 6} more" if len(names) > 6 else "")
            lines.append(f"  {kind:11} {shown}")

    by_file: dict[str, list[dict[str, Any]]] = {}
    for node in report["nodes"]:
        if node["kind"] == "module":
            continue
        by_file.setdefault(node["file"], []).append(node)

    for file_name in sorted(by_file):
        lines.append("")
        lines.append(file_name)
        for node in sorted(
            by_file[file_name], key=lambda item: (item["start_line"], item["label"])
        ):
            label = KIND_LABELS.get(node["kind"], node["kind"])
            detail = f"line {node['start_line']}"
            if node.get("async_"):
                detail += ", async"
            if node.get("doc"):
                # Documentation is collapsed to one bounded line so a
                # multi-paragraph docstring cannot break the
                # one-declaration-per-line shape or dominate the map.
                summary = " ".join(str(node["doc"]).split())
                if len(summary) > MAX_DOC_SUMMARY_CHARS:
                    summary = summary[: MAX_DOC_SUMMARY_CHARS - 1].rstrip() + "…"
                detail += f" - {summary}"
            lines.append(f"  {label:9} {node['label']} ({detail})")

    if report["unreadable_files"]:
        lines.append("")
        lines.append("Files not analyzed:")
        for item in report["unreadable_files"]:
            lines.append(f"  {item['path']}: {item['reason']}")

    lines.append("")
    lines.append(
        "Structure only: this map reports what is declared, not what runs."
    )
    return "\n".join(lines)


def to_mermaid(report: dict[str, Any]) -> str:
    """Render a source map as a Mermaid flowchart with readable node labels.

    Node identifiers are derived with SHA-1 rather than :func:`hash`, because
    string hashing is randomized per process and would make output differ
    between runs of the same program.
    """

    def escape(value: str) -> str:
        return value.replace('"', "'").replace("\n", " ")

    identifiers: dict[str, str] = {}

    def mermaid_id(identifier: str) -> str:
        mapped = identifiers.get(identifier)
        if mapped is None:
            digest = hashlib.sha1(identifier.encode("utf-8")).hexdigest()[:12]
            mapped = f"n_{digest}"
            identifiers[identifier] = mapped
        return mapped

    lines = ["flowchart LR"]
    for node in report["nodes"]:
        lines.append(
            f'    {mermaid_id(node["id"])}["{escape(node["label"])}'
            f'<br/>{escape(node["kind"])} ({escape(node["language"])})"]'
        )
    for edge in report["edges"]:
        if edge["relation"] != "calls":
            continue
        label = escape(edge["label"]) or "calls"
        lines.append(f"    {edge['source']} -->|{label}| {edge['target']}")
    return "\n".join(lines)


__all__ = [
    "KIND_LABELS",
    "MAX_EDGES",
    "MAX_FILES",
    "MAX_NODES",
    "SourceMapError",
    "build_source_map",
    "to_mermaid",
    "to_text",
]
