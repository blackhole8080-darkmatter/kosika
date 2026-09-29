"""Read-only structural analysis for JavaScript and TypeScript source.

This adapter reports syntax-backed facts: imports and exports, declarations,
class members, and call observations. It is a bounded scanner, not a full
parser. It does not claim to infer behavior, resolve dynamic dispatch, type
check, or execute the source.

The scanner masks comments, string bodies, template literals, and regular
expression literals before matching declarations. Masking preserves length and
line breaks, so every reported line number refers to the original file.

Two deliberate choices keep the output honest rather than merely impressive:

* Template literal bodies are masked wholesale, including ``${...}``
  interpolations. Arbitrary text inside a template must not be able to invent
  declarations or calls.
* Regular expression literals are masked, using the standard preceding-token
  heuristic to tell a regex from a division. A misread is reported through
  :func:`scan_uncertainties` rather than silently dropped.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from .source_schema import (
    SourceAdapterError,
    build_source_analysis,
)

MAX_SOURCE_BYTES = 1024 * 1024
MAX_SYMBOLS = 2000
MAX_IMPORTS = 4000
MAX_CALLS_PER_SCOPE = 2000
MAX_CALL_OBSERVATIONS = 20000
MAX_NESTING_DEPTH = 64
MAX_DOC_CHARS = 400
MAX_INLINE_BODY_CHARS = 400

#: Marker used to blank out literal bodies while preserving offsets.
_MASK = "@"
_QUOTE_CHARS = "'\"`"

_LINE_COMMENT = re.compile(r"//[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

#: A trailing character that cannot end an expression, so ``/`` starts a regex.
_REGEX_PRECEDERS = frozenset("(,=:[!&|?{};+-*%<>~^")
_REGEX_PRECEDING_WORDS = frozenset(
    {
        "await",
        "case",
        "delete",
        "do",
        "else",
        "in",
        "instanceof",
        "new",
        "of",
        "return",
        "throw",
        "typeof",
        "void",
        "yield",
    }
)

#: Words that look like calls but are keywords, never reported as callees.
_KEYWORDS = frozenset(
    {
        "await",
        "case",
        "catch",
        "class",
        "const",
        "delete",
        "do",
        "else",
        "export",
        "extends",
        "finally",
        "for",
        "function",
        "if",
        "import",
        "in",
        "instanceof",
        "let",
        "new",
        "of",
        "return",
        "super",
        "switch",
        "throw",
        "try",
        "typeof",
        "var",
        "void",
        "while",
        "with",
        "yield",
    }
)

_IMPORT_RE = re.compile(
    r"(?P<keyword>import|export)\b(?P<clause>[^;\n]*?)\bfrom\b\s*"
    r"(?P<quote>['\"])(?P<module>[^'\"]*)(?P=quote)"
)
_BARE_IMPORT_RE = re.compile(
    r"\bimport\s*(?P<quote>['\"])(?P<module>[^'\"]*)(?P=quote)"
)
_REQUIRE_RE = re.compile(
    r"\brequire\s*\(\s*(?P<quote>['\"])(?P<module>[^'\"]*)(?P=quote)\s*\)"
)
_DYNAMIC_IMPORT_RE = re.compile(r"\bimport\s*\(")
_IMPORT_SPECIFIER_RE = re.compile(r"\{(?P<names>[^}]*)\}")

_CLASS_RE = re.compile(r"\bclass\s+(?P<name>[A-Za-z_$][\w$]*)")
_FUNCTION_RE = re.compile(
    r"(?:async\s+)?\bfunction\s*\*?\s*(?P<name>[A-Za-z_$][\w$]*)"
)
_INTERFACE_RE = re.compile(r"\binterface\s+(?P<name>[A-Za-z_$][\w$]*)")
_TYPE_RE = re.compile(r"\btype\s+(?P<name>[A-Za-z_$][\w$]*)\s*=")
_ENUM_RE = re.compile(r"\b(?:const\s+)?enum\s+(?P<name>[A-Za-z_$][\w$]*)")
_BINDING_RE = re.compile(
    r"\b(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*"
    r"(?::\s*(?P<type>[^=\n]+?)\s*)?=\s*(?P<value>async\s*)?"
    r"(?P<body>function\b|\(|[A-Za-z_$][\w$]*\s*=>)"
)
#: Any top-level binding, including plain values that are not functions.
_PLAIN_BINDING_RE = re.compile(
    r"\b(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)"
    r"(?:\s*:\s*(?P<type>[^=\n]+?))?\s*=(?!=)"
)
_METHOD_RE = re.compile(
    r"(?:(?:public|private|protected|readonly|static|async|abstract|override|declare)\s+)*"
    r"(?P<name>[A-Za-z_$#][\w$]*)\s*(?:<[^<>(){}]*>)?\s*\("
)
_GETTER_RE = re.compile(
    r"\b(?:get|set)\s+(?P<name>[A-Za-z_$#][\w$]*)\s*(?:<[^<>(){}]*>)?\s*\("
)
#: Arrow function written as ``(args): Type => body``.
_TYPED_ARROW_RE = re.compile(r"\(\s*(?P<params>[^()]*)\)\s*:\s*[^=]{0,120}?=>")
_CALL_RE = re.compile(
    r"(?P<callee>(?:[A-Za-z_$][\w$]*\s*\.\s*)*[A-Za-z_$][\w$]*)\s*\("
)
_DOTTED_RE = re.compile(r"^[A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)*$")

_EFFECT_ROOTS: dict[str, tuple[str, ...]] = {
    "filesystem": (
        "fs",
        "fs/promises",
        "node:fs",
        "readFile",
        "readFileSync",
        "writeFile",
        "writeFileSync",
        "createReadStream",
        "createWriteStream",
    ),
    "process": (
        "child_process",
        "node:child_process",
        "exec",
        "execSync",
        "execFile",
        "spawn",
        "spawnSync",
        "fork",
    ),
    "network": (
        "fetch",
        "axios",
        "http",
        "https",
        "node:http",
        "node:https",
        "XMLHttpRequest",
        "got",
    ),
    "database": ("pg", "mysql", "mysql2", "mongoose", "sqlite3", "prisma", "knex"),
}

#: Reverse index so a dotted callee can be classified in one pass.
_EFFECT_BY_LEAF: dict[str, str] = {
    leaf: kind for kind, leaves in _EFFECT_ROOTS.items() for leaf in leaves
}

_JSX_LANGUAGES = frozenset({"javascript", "typescript"})


class JavaScriptSourceError(SourceAdapterError):
    """Raised when JavaScript or TypeScript source cannot be scanned safely."""


class _Masked:
    """A length-preserving masked view of source plus literal bookkeeping."""

    __slots__ = ("text", "lines", "literals", "uncertainties")

    def __init__(
        self,
        text: str,
        literals: list[dict[str, Any]],
        uncertainties: list[dict[str, Any]],
    ) -> None:
        self.text = text
        self.lines = text.count("\n") + 1
        self.literals = literals
        self.uncertainties = uncertainties

    def line_of(self, index: int) -> int:
        return self.text.count("\n", 0, index) + 1


def _previous_significant(masked: str, index: int) -> tuple[str, str]:
    """Return the previous non-whitespace character and word before ``index``."""

    position = index - 1
    while position >= 0 and masked[position] in " \t\r\n":
        position -= 1
    if position < 0:
        return "", ""
    character = masked[position]
    if character.isalnum() or character in "_$":
        end = position + 1
        while position >= 0 and (masked[position].isalnum() or masked[position] in "_$"):
            position -= 1
        return character, masked[position + 1 : end]
    return character, ""


def _is_jsx_closing_tag(masked: str, index: int) -> bool:
    """Return True when ``/`` at ``index`` closes a JSX element, not a regex.

    ``</div>`` and ``</Widget>`` are indistinguishable from a regex start by
    preceding-token rules alone, so the shape after the slash decides. A regex
    literal never ends a fragment with a bare ``>`` on the same span.
    """

    if index == 0 or masked[index - 1] != "<":
        return False
    return bool(re.match(r"/[A-Za-z][\w.]*\s*[/]?>", masked[index:]))


def _is_regex_start(masked: str, index: int) -> bool:
    if _is_jsx_closing_tag(masked, index):
        return False
    character, word = _previous_significant(masked, index)
    if word:
        return word in _REGEX_PRECEDING_WORDS
    return character in _REGEX_PRECEDERS


def _mask(source: str) -> _Masked:
    """Blank out comments and literal bodies while preserving every offset."""

    out: list[str] = list(source)
    literals: list[dict[str, Any]] = []
    uncertainties: list[dict[str, Any]] = []
    index = 0
    length = len(source)

    def blank(start: int, end: int) -> None:
        for offset in range(start, end):
            if out[offset] != "\n":
                out[offset] = _MASK

    while index < length:
        character = source[index]

        if character == "/" and index + 1 < length:
            following = source[index + 1]
            if following == "/":
                match = _LINE_COMMENT.match(source, index)
                end = match.end() if match else length
                blank(index, end)
                index = end
                continue
            if following == "*":
                match = _BLOCK_COMMENT.match(source, index)
                if match is None:
                    raise JavaScriptSourceError(
                        f"Unterminated block comment at line {source.count(chr(10), 0, index) + 1}"
                    )
                blank(index, match.end())
                index = match.end()
                continue
            if _is_regex_start("".join(out), index):
                regex_end = _scan_regex(source, index)
                if regex_end is None:
                    uncertainties.append(
                        {
                            "line": source.count("\n", 0, index) + 1,
                            "reason": "unterminated_regex_literal",
                        }
                    )
                    index += 1
                    continue
                blank(index, regex_end)
                index = regex_end
                continue

        if character in _QUOTE_CHARS:
            string_end = _scan_string(source, index)
            if string_end is None:
                raise JavaScriptSourceError(
                    f"Unterminated string literal at line "
                    f"{source.count(chr(10), 0, index) + 1}"
                )
            quote = character
            blank(index + 1, string_end - 1)
            literals.append(
                {
                    "quote": quote,
                    "start": index,
                    "end": string_end,
                    "value": source[index + 1 : string_end - 1],
                }
            )
            out[index] = quote
            out[string_end - 1] = quote
            index = string_end
            continue

        index += 1

    return _Masked("".join(out), literals, uncertainties)


def _scan_string(source: str, start: int) -> int | None:
    """Return the index just past a string literal, or None when unterminated."""

    quote = source[start]
    index = start + 1
    length = len(source)
    while index < length:
        character = source[index]
        if character == "\\":
            index += 2
            continue
        if character == quote:
            return index + 1
        # An unescaped newline terminates a single-quoted string. Template
        # literals may span lines, so they are read until their closing backtick.
        if character == "\n" and quote != "`":
            return None
        index += 1
    return None


def _scan_regex(source: str, start: int) -> int | None:
    """Return the index just past a regex literal, or None when unterminated."""

    index = start + 1
    length = len(source)
    in_class = False
    while index < length:
        character = source[index]
        if character == "\\":
            index += 2
            continue
        if character == "\n":
            return None
        if character == "[":
            in_class = True
        elif character == "]":
            in_class = False
        elif character == "/" and not in_class:
            index += 1
            while index < length and source[index].isalpha():
                index += 1
            return index
        index += 1
    return None


def _match_brace(masked: str, open_index: int) -> int:
    """Return the index of the brace matching the one at ``open_index``."""

    depth = 0
    index = open_index
    length = len(masked)
    while index < length:
        character = masked[index]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return index
        elif character in "([{":
            index = _skip_group(masked, index)
            continue
        index += 1
    return length - 1


def _skip_group(masked: str, open_index: int) -> int:
    """Return the index just past a bracketed group starting at ``open_index``.

    Only the matching bracket type balances the group, so a malformed mix such
    as ``a(b]`` cannot silently consume a following region.
    """

    pairs = {"(": ")", "[": "]", "{": "}"}
    closing = pairs[masked[open_index]]
    depth = 0
    index = open_index
    length = len(masked)
    while index < length:
        if masked[index] == masked[open_index]:
            depth += 1
        elif masked[index] == closing:
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return length


def _observed_effect(callee: str) -> str | None:
    """Classify a dotted callee against a small, explicit heuristic set."""

    parts = [part.strip() for part in callee.split(".")]
    if not parts or not _DOTTED_RE.match(callee.replace(" ", "")):
        return None
    if any(not part for part in parts):
        return None
    for part in parts:
        effect = _EFFECT_BY_LEAF.get(part)
        if effect is not None:
            return effect
    return None


class _CallCollector:
    """Collects call observations inside a bounded source region."""

    def __init__(self, limit: int) -> None:
        self._calls: list[dict[str, Any]] = []
        self._limit = limit

    def scan(self, masked: str, start: int, end: int, *, skip: frozenset[str]) -> None:
        for match in _CALL_RE.finditer(masked, start, end):
            raw = match.group("callee")
            parts = [part.strip() for part in raw.split(".")]
            if not parts:
                continue
            leaf = parts[-1]
            if leaf in _KEYWORDS or raw.replace(" ", "") in skip:
                continue
            if any(part in _KEYWORDS for part in parts):
                continue
            if len(self._calls) >= self._limit:
                raise JavaScriptSourceError(
                    f"A scope exceeds the {self._limit} call observation limit"
                )
            self._calls.append(
                {
                    "callee": ".".join(parts),
                    "line": masked.count("\n", 0, match.start()) + 1,
                    "observed_effect": _observed_effect(raw),
                }
            )

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self._calls


def _leading_comment(
    source_lines: list[str], start_line: int, *, window: int = 3
) -> str | None:
    """Return a contiguous block comment directly above a declaration."""

    collected: list[str] = []
    index = start_line - 2
    while index >= 0 and len(collected) < window:
        line = source_lines[index].strip()
        if line.startswith("//"):
            collected.append(line[2:].strip())
            index -= 1
            continue
        if line.endswith("*/"):
            body: list[str] = []
            cursor = index
            while cursor >= 0 and "/*" not in source_lines[cursor]:
                body.append(source_lines[cursor].strip())
                cursor -= 1
            if cursor < 0:
                break
            opening = source_lines[cursor].strip()
            if not opening.startswith("/*"):
                break
            remainder = opening[2:]
            if "*/" in remainder:
                body.append(remainder.split("*/")[0].strip())
                collected = body + collected
                break
            body.append(remainder)
            collected = body + collected
            index = cursor - 1
            continue
        break
    if not collected:
        return None
    text = "\n".join(reversed(collected)).strip()
    return text or None


def _doc_summary(text: str | None, *, limit: int = MAX_DOC_CHARS) -> str | None:
    if not text:
        return None
    collapsed = " ".join(text.split())
    return collapsed[:limit] or None


def _collect_imports(
    masked: _Masked,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Collect import bindings, recovering literal values by source offset.

    The masked text preserves quote characters and byte offsets but blanks
    literal bodies, so the real module specifier is read back from the literal
    table using the opening quote position the match reported.
    """

    text = masked.text
    by_start: dict[int, Any] = {item["start"]: item["value"] for item in masked.literals}
    imports: list[dict[str, Any]] = []
    dynamic: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str, str]] = set()

    def value_at(match: re.Match[str]) -> str:
        # The masked text blanks literal bodies, so the real specifier is read
        # back by the opening quote offset. The masked capture is the fallback.
        recovered = by_start.get(match.start("quote"))
        if isinstance(recovered, str):
            return recovered
        return str(match.group("module"))

    def add(kind: str, module: str, line: int, name: str | None, clause: str | None) -> None:
        key = (kind, line, module, name or "")
        if key in seen:
            return
        seen.add(key)
        if len(imports) >= MAX_IMPORTS:
            raise JavaScriptSourceError(
                f"JavaScript source exceeds the {MAX_IMPORTS}-import analysis limit"
            )
        imports.append(
            {
                "kind": kind,
                "module": module,
                "name": name,
                "alias": None,
                "line": line,
                "names": clause,
            }
        )

    for match in _IMPORT_RE.finditer(text):
        line = masked.line_of(match.start())
        clause = match.group("clause").strip()
        module = value_at(match)
        if match.group("keyword") == "export":
            add("re_export", module, line, None, clause)
            continue
        add("import", module, line, None, clause)
        specifier = _IMPORT_SPECIFIER_RE.search(match.group("clause"))
        if specifier is not None:
            for raw in specifier.group("names").split(","):
                identifier = raw.split(" as ")[0].strip()
                if identifier:
                    add("named_import", module, line, identifier, None)

    for match in _BARE_IMPORT_RE.finditer(text):
        add("side_effect_import", value_at(match), masked.line_of(match.start()), None, None)

    for match in _REQUIRE_RE.finditer(text):
        add("require", value_at(match), masked.line_of(match.start()), None, None)

    for match in _DYNAMIC_IMPORT_RE.finditer(text):
        dynamic.append({"line": masked.line_of(match.start())})

    imports.sort(key=lambda item: (item["line"], item["module"], item["name"] or ""))
    return imports, dynamic


