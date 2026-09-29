"""Optional Tree-sitter backend for bounded Rust syntax analysis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:
    import tree_sitter_rust
    from tree_sitter import Language, Parser
except ImportError:  # pragma: no cover - exercised when optional packages are absent
    Language = None  # type: ignore[assignment]
    Parser = None  # type: ignore[assignment]
    tree_sitter_rust = None  # type: ignore[assignment]


class RustParserBackendError(ValueError):
    """Raised when the parser backend cannot produce a trustworthy AST."""


@dataclass(frozen=True)
class ParsedRustSource:
    """Normalized syntax facts extracted from one Rust AST."""

    imports: tuple[dict[str, Any], ...]
    symbols: tuple[dict[str, Any], ...]
    module_calls: tuple[dict[str, Any], ...]


_DECLARATION_TYPES = frozenset(
    {
        "const_item",
        "enum_item",
        "function_item",
        "function_signature_item",
        "impl_item",
        "mod_item",
        "static_item",
        "struct_item",
        "trait_item",
        "type_item",
    }
)


def parser_available() -> bool:
    """Return whether Tree-sitter and the Rust grammar are importable."""

    return Language is not None and Parser is not None and tree_sitter_rust is not None


def _line(node: Any) -> int:
    return int(node.start_point.row) + 1


def _end_line(node: Any) -> int:
    return max(_line(node), int(node.end_point.row) + 1)


def _text(source: bytes, node: Any | None) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8")


def _field_text(source: bytes, node: Any, field: str) -> str:
    return _text(source, node.child_by_field_name(field)).strip()


def _base_type(value: str) -> str:
    return value.split("<", 1)[0].strip()


def _doc_summary(source: bytes, node: Any) -> str | None:
    lines = source.decode("utf-8").splitlines()
    index = int(node.start_point.row) - 1
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


def _async(source: bytes, node: Any) -> bool:
    name = node.child_by_field_name("name")
    if name is None:
        return False
    header = source[node.start_byte : name.start_byte]
    return b"async" in header


def _callee(source: bytes, node: Any) -> str:
    function = node.child_by_field_name("function")
    return "".join(_text(source, function).split())


def _call_rows(source: bytes, node: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type == "call_expression":
            callee = _callee(source, current)
            if callee:
                rows.append(
                    {
                        "_start_byte": current.start_byte,
                        "callee": callee,
                        "line": _line(current),
                        "observed_effect": None,
                    }
                )
        stack.extend(reversed(current.named_children))
    return rows


def _declaration_name(source: bytes, node: Any) -> str:
    if node.type == "impl_item":
        target = _field_text(source, node, "type")
        trait = _field_text(source, node, "trait")
        return f"{trait} for {target}" if trait else target
    return _field_text(source, node, "name")


def _symbol_kind(node: Any, owner: str | None) -> str:
    if node.type in {"function_item", "function_signature_item"}:
        return "method" if owner else "function"
    return {
        "const_item": "constant",
        "enum_item": "enum",
        "impl_item": "impl",
        "mod_item": "module",
        "static_item": "constant",
        "struct_item": "struct",
        "trait_item": "interface",
        "type_item": "type",
    }[node.type]


def _symbol_row(source: bytes, node: Any, owner: str | None) -> dict[str, Any]:
    name = _declaration_name(source, node)
    qualified_name = name
    if node.type in {"function_item", "function_signature_item"} and owner:
        qualified_name = f"{owner}.{name}"
    calls = (
        []
        if node.type in {"impl_item", "trait_item", "mod_item"}
        else _call_rows(source, node)
    )
    return {
        "kind": _symbol_kind(node, owner),
        "name": name,
        "qualified_name": qualified_name,
        "start_line": _line(node),
        "end_line": _end_line(node),
        "docstring": _doc_summary(source, node),
        "async": _async(source, node) if node.type == "function_item" else False,
        "calls": calls,
        "_start_byte": node.start_byte,
        "_end_byte": node.end_byte,
    }


def _walk_declarations(
    source: bytes,
    node: Any,
    symbols: list[dict[str, Any]],
    ranges: list[tuple[int, int]],
    owner: str | None = None,
) -> None:
    for child in node.named_children:
        if child.type not in _DECLARATION_TYPES:
            _walk_declarations(source, child, symbols, ranges, owner)
            continue
        child_owner = owner
        row = _symbol_row(source, child, owner)
        symbols.append(row)
        ranges.append((child.start_byte, child.end_byte))
        if child.type in {"impl_item", "trait_item", "mod_item"}:
            declaration_name = _declaration_name(source, child)
            child_owner = _base_type(declaration_name.split(" for ")[-1])
        body = child.child_by_field_name("body")
        if body is not None:
            _walk_declarations(source, body, symbols, ranges, child_owner)


def _imports(source: bytes, root: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "use_declaration":
            argument = node.child_by_field_name("argument")
            module = _text(source, argument).strip()
            rows.append(
                {
                    "kind": "use",
                    "module": module,
                    "name": None,
                    "alias": None,
                    "line": _line(node),
                    "names": None,
                }
            )
        elif node.type == "extern_crate_declaration":
            name = _field_text(source, node, "name")
            rows.append(
                {
                    "kind": "extern_crate",
                    "module": name,
                    "name": name,
                    "alias": None,
                    "line": _line(node),
                    "names": None,
                }
            )
        stack.extend(reversed(node.named_children))
    return rows


def parse_rust_source(source: bytes) -> ParsedRustSource:
    """Parse UTF-8 Rust bytes into bounded normalized syntax facts."""

    if not parser_available():
        raise RustParserBackendError(
            "Tree-sitter Rust backend is unavailable; install tree-sitter-rust"
        )
    parser = Parser(Language(tree_sitter_rust.language()))
    tree = parser.parse(source)
    root = tree.root_node
    if root.has_error:
        raise RustParserBackendError("Rust parser found syntax errors")

    symbols: list[dict[str, Any]] = []
    ranges: list[tuple[int, int]] = []
    _walk_declarations(source, root, symbols, ranges)
    all_calls = _call_rows(source, root)
    module_calls = [
        call
        for call in all_calls
        if not any(
            start <= call["_start_byte"] < end for start, end in ranges
        )
    ]
    for call in module_calls:
        call.pop("_start_byte", None)
    for row in symbols:
        for call in row["calls"]:
            call.pop("_start_byte", None)
        row.pop("_start_byte", None)
        row.pop("_end_byte", None)
    symbols.sort(
        key=lambda item: (item["start_line"], item["qualified_name"], item["kind"])
    )
    imports = sorted(
        _imports(source, root), key=lambda item: (item["line"], item["module"])
    )
    return ParsedRustSource(tuple(imports), tuple(symbols), tuple(module_calls))




__all__ = [
    "ParsedRustSource",
    "RustParserBackendError",
    "parse_rust_source",
    "parser_available",
]
