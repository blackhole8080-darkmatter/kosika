"""Language-neutral structural analysis contract.

Every source adapter emits the same document shape, so a mindmap, source map,
or assistant can consume Python, JavaScript, TypeScript, and future languages
without knowing which adapter produced them. The shape is a superset of
``kosika.source-analysis/v1`` as emitted by :mod:`kosika.python_adapter`.

This module is declarative only. It never reads, executes, imports, or
transpiles foreign source.
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable

SOURCE_ANALYSIS_SCHEMA = "kosika.source-analysis/v1"
SOURCE_MAP_SCHEMA = "kosika.source-map/v1"
#: Observed effect kinds an adapter may report.
EFFECT_KINDS: frozenset[str] = frozenset(
    {"database", "filesystem", "network", "process"}
)


#: Symbol kinds an adapter may report.
SYMBOL_KINDS: frozenset[str] = frozenset(
    {
        "class",
        "constant",
        "enum",
        "function",
        "interface",
        "method",
        "module",
        "struct",
        "type",
    }
)

#: Relations a source map edge may carry between symbols.
EDGE_RELATIONS: frozenset[str] = frozenset(
    {"calls", "extends", "implements", "imports", "member_of", "returns"}
)

_SHARED_LIMITATIONS: tuple[str, ...] = (
    "Call observations are syntactic and are not resolved to definitions.",
    "Dynamic dispatch, reflection, generated code, and runtime behavior are not analyzed.",
    "Observed effects are a small heuristic set; absence does not prove purity or safety.",
    "Purpose and business intent must come from documentation, tests, or developer input.",
)


class SourceAdapterError(ValueError):
    """Raised when a source adapter cannot analyze input safely."""


class SourceAnalysisError(SourceAdapterError):
    """Raised when an analysis document cannot be trusted."""




def source_digest(source: str, *, path: str) -> dict[str, Any]:
    """Return the shared source identity block for an analysis document."""

    raw = source.encode("utf-8")
    return {
        "path": path,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "line_count": len(source.splitlines()),
    }


def build_source_analysis(
    *,
    language: str,
    path: str,
    source: str,
    support_level: str,
    imports: Iterable[dict[str, Any]],
    module_calls: Iterable[dict[str, Any]],
    symbols: Iterable[dict[str, Any]],
    module_docstring: str | None = None,
    extra_limitations: Iterable[str] = (),
) -> dict[str, Any]:
    """Assemble and self-check a language-neutral analysis document.

    The builder enforces the contract so a malformed adapter fails loudly here
    rather than producing a map that quietly misrepresents a foreign program.
    """

    import_rows = [dict(item) for item in imports]
    module_call_rows = [dict(item) for item in module_calls]
    symbol_rows = [dict(item) for item in symbols]

    effects: list[dict[str, Any]] = []
    for call in module_call_rows:
        if call.get("observed_effect") is not None:
            effects.append(
                {
                    "symbol": "<module>",
                    "callee": call["callee"],
                    "line": call["line"],
                    "kind": call["observed_effect"],
                }
            )
    for symbol in symbol_rows:
        for call in symbol.get("calls", ()):
            if call.get("observed_effect") is not None:
                effects.append(
                    {
                        "symbol": symbol["qualified_name"],
                        "callee": call["callee"],
                        "line": call["line"],
                        "kind": call["observed_effect"],
                    }
                )

    document = {
        "schema": SOURCE_ANALYSIS_SCHEMA,
        "language": language,
        "analysis_mode": "structural_scan",
        "support_level": support_level,
        "source": source_digest(source, path=path),
        "module_docstring": module_docstring,
        "imports": import_rows,
        "module_calls": module_call_rows,
        "symbols": symbol_rows,
        "observed_effects": effects,
        "limitations": [*_SHARED_LIMITATIONS, *extra_limitations],
    }
    validate_source_analysis(document)
    return document


def validate_source_analysis(document: dict[str, Any]) -> None:
    """Fail closed when an analysis document violates the shared contract."""

    if not isinstance(document, dict):
        raise SourceAnalysisError("Source analysis must be a mapping")
    if document.get("schema") != SOURCE_ANALYSIS_SCHEMA:
        raise SourceAnalysisError(
            f"Source analysis schema must be {SOURCE_ANALYSIS_SCHEMA!r}"
        )
    language = document.get("language")
    if not isinstance(language, str) or not language:
        raise SourceAnalysisError("Source analysis requires a non-empty language")
    source = document.get("source")
    if not isinstance(source, dict):
        raise SourceAnalysisError("Source analysis requires a source identity block")
    for key in ("path", "sha256", "byte_length", "line_count"):
        if key not in source:
            raise SourceAnalysisError(f"Source analysis is missing source.{key}")

    imports = document.get("imports")
    symbols = document.get("symbols")
    if not isinstance(imports, list) or not isinstance(symbols, list):
        raise SourceAnalysisError("Source analysis requires import and symbol lists")

    for item in imports:
        if not isinstance(item, dict) or "kind" not in item or "line" not in item:
            raise SourceAnalysisError("Malformed import observation in source analysis")

    qualified_names: set[str] = set()
    for symbol in symbols:
        if not isinstance(symbol, dict):
            raise SourceAnalysisError("Malformed symbol in source analysis")
        for key in ("kind", "name", "qualified_name", "start_line", "end_line", "calls"):
            if key not in symbol:
                raise SourceAnalysisError(f"Symbol is missing {key!r}")
        if symbol["kind"] not in SYMBOL_KINDS:
            raise SourceAnalysisError(f"Unsupported symbol kind {symbol['kind']!r}")
        if symbol["start_line"] > symbol["end_line"]:
            raise SourceAnalysisError(
                f"Symbol {symbol['qualified_name']!r} has an inverted line range"
            )
        name = symbol["qualified_name"]
        # Repeated qualified names within one file are legal in the languages
        # this contract covers: JavaScript permits several `function node()`
        # declarations in a module, and a name may also be reused in nested
        # scopes. The analysis therefore reports what the file declares, and
        # the source map disambiguates by file and position.
        qualified_names.add(name)
        for call in symbol["calls"]:
            if not isinstance(call, dict) or "callee" not in call:
                raise SourceAnalysisError(
                    f"Malformed call observation in symbol {name!r}"
                )

    for effect in document.get("observed_effects", ()):
        if effect.get("kind") not in EFFECT_KINDS:
            raise SourceAnalysisError(
                f"Unsupported observed effect kind {effect.get('kind')!r}"
            )

