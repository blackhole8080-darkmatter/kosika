"""Bounded, read-only structural analysis for Rust source files.

Tree-sitter provides the primary syntax backend. A bounded scanner remains as
an explicit fallback when the optional parser packages are unavailable.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .rust_parser_backend import (
    RustParserBackendError,
    parse_rust_source,
    parser_available,
)
from .source_schema import SourceAdapterError, build_source_analysis

MAX_SOURCE_BYTES = 1024 * 1024
MAX_SYMBOLS = 2000
MAX_IMPORTS = 4000
MAX_CALLS_PER_SCOPE = 2000
MAX_CALL_OBSERVATIONS = 20000
MAX_NESTING_DEPTH = 128


class RustSourceError(SourceAdapterError):
    """Raised when Rust source cannot be analyzed within adapter limits."""


class _Masked:
    def __init__(self, text: str) -> None:
        self.text = text

    def line_of(self, index: int) -> int:
        return self.text.count("\n", 0, max(0, index)) + 1


_DECL_RE = re.compile(
    r"(?m)(?:^[ \t]*|(?<={)[ \t]*)(?P<prefix>(?:(?:pub(?:\([^)]*\))?|unsafe|async|const|extern)\s+)*)(?P<kind>fn|struct|enum|trait|type|const|static|mod)\s+(?P<name>[A-Za-z_]\w*)"
)
_IMPL_RE = re.compile(
    r"(?m)^[ \t]*(?:unsafe\s+)?impl\s+(?:<[^{}]*>\s*)?(?P<head>[A-Za-z_]\w*(?:::\w+)*(?:\s+for\s+[A-Za-z_]\w*(?:::\w+)*)?)[^{}]*\{"
)
_USE_RE = re.compile(r"(?m)^[ \t]*(?:pub\s+)?use\s+(?P<clause>[^;\n]+);")
_EXTERN_CRATE_RE = re.compile(
    r"(?m)^[ \t]*(?:pub\s+)?extern\s+crate\s+(?P<name>[A-Za-z_]\w*)(?:\s+as\s+(?P<alias>[A-Za-z_]\w*))?\s*;"
)
_CALL_RE = re.compile(
    r"(?P<callee>(?:[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*|[A-Za-z_]\w*(?:\.[A-Za-z_]\w+)+))\s*\("
)
_KEYWORDS = frozenset(
    {
        "if",
        "while",
        "for",
        "match",
        "loop",
        "fn",
        "macro_rules",
    }
)
_SYMBOL_KINDS = {
    "fn": "function",
    "struct": "struct",
    "enum": "enum",
    "trait": "interface",
    "type": "type",
    "const": "constant",
    "static": "constant",
    "mod": "module",
}


def _blank(text: list[str], start: int, end: int) -> None:
    for index in range(start, end):
        if text[index] != "\n":
            text[index] = " "


def _quoted_end(source: str, start: int, quote: str) -> int | None:
    index = start + 1
    while index < len(source):
        if source[index] == "\\":
            index += 2
            continue
        if source[index] == quote:
            return index + 1
        index += 1
    return None


def _raw_string_end(source: str, start: int) -> int | None:
    index = start + 1
    while index < len(source) and source[index] == "#":
        index += 1
    if index >= len(source) or source[index] != '"':
        return None
    hashes = source[start + 1 : index]
    closing = '"' + hashes
    end = source.find(closing, index + 1)
    return None if end < 0 else end + len(closing)


def _mask(source: str) -> _Masked:
    """Mask comments and literals while preserving offsets and newlines."""

    masked = list(source)
    index = 0
    block_depth = 0
    while index < len(source):
        if block_depth:
            if source.startswith("/*", index):
                block_depth += 1
                _blank(masked, index, index + 2)
                index += 2
            elif source.startswith("*/", index):
                block_depth -= 1
                _blank(masked, index, index + 2)
                index += 2
            else:
                _blank(masked, index, index + 1)
                index += 1
            continue
        if source.startswith("//", index):
            comment_end = source.find("\n", index)
            comment_end = len(source) if comment_end < 0 else comment_end
            _blank(masked, index, comment_end)
            index = comment_end
            continue
        if source.startswith("/*", index):
            block_depth = 1
            _blank(masked, index, index + 2)
            index += 2
            continue
        if source[index] == "r":
            probe = index + 1
            while probe < len(source) and source[probe] == "#":
                probe += 1
            if probe < len(source) and source[probe] == '"':
                raw_end = _raw_string_end(source, index)
                if raw_end is None:
                    raise RustSourceError("Rust source has an unterminated raw string")
                _blank(masked, index, raw_end)
                index = raw_end
                continue
        if source[index] == "'":
            is_lifetime = (
                index + 1 < len(source)
                and (source[index + 1].isalpha() or source[index + 1] == "_")
                and (index + 2 >= len(source) or source[index + 2] != "'")
            )
            if is_lifetime:
                index += 1
                continue
        if source[index] in {'"', "'"}:
            literal_end = _quoted_end(source, index, source[index])
            if literal_end is None:
                raise RustSourceError("Rust source has an unterminated literal")
            _blank(masked, index, literal_end)
            index = literal_end
            continue
        index += 1
    if block_depth:
        raise RustSourceError("Rust source has an unterminated block comment")
    return _Masked("".join(masked))


def _match_brace(text: str, opening: int) -> int:
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return len(text)


def _guard_nesting(text: str) -> None:
    depth = 0
    for character in text:
        if character == "{":
            depth += 1
            if depth > MAX_NESTING_DEPTH:
                raise RustSourceError(
                    f"Rust source exceeds the {MAX_NESTING_DEPTH}-level nesting limit"
                )
        elif character == "}":
            depth = max(0, depth - 1)


def _doc_summary(source: str, start_line: int) -> str | None:
    lines = source.splitlines()
    index = start_line - 2
    comments: list[str] = []
    while index >= 0:
        line = lines[index].strip()
        if line.startswith("///"):
            comments.append(line[3:].strip())
        elif line.startswith("//!"):
            comments.append(line[3:].strip())
        elif line:
            break
        index -= 1
    if not comments:
        return None
    return " ".join(reversed(comments))[:400] or None


def _observed_effect(callee: str) -> str | None:
    lowered = callee.lower()
    if any(
        marker in lowered
        for marker in (
            "std::fs",
            "tokio::fs",
            "file::open",
            "file::create",
            "read_to_string",
            "write_all",
        )
    ):
        return "filesystem"
    if any(
        marker in lowered for marker in ("reqwest", "hyper", "tcpstream", "httpclient")
    ):
        return "network"
    if any(
        marker in lowered
        for marker in ("std::process", "command::new", "std::process::command")
    ):
        return "process"
    if any(
        marker in lowered
        for marker in ("sqlx", "diesel", "rusqlite", "postgres", "mysql")
    ):
        return "database"
    return None


def _calls(text: str, start: int, end: int) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for match in _CALL_RE.finditer(text, start, end):
        callee = match.group("callee")
        if callee.split("::")[-1] in _KEYWORDS:
            continue
        calls.append(
            {
                "_offset": match.start(),
                "callee": callee,
                "line": text.count("\n", 0, match.start()) + 1,
                "observed_effect": _observed_effect(callee),
            }
        )
        if len(calls) >= MAX_CALLS_PER_SCOPE:
            raise RustSourceError(
                f"A Rust scope exceeds the {MAX_CALLS_PER_SCOPE} call observation limit"
            )
    return calls


def _declaration_end(text: str, start: int, kind: str) -> int:
    if kind in {"const", "static", "type"}:
        end = text.find(";", start)
        return len(text) if end < 0 else end + 1
    opening = text.find("{", start)
    if opening < 0:
        end = text.find(";", start)
        return len(text) if end < 0 else end + 1
    return _match_brace(text, opening)


def _import_rows(masked: _Masked) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for match in _USE_RE.finditer(masked.text):
        clause = " ".join(match.group("clause").split())
        rows.append(
            {
                "kind": "use",
                "module": clause,
                "name": None,
                "alias": None,
                "line": masked.line_of(match.start()),
                "names": None,
            }
        )
    for match in _EXTERN_CRATE_RE.finditer(masked.text):
        rows.append(
            {
                "kind": "extern_crate",
                "module": match.group("name"),
                "name": match.group("name"),
                "alias": match.group("alias"),
                "line": masked.line_of(match.start()),
                "names": None,
            }
        )
    if len(rows) > MAX_IMPORTS:
        raise RustSourceError(
            f"Rust source exceeds the {MAX_IMPORTS}-import analysis limit"
        )
    return sorted(rows, key=lambda item: (item["line"], item["module"]))


def _analyze_rust_source_scanner(
    source: str, *, path: str = "<memory>"
) -> dict[str, Any]:
    """Analyze Rust with the legacy bounded scanner fallback."""

    if not isinstance(source, str):
        raise RustSourceError("Rust source must be text")
    try:
        raw = source.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RustSourceError("Rust source is not valid UTF-8 text") from exc
    if len(raw) > MAX_SOURCE_BYTES:
        raise RustSourceError(
            f"Rust source exceeds the {MAX_SOURCE_BYTES}-byte analysis limit"
        )

    masked = _mask(source)
    _guard_nesting(masked.text)
    imports = _import_rows(masked)
    impl_matches = list(_IMPL_RE.finditer(masked.text))
    impl_ranges: list[tuple[int, int, str]] = []
    symbols: list[dict[str, Any]] = []

    for match in impl_matches:
        head = " ".join(match.group("head").split())
        end = _match_brace(masked.text, match.end() - 1)
        target = head.split(" for ")[-1]
        impl_ranges.append((match.start(), end, target))
        symbols.append(
            {
                "kind": "impl",
                "name": head,
                "qualified_name": head,
                "start_line": masked.line_of(match.start()),
                "end_line": masked.line_of(max(match.start(), end - 1)),
                "docstring": _doc_summary(source, masked.line_of(match.start())),
                "async": False,
                "calls": _calls(masked.text, match.end(), end),
            }
        )

    for match in _DECL_RE.finditer(masked.text):
        kind = match.group("kind")
        name = match.group("name")
        start = match.start()
        end = _declaration_end(masked.text, match.end(), kind)
        owner = next(
            (
                target
                for impl_start, impl_end, target in impl_ranges
                if impl_start <= start < impl_end
            ),
            None,
        )
        symbol_kind = (
            "method" if kind == "fn" and owner is not None else _SYMBOL_KINDS[kind]
        )
        qualified_name = f"{owner}.{name}" if owner is not None else name
        prefix = match.group("prefix")
        symbols.append(
            {
                "kind": symbol_kind,
                "name": name,
                "qualified_name": qualified_name,
                "start_line": masked.line_of(start),
                "end_line": masked.line_of(max(start, end - 1)),
                "docstring": _doc_summary(source, masked.line_of(start)),
                "async": "async" in prefix.split(),
                "calls": _calls(masked.text, match.end(), end),
            }
        )

    if len(symbols) > MAX_SYMBOLS:
        raise RustSourceError(
            f"Rust source exceeds the {MAX_SYMBOLS}-symbol analysis limit"
        )
    symbols.sort(key=lambda item: (item["start_line"], item["qualified_name"]))
    declaration_ranges: list[tuple[int, int]] = []
    for match in _DECL_RE.finditer(masked.text):
        declaration_ranges.append(
            (
                match.start(),
                _declaration_end(masked.text, match.end(), match.group("kind")),
            )
        )
    declaration_ranges.extend((start, end) for start, end, _target in impl_ranges)
    module_calls: list[dict[str, Any]] = []
    for call in _calls(masked.text, 0, len(masked.text)):
        offset = call["_offset"]
        if not any(start <= offset < end for start, end in declaration_ranges):
            module_calls.append(call)
    if (
        len(module_calls) + sum(len(item["calls"]) for item in symbols)
        > MAX_CALL_OBSERVATIONS
    ):
        raise RustSourceError(
            f"Rust source exceeds the {MAX_CALL_OBSERVATIONS} call observation limit"
        )
    for call in module_calls:
        call.pop("_offset", None)
    for symbol in symbols:
        for call in symbol["calls"]:
            call.pop("_offset", None)

    document = build_source_analysis(
        language="rust",
        path=path,
        source=source,
        support_level="declarations_imports_and_direct_call_observations",
        imports=imports,
        module_calls=module_calls,
        symbols=symbols,
        extra_limitations=(
            "This is a bounded scanner, not a Rust parser: macro expansion, conditional compilation, and complex syntax are not resolved.",
            "Trait implementations, type inference, ownership, and borrow checking are not analyzed.",
            "Call targets are not resolved to definitions, and proc-macro generated code is not visible.",
        ),
    )
    document["analysis_mode"] = "structural_scan"
    return document


def _annotate_effects(
    module_calls: list[dict[str, Any]], symbols: list[dict[str, Any]]
) -> None:
    for call in module_calls:
        call["observed_effect"] = _observed_effect(call["callee"])
    for symbol in symbols:
        for call in symbol["calls"]:
            call["observed_effect"] = _observed_effect(call["callee"])


def _build_parser_document(source: str, path: str, parsed: Any) -> dict[str, Any]:
    imports = [dict(item) for item in parsed.imports]
    module_calls = [dict(item) for item in parsed.module_calls]
    symbols = [
        {
            **dict(symbol),
            "calls": [dict(call) for call in symbol["calls"]],
        }
        for symbol in parsed.symbols
    ]
    if len(imports) > MAX_IMPORTS:
        raise RustSourceError(
            f"Rust source exceeds the {MAX_IMPORTS}-import analysis limit"
        )
    if len(symbols) > MAX_SYMBOLS:
        raise RustSourceError(
            f"Rust source exceeds the {MAX_SYMBOLS}-symbol analysis limit"
        )
    total_calls = len(module_calls) + sum(len(symbol["calls"]) for symbol in symbols)
    if total_calls > MAX_CALL_OBSERVATIONS:
        raise RustSourceError(
            f"Rust source exceeds the {MAX_CALL_OBSERVATIONS} call observation limit"
        )
    _annotate_effects(module_calls, symbols)
    document = build_source_analysis(
        language="rust",
        path=path,
        source=source,
        support_level="parser_ast_declarations_imports_and_direct_call_observations",
        imports=imports,
        module_calls=module_calls,
        symbols=symbols,
        extra_limitations=(
            "Tree-sitter provides syntax structure, not Rust type checking or borrow checking.",
            "Macro expansion, conditional compilation, generated code, and dynamic behavior are not resolved.",
            "Call targets are not resolved to definitions unless a source-map relationship is unambiguous.",
        ),
    )
    document["analysis_mode"] = "parser_ast"
    document["parser_backend"] = "tree-sitter-rust"

    return document


def analyze_rust_source(source: str, *, path: str = "<memory>") -> dict[str, Any]:
    """Analyze Rust with Tree-sitter when available, otherwise use the scanner."""

    if not isinstance(source, str):
        raise RustSourceError("Rust source must be text")
    try:
        raw = source.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RustSourceError("Rust source is not valid UTF-8 text") from exc
    if len(raw) > MAX_SOURCE_BYTES:
        raise RustSourceError(
            f"Rust source exceeds the {MAX_SOURCE_BYTES}-byte analysis limit"
        )
    if not parser_available():
        return _analyze_rust_source_scanner(source, path=path)
    try:
        parsed = parse_rust_source(raw)
    except RustParserBackendError as exc:
        raise RustSourceError(str(exc)) from exc
    return _build_parser_document(source, path, parsed)


def analyze_rust_file(path: str | Path) -> dict[str, Any]:
    """Read one bounded UTF-8 Rust file and analyze it without execution."""

    source_path = Path(path)
    try:
        with source_path.open("rb") as source_file:
            raw = source_file.read(MAX_SOURCE_BYTES + 1)
    except OSError as exc:
        raise RustSourceError(f"Unable to read Rust source: {exc}") from exc
    if len(raw) > MAX_SOURCE_BYTES:
        raise RustSourceError(
            f"Rust source exceeds the {MAX_SOURCE_BYTES}-byte analysis limit"
        )
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RustSourceError("Rust source must be UTF-8 encoded") from exc
    return analyze_rust_source(source, path=source_path.as_posix())


__all__ = [
    "MAX_CALL_OBSERVATIONS",
    "MAX_CALLS_PER_SCOPE",
    "MAX_IMPORTS",
    "MAX_NESTING_DEPTH",
    "MAX_SOURCE_BYTES",
    "MAX_SYMBOLS",
    "RustSourceError",
    "analyze_rust_file",
    "analyze_rust_source",
]
