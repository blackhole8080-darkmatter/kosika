"""Bounded, read-only editor features for Kosika source files.

The backend returns JSON-compatible data so a VS Code client can provide editor
features without reimplementing Kosika parsing or checking in JavaScript.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .checker import CheckError, check
from .development import check_project
from .diagnostics import Diagnostic, anchors_for, diagnostic_from_exception
from .lexer import Token, lex
from .parser import ParseError, parse

EDITOR_SCHEMA = "kosika.editor-features/v1"
MAX_SOURCE_BYTES = 512 * 1024
_DECLARATIONS = {
    "record": "struct",
    "tool": "tool",
    "agent": "agent",
    "swarm": "swarm",
    "model": "model",
    "workflow": "workflow",
    "fn": "function",
}
_KEYWORDS = (
    "record",
    "tool",
    "agent",
    "swarm",
    "model",
    "workflow",
    "fn",
    "step",
    "let",
    "return",
    "import",
    "export",
    "call",
    "ask",
    "execute",
    "use_model",
    "require",
    "approval",
    "can",
    "cannot",
)


class EditorFeatureError(ValueError):
    """Raised when editor feature input exceeds safe bounds."""


def build_editor_features(
    source: str,
    *,
    path: str = "<memory>",
    source_path: str | Path | None = None,
    root: str | Path | None = None,
    source_overrides: Mapping[Path, str] | None = None,
) -> dict[str, Any]:
    """Return diagnostics and static editor features for one source document."""

    if not isinstance(source, str):
        raise EditorFeatureError("Editor source must be text")
    raw = source.encode("utf-8")
    if len(raw) > MAX_SOURCE_BYTES:
        raise EditorFeatureError(
            f"Editor source exceeds the {MAX_SOURCE_BYTES}-byte limit"
        )

    diagnostics: list[dict[str, Any]] = []
    tokens: list[Token] = []
    program = None
    imports: list[str] = []
    exports: list[str] = []
    try:
        tokens = lex(source)
        program = parse(source)
        imports = list(program.imports)
        exports = list(program.exports)
    except (ParseError, ValueError) as exc:
        diagnostics.append(_diagnostic(exc, tokens, path=path))

    if program is not None:
        if program.imports and source_path is not None:
            overrides = dict(source_overrides or {})
            overrides[Path(source_path)] = source
            checked = check_project(
                source_path,
                root=root,
                source_overrides=overrides,
            )
            report = checked["diagnostics"]
            assert isinstance(report, dict)
            diagnostics.extend(report["diagnostics"])
        else:
            try:
                check(program)
            except CheckError as exc:
                diagnostics.append(_diagnostic(exc, tokens, path=path))

    symbols = _symbols(tokens, program)
    definitions = [
        {
            "name": symbol["name"],
            "kind": symbol["kind"],
            "range": symbol["range"],
            "selectionRange": symbol["selectionRange"],
            "detail": symbol["detail"],
        }
        for symbol in symbols
    ]
    completions = _completions(symbols)
    hovers = [
        {
            "name": symbol["name"],
            "range": symbol["selectionRange"],
            "contents": f"```kosika\n{symbol['detail']}\n```",
        }
        for symbol in symbols
    ]
    return {
        "schema": EDITOR_SCHEMA,
        "path": path,
        "imports": imports,
        "exports": exports,
        "diagnostics": diagnostics,
        "symbols": symbols,
        "definitions": definitions,
        "hovers": hovers,
        "completions": completions,
    }


def _position(line: int, column: int, length: int = 0) -> dict[str, int]:
    return {"line": max(line - 1, 0), "character": max(column - 1, 0) + length}


def _range(token: Token, *, end: Token | None = None) -> dict[str, dict[str, int]]:
    finish = end or token
    length = len(finish.value) if finish.value else 1
    return {
        "start": _position(token.line, token.column),
        "end": _position(finish.line, finish.column, length),
    }


def _diagnostic(exc: Exception, tokens: list[Token], *, path: str) -> dict[str, Any]:
    structured = diagnostic_from_exception(
        exc,
        anchors=anchors_for(tokens) if tokens else None,
        path=path,
    )
    return _diagnostic_payload(structured)


def _diagnostic_payload(structured: Diagnostic) -> dict[str, Any]:
    """Render a structured failure for the editor and LSP layers."""

    payload: dict[str, Any] = {
        "message": structured.message,
        "severity": "error",
        "range": structured.span.to_lsp() if structured.span is not None else None,
        "path": structured.path,
        "code": structured.code,
    }
    if structured.data:
        payload["data"] = dict(structured.data)
    return payload


def _symbols(tokens: list[Token], program: Any) -> list[dict[str, Any]]:
    symbols: list[dict[str, Any]] = []
    depth = 0
    for index, token in enumerate(tokens[:-1]):
        if depth == 0 and token.kind == "IDENT" and token.value in _DECLARATIONS:
            name = tokens[index + 1]
            if name.kind == "IDENT":
                end = _declaration_end(tokens, index + 1)
                kind = _DECLARATIONS[token.value]
                symbols.append(
                    {
                        "name": name.value,
                        "kind": kind,
                        "detail": _detail(program, kind, name.value),
                        "range": _range(token, end=end),
                        "selectionRange": _range(name),
                    }
                )
        if token.value in {"{", "["}:
            depth += 1
        elif token.value in {"}", "]"}:
            depth = max(depth - 1, 0)
    return sorted(
        symbols,
        key=lambda item: (
            item["selectionRange"]["start"]["line"],
            item["selectionRange"]["start"]["character"],
            item["name"],
        ),
    )


def _declaration_end(tokens: list[Token], name_index: int) -> Token:
    opening_index = None
    for index in range(name_index + 1, len(tokens) - 1):
        if tokens[index].value == "{":
            opening_index = index
            break
        if tokens[index].value in {
            "}",
            "record",
            "tool",
            "agent",
            "swarm",
            "model",
            "workflow",
            "fn",
        }:
            break
    if opening_index is None:
        return tokens[name_index]
    depth = 0
    for token in tokens[opening_index:]:
        if token.value == "{":
            depth += 1
        elif token.value == "}":
            depth -= 1
            if depth == 0:
                return token
    return tokens[name_index]


def _detail(program: Any, kind: str, name: str) -> str:
    if program is None:
        return f"{kind} {name}"
    collections = {
        "struct": program.records,
        "tool": program.tools,
        "agent": program.agents,
        "swarm": program.swarms,
        "model": program.models,
        "workflow": program.workflows,
        "function": program.functions,
    }
    declaration = next(
        (item for item in collections.get(kind, ()) if item.name == name), None
    )
    if declaration is None:
        return f"{kind} {name}"
    if kind == "struct":
        fields = ", ".join(
            f"{field.name}: {field.type_name}" for field in declaration.fields
        )
        return f"record {name} {{{fields}}}"
    if kind == "tool":
        return f"tool {name} ({declaration.access}) {declaration.source}/{declaration.operation}"
    if kind == "agent":
        return f"agent {name} can [{', '.join(declaration.can)}]"
    if kind == "swarm":
        return f"swarm {name} -> {declaration.workflow}"
    if kind == "model":
        return f"model {name}: {declaration.input} -> {declaration.output}"
    if kind == "workflow":
        return f"workflow {name}: {declaration.output_type}"
    parameters = ", ".join(
        f"{parameter.name}: {parameter.type_name}"
        for parameter in declaration.parameters
    )
    return f"fn {name}({parameters}) -> {declaration.output_type}"


def _completions(symbols: list[dict[str, Any]]) -> list[dict[str, str]]:
    items = [{"label": keyword, "kind": "keyword"} for keyword in _KEYWORDS]
    items.extend(
        {
            "label": symbol["name"],
            "kind": symbol["kind"],
            "detail": symbol["detail"],
        }
        for symbol in symbols
    )
    return sorted(items, key=lambda item: (item["label"], item["kind"]))


__all__ = [
    "EDITOR_SCHEMA",
    "EditorFeatureError",
    "MAX_SOURCE_BYTES",
    "build_editor_features",
]