def _collect_symbols(
    source: str,
    masked: _Masked,
    language: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Collect declarations with bounded nesting and per-scope call collection.

    Declarations are located by source offset first, then converted to line
    numbers in one place, so a byte offset can never be reported as a line.
    """

    text = masked.text
    source_lines = source.splitlines()
    symbols: list[dict[str, Any]] = []
    base_calls = _CallCollector(MAX_CALL_OBSERVATIONS)
    base_calls.scan(text, 0, len(text), skip=frozenset())
    module_calls = base_calls.calls

    class_body_ranges: list[tuple[int, int, str]] = []

    def add_symbol(
        kind: str,
        name: str,
        scope: tuple[str, ...],
        start_offset: int,
        end_offset: int,
        doc: str | None,
        calls: list[dict[str, Any]],
        *,
        async_function: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if len(symbols) >= MAX_SYMBOLS:
            raise JavaScriptSourceError(
                f"JavaScript source exceeds the {MAX_SYMBOLS}-symbol analysis limit"
            )
        start_line = masked.line_of(start_offset)
        end_line = masked.line_of(min(end_offset, len(text) - 1))
        row = {
            "kind": kind,
            "name": name,
            "qualified_name": ".".join((*scope, name)),
            "start_line": start_line,
            "end_line": max(start_line, end_line),
            "docstring": _doc_summary(doc, limit=MAX_DOC_CHARS),
            "async": async_function,
            "calls": calls,
        }
        if extra:
            row.update(extra)
        symbols.append(row)

    def scoped_calls(start: int, end: int, own_name: str) -> list[dict[str, Any]]:
        collector = _CallCollector(MAX_CALLS_PER_SCOPE)
        collector.scan(text, start, end, skip=frozenset({own_name}))
        return collector.calls

    # Classes first: their bodies supply method symbols, and their ranges let
    # the later top-level patterns skip anything nested inside a class body.
    for match in _CLASS_RE.finditer(text):
        start = match.start()
        brace = text.find("{", match.end())
        if brace == -1:
            continue
        end = _match_brace(text, brace)
        name = match.group("name")
        start_line = masked.line_of(start)
        add_symbol(
            "class",
            name,
            (),
            start,
            end,
            _leading_comment(source_lines, start_line),
            scoped_calls(brace + 1, end, name),
            extra={"extends": _extends_clause(text, match.end(), brace)},
        )
        class_body_ranges.append((start, end, name))

    for start, end, class_name in class_body_ranges:
        seen: set[tuple[str, int]] = set()
        for match in _iter_methods(text, start, end):
            name = match.group("name")
            body_start = text.find("{", match.end())
            method_end = _match_brace(text, body_start) if body_start != -1 else end
            key = (name, match.start())
            if key in seen:
                continue
            seen.add(key)
            add_symbol(
                "method",
                name,
                (class_name,),
                match.start(),
                min(method_end, end),
                _leading_comment(source_lines, masked.line_of(match.start())),
                scoped_calls(match.end(), method_end, name),
                extra={"accessor": "get" if _is_accessor(text, match) else None},
            )

    def in_class_body(position: int) -> bool:
        return any(start <= position <= end for start, end, _ in class_body_ranges)

    for match in _FUNCTION_RE.finditer(text):
        if in_class_body(match.start()):
            continue
        name = match.group("name")
        start_line = masked.line_of(match.start())
        body_start = text.find("{", match.end())
        end = _match_brace(text, body_start) if body_start != -1 else len(text) - 1
        add_symbol(
            "function",
            name,
            (),
            match.start(),
            end,
            _leading_comment(source_lines, start_line),
            scoped_calls(body_start + 1, end, name),
            async_function=match.group(0).lstrip().startswith("async"),
        )

    for pattern, kind in ((_INTERFACE_RE, "interface"), (_ENUM_RE, "enum")):
        for match in pattern.finditer(text):
            if in_class_body(match.start()):
                continue
            start_line = masked.line_of(match.start())
            add_symbol(
                kind,
                match.group("name"),
                (),
                match.start(),
                _span_end(text, match.end(), terminator=";"),
                _leading_comment(source_lines, start_line),
                [],
            )

    for match in _TYPE_RE.finditer(text):
        if in_class_body(match.start()):
            continue
        start_line = masked.line_of(match.start())
        add_symbol(
            "type",
            match.group("name"),
            (),
            match.start(),
            _span_end(text, match.end(), terminator=";"),
            _leading_comment(source_lines, start_line),
            [],
        )

    for match in _BINDING_RE.finditer(text):
        if in_class_body(match.start()):
            continue
        name = match.group("name")
        start_line = masked.line_of(match.start())
        # A binding is a function when its initializer is a function expression
        # or an arrow, including a typed arrow such as ``(x: T): U => body``.
        arrow = _arrow_body_end(text, match.start("body"))
        if "=>" in match.group("body") or "function" in match.group("body") or arrow is not None:
            if arrow is not None:
                end = arrow
            else:
                brace = text.find("{", match.end())
                end = (
                    _match_brace(text, brace)
                    if brace != -1 and brace - match.end() < MAX_INLINE_BODY_CHARS
                    else _span_end(text, match.end(), terminator=";")
                )
            add_symbol(
                "function",
                name,
                (),
                match.start(),
                end,
                _leading_comment(source_lines, start_line),
                scoped_calls(match.end(), end, name),
                async_function=bool(match.group("value")),
            )
        else:
            end = _span_end(text, match.end(), terminator=";")
            add_symbol(
                "constant",
                name,
                (),
                match.start(),
                end,
                _leading_comment(source_lines, start_line),
                [],
                extra={"declared_type": (match.group("type") or "").strip() or None},
            )

    # Report remaining top-level bindings that are plain values, so a declared
    # configuration constant is visible in the map.
    reported = {symbol["name"] for symbol in symbols}
    for match in _PLAIN_BINDING_RE.finditer(text):
        if in_class_body(match.start()):
            continue
        name = match.group("name")
        if name in reported:
            continue
        start_line = masked.line_of(match.start())
        add_symbol(
            "constant",
            name,
            (),
            match.start(),
            _span_end(text, match.end(), terminator=";"),
            _leading_comment(source_lines, start_line),
            [],
            extra={"declared_type": (match.group("type") or "").strip() or None},
        )

    symbols.sort(key=lambda item: (item["start_line"], item["qualified_name"]))
    return symbols, module_calls


def _is_accessor(masked: str, match: re.Match[str]) -> bool:
    """Return True when a method match is preceded by a get/set keyword."""

    prefix = masked[max(0, match.start() - 6) : match.start()]
    return bool(re.search(r"\b(get|set)\s+$", prefix))


def _extends_clause(masked: str, after: int, brace: int) -> str | None:
    clause = masked[after:brace].strip()
    if not clause.startswith("extends"):
        return None
    body = clause[len("extends") :].strip()
    body = body.split("{")[0].strip()
    return " ".join(body.split()) or None


_MEMBER_MODIFIERS = (
    "static",
    "async",
    "get",
    "set",
    "public",
    "private",
    "protected",
    "readonly",
    "abstract",
    "override",
    "declare",
)


def _iter_methods(masked: str, start: int, end: int):
    """Yield method-like member declarations found directly in a class body.

    The walker tracks nesting depth and only considers a position that starts a
    new class member, so a call inside a member body can never be mistaken for a
    declaration.
    """

    brace = masked.find("{", start)
    if brace == -1 or brace > end:
        return
    index = brace + 1
    depth = 0
    member_start: int | None = index
    while index < end:
        character = masked[index]
        if character in "([{":
            depth += 1
            index += 1
            continue
        if character in ")]}":
            depth -= 1
            if depth < 0:
                return
            index += 1
            continue
        if depth == 0 and character in ";\n":
            if member_start is not None:
                match = _match_member(masked, member_start, index)
                if match is not None:
                    yield match
            index += 1
            member_start = index
            continue
        index += 1

    if member_start is not None:
        match = _match_member(masked, member_start, end)
        if match is not None:
            yield match


def _match_member(masked: str, start: int, end: int) -> re.Match[str] | None:
    """Return a method match when a class member span declares a method."""

    prefix_end = start
    while prefix_end < end and masked[prefix_end] in " \t\r\n":
        prefix_end += 1
    # A member is a declaration only when it opens with a modifier or a name;
    # a member beginning with a keyword expression is not a method.
    accessor = _GETTER_RE.match(masked, prefix_end, end)
    if accessor is not None:
        return accessor
    match = _METHOD_RE.match(masked, prefix_end, end)
    if match is None:
        return None
    name = match.group("name")
    if name in _MEMBER_MODIFIERS or name in _KEYWORDS:
        return None
    return match


def _arrow_body_end(masked: str, start: int) -> int | None:
    """Return the end offset when a binding initializer is an arrow function.

    Both ``(x) => body`` and a typed ``(x: T): Promise<U> => body`` begin with a
    parenthesis, so the arrow is located explicitly after the parameter list
    rather than assumed.
    """

    window = masked[start : start + MAX_INLINE_BODY_CHARS]
    if not window.startswith("("):
        return None
    parameters_end = _skip_group(masked, start)
    if parameters_end <= start:
        return None
    tail = masked[parameters_end : parameters_end + MAX_INLINE_BODY_CHARS]
    arrow = re.match(r"(?:\s*:\s*[^=]{0,120}?)?\s*=>", tail)
    if arrow is None:
        return None
    body_start = parameters_end + arrow.end()
    while body_start < len(masked) and masked[body_start] in " \t\r\n":
        body_start += 1
    if body_start < len(masked) and masked[body_start] == "{":
        return _match_brace(masked, body_start)
    return _span_end(masked, body_start, terminator=";")


def _span_end(masked: str, start: int, *, terminator: str) -> int:
    index = start
    length = len(masked)
    while index < length:
        if masked[index] == terminator:
            return index
        if masked[index] in "([":
            index = _skip_group(masked, index)
            continue
        if masked[index] == "\n" and terminator == ";":
            return index
        index += 1
    return length


def _guard_nesting(masked: str) -> None:
    """Refuse source nested deeper than the analysis limit.

    The check runs over already-masked text, so braces inside strings, comments,
    templates, and regex literals cannot trigger or hide a real nesting depth.
    """

    depth = 0
    for character in masked:
        if character == "{":
            depth += 1
            if depth > MAX_NESTING_DEPTH:
                raise JavaScriptSourceError(
                    f"JavaScript source exceeds the {MAX_NESTING_DEPTH}-level nesting limit"
                )
        elif character == "}":
            depth = max(0, depth - 1)


def scan_javascript_source(
    source: str,
    *,
    path: str = "<memory>",
    language: str = "javascript",
) -> dict[str, Any]:
    """Return deterministic structural facts for JS/TS source without execution."""

    if not isinstance(source, str):
        raise JavaScriptSourceError("JavaScript source must be text")
    if language not in _JSX_LANGUAGES:
        raise JavaScriptSourceError(f"Unsupported JavaScript-family language {language!r}")
    try:
        raw = source.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise JavaScriptSourceError("JavaScript source is not valid UTF-8 text") from exc
    if len(raw) > MAX_SOURCE_BYTES:
        raise JavaScriptSourceError(
            f"JavaScript source exceeds the {MAX_SOURCE_BYTES}-byte analysis limit"
        )

    masked = _mask(source)
    _guard_nesting(masked.text)
    imports, dynamic = _collect_imports(masked)
    symbols, module_calls = _collect_symbols(source, masked, language)

    document = build_source_analysis(
        language=language,
        path=path,
        source=source,
        support_level=(
            "declarations_imports_and_direct_call_observations"
            if language == "typescript"
            else "declarations_imports_and_direct_call_observations"
        ),
        imports=imports,
        module_calls=module_calls,
        symbols=symbols,
        module_docstring=_leading_comment(source.splitlines(), 1, window=2),
        extra_limitations=[
            "This is a bounded scanner, not a parser: complex generic, conditional, and decorator "
            "syntax may be reported approximately.",
            "Dynamic import() call sites are counted but their module specifiers are not resolved.",
            "Call targets are not resolved to definitions, and TypeScript types are not checked.",
        ],
    )
    document["analysis_mode"] = "structural_scan"
    document["dynamic_imports"] = dynamic
    document["uncertainties"] = masked.uncertainties
    if masked.uncertainties:
        document["support_level"] = "partial_declarations_with_reported_uncertainties"
    return document


def scan_javascript_file(path: str | Path) -> dict[str, Any]:
    """Read one bounded UTF-8 JavaScript or TypeScript file and scan it."""

    source_path = Path(path)
    try:
        with source_path.open("rb") as source_file:
            raw = source_file.read(MAX_SOURCE_BYTES + 1)
    except OSError as exc:
        raise JavaScriptSourceError(f"Unable to read source: {exc}") from exc
    if len(raw) > MAX_SOURCE_BYTES:
        raise JavaScriptSourceError(
            f"JavaScript source exceeds the {MAX_SOURCE_BYTES}-byte analysis limit"
        )
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise JavaScriptSourceError("JavaScript source must be UTF-8 encoded") from exc

    language = "typescript" if source_path.suffix.lower() in {
        ".ts",
        ".tsx",
        ".mts",
        ".cts",
    } else "javascript"
    document = scan_javascript_source(source, path=source_path.as_posix(), language=language)
    document["source"]["sha256"] = hashlib.sha256(raw).hexdigest()
    return document


__all__ = [
    "JavaScriptSourceError",
    "MAX_CALL_OBSERVATIONS",
    "MAX_CALLS_PER_SCOPE",
    "MAX_IMPORTS",
    "MAX_SOURCE_BYTES",
    "MAX_SYMBOLS",
    "scan_javascript_file",
    "scan_javascript_source",
]
