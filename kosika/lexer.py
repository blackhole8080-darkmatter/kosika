from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    line: int
    column: int


class LexError(ValueError):
    pass


_TOKEN_RE = re.compile(
    r"(?P<SPACE>[ \t\r]+)|"
    r"(?P<COMMENT>//[^\n]*)|"
    r"(?P<NEWLINE>\n)|"
    r"(?P<ARROW>->)|"
    r"(?P<OP>==|!=|<=|>=|\+=|-=|\*=|/=)|"
    r"(?P<STRING>\"(?:\\.|[^\"\\])*\")|"
    r"(?P<NUMBER>\d+(?:\.\d+)?)|"
    r"(?P<IDENT>[A-Za-z_][A-Za-z0-9_]*)|"
    r"(?P<SYMBOL>[{}\[\]():,.?=+\-*/%<>])"
)


_ESCAPE_RE = re.compile(
    r"\\u[0-9a-fA-F]{4}|\\U[0-9a-fA-F]{8}|\\N\{[^}]*\}"
    r"|\\x[0-9a-fA-F]{2}|\\[0-7]{1,3}|\\[\s\S]"
)


def _decode_string(raw: str) -> str:
    """Decode escapes in a string token without corrupting non-ASCII text.

    Escape sequences such as \\n, \\" and \\u00e9 are pure ASCII, so each is
    decoded one at a time with unicode_escape, preserving the escape set the
    whole-token decode used to support. Literal multi-byte characters must be
    copied through unchanged: a whole-token
    bytes(...).decode("unicode_escape") would reinterpret their UTF-8 bytes as
    latin-1 and produce mojibake.
    """
    body = raw[1:-1]
    pieces: list[str] = []
    index = 0
    while index < len(body):
        character = body[index]
        if character != "\\":
            pieces.append(character)
            index += 1
            continue
        match = _ESCAPE_RE.match(body, index)
        if match is None:  # Unreachable: a token cannot end in a lone backslash.
            pieces.append(character)
            index += 1
            continue
        escape = match.group(0)
        if escape[-1].isascii():
            # Escapes are pure ASCII, so unicode_escape cannot mangle them.
            pieces.append(escape.encode("ascii").decode("unicode_escape"))
        else:
            # A backslash before a literal non-ASCII character is not a known
            # escape: keep both characters as written.
            pieces.append(escape)
        index = match.end()
    return "".join(pieces)


def lex(source: str) -> list[Token]:
    tokens: list[Token] = []
    position = 0
    line = 1
    column = 1
    while position < len(source):
        match = _TOKEN_RE.match(source, position)
        if not match:
            excerpt = source[position : position + 20]
            raise LexError(f"Unexpected character at {line}:{column}: {excerpt!r}")
        kind = match.lastgroup or ""
        value = match.group(0)
        if kind == "NEWLINE":
            line += 1
            column = 1
        elif kind not in {"SPACE", "COMMENT"}:
            if kind == "STRING":
                value = _decode_string(value)
            tokens.append(Token(kind, value, line, column))
            column += len(match.group(0))
        else:
            column += len(value)
        position = match.end()
    tokens.append(Token("EOF", "", line, column))
    return tokens
