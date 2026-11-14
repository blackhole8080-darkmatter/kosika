"""Kosika logs: typed, add-only, tamper-evident JSON Lines.

A program keeps a log by declaring a `system "log"` tool:

    record Step { agent: Text tool: Text ok: Bool saw: Text }
    record Add { path: Text by: Text entry: Step }
    record Added { n: Number hash: Text }
    record Path { path: Text }
    record Steps { rows: Step[] ok: Bool problem: Text }

    tool keep  { source system "log" operation: "append" access: observe input: Add  output: Added }
    tool steps { source system "log" operation: "read"   access: observe input: Path output: Steps }

A log file (`.kl` by custom) is plain JSON, one value per line:

    {"kosika":"log","type":"Step","fields":["agent","tool","ok","saw"],"shape":"5d1c..."}
    [1,"2026-11-11T22:44:20Z","browser_hands","0000...","browser_hands","web",true,"..."]

The first line says which record the log holds: its name, its fields in
order, and `shape`, the first 16 hex digits of the SHA-256 of the record
written as `Step{agent:Text,tool:Text,ok:Bool,saw:Text}`. Every later line is
a tuple: the entry's number from 1, when it was added (UTC), who added it,
`prev` (the SHA-256 of the line before, the header for entry 1), then the
record's fields in that order. Changing or removing any line breaks the
chain at the next one, and reading says where.

Appending only ever adds a line at the end, inside the granted folder, so a
log tool may be `access: observe`: it cannot change or remove what is there.
Every entry is checked against the record before it is written, and again
when it is read; a log of another record, or of the same name with another
shape, is refused.

The Go engine (native/klog.go) writes the same bytes for the same entries.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .ast import Program, ToolDecl
from .schema import schema_for_type, validate

#: How many hex digits of the record's hash the header keeps as its shape.
SHAPE_DIGITS = 16
#: A log larger than this is not read whole.
MAX_LOG_BYTES = 16 * 1024 * 1024
#: Entries one read gives back at most.
MAX_ROWS = 100_000


class LogError(RuntimeError):
    """A log that cannot be written or read as asked."""


def _record(program: Program, name: str):
    for record in program.records:
        if record.name == name:
            return record
    raise LogError(f"{name!r} is not a record of this program")


def entry_type(program: Program, tool: ToolDecl) -> str:
    """The record a log tool keeps: the type of `entry` for append, of the
    items of `rows` for read."""

    if tool.operation == "append":
        fields = {f.name: f.type_name for f in _record(program, tool.input or "").fields}
        return fields["entry"]
    fields = {f.name: f.type_name for f in _record(program, tool.output).fields}
    return fields["rows"][:-2]


def shape_text(program: Program, name: str) -> str:
    record = _record(program, name)
    return name + "{" + ",".join(f"{f.name}:{f.type_name}" for f in record.fields) + "}"


def shape_of(program: Program, name: str) -> str:
    return hashlib.sha256(shape_text(program, name).encode("utf-8")).hexdigest()[:SHAPE_DIGITS]


def _plain(value: Any) -> Any:
    """A value as the log writes it: whole numbers without a decimal point,
    so both engines write the same digits."""

    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise LogError("a log cannot hold a number that is not finite")
        if float(value).is_integer() and abs(value) < 2**53:
            return int(value)
        return float(value)
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        # Records inside an entry are written with their keys sorted, so the
        # order a program wrote them in does not change the bytes.
        return {key: _plain(value[key]) for key in sorted(value)}
    return value


def line_of(value: Any) -> str:
    """One line of a log: compact JSON, letters as themselves, written the
    way Go's encoder writes them (it always escapes U+2028 and U+2029)."""

    text = json.dumps(_plain(value), ensure_ascii=False, separators=(",", ":"))
    return text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def hash_of(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def header(program: Program, name: str) -> str:
    fields = [f.name for f in _record(program, name).fields]
    # Written key by key, in this order: the header is not an entry, and
    # its keys are not sorted.
    return (
        '{"kosika":"log","type":' + line_of(name) + ',"fields":' + line_of(fields)
        + ',"shape":' + line_of(shape_of(program, name)) + "}"
    )


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    if path.stat().st_size > MAX_LOG_BYTES:
        raise LogError(f"{path.name!r} is larger than 16 MiB")
    text = path.read_text(encoding="utf-8")
    return [line for line in text.split("\n") if line != ""]


def check_header(program: Program, name: str, first: str) -> str | None:
    """Why this header is not one for the record, or None."""

    try:
        head = json.loads(first)
    except ValueError:
        return "line 1 is not JSON"
    if not isinstance(head, dict) or head.get("kosika") != "log":
        return "line 1 is not a Kosika log header"
    if head.get("type") != name:
        return f"the log holds {head.get('type')!r}, not {name!r}"
    if head.get("shape") != shape_of(program, name):
        return (
            f"the log's {name} has another shape ({head.get('shape')}); "
            f"this program's is {shape_of(program, name)}"
        )
    return None


def read_log(program: Program, name: str, path: Path) -> tuple[list[dict[str, Any]], str]:
    """The log's entries as records, in order, and "" or the first problem.
    Entries before the problem are given back; none after it."""

    lines = _lines(path)
    if not lines:
        return [], ""
    problem = check_header(program, name, lines[0])
    if problem:
        return [], problem
    fields = [f.name for f in _record(program, name).fields]
    schema = schema_for_type(program, name)
    rows: list[dict[str, Any]] = []
    prev = hash_of(lines[0])
    for at, line in enumerate(lines[1:], start=2):
        if len(rows) >= MAX_ROWS:
            return rows, f"more than {MAX_ROWS} entries; only the first are given"
        try:
            cells = json.loads(line)
        except ValueError:
            return rows, f"line {at} is not JSON"
        if not isinstance(cells, list) or len(cells) != 4 + len(fields):
            return rows, f"line {at} is not an entry of {len(fields)} fields"
        n, _when, _by, before = cells[:4]
        if n != at - 1:
            return rows, f"line {at} says entry {n}, but it is entry {at - 1}: an entry was removed or moved"
        if before != prev:
            return rows, f"line {at} does not follow line {at - 1}: line {at - 1} was changed"
        entry = dict(zip(fields, cells[4:]))
        try:
            validate(entry, schema)
        except Exception as exc:  # the schema's own message says which field
            return rows, f"line {at} is not a {name}: {exc}"
        rows.append(entry)
        prev = hash_of(line)
    return rows, ""


def append_log(program: Program, name: str, path: Path, by: str, entry: dict[str, Any]) -> dict[str, Any]:
    validate(entry, schema_for_type(program, name))
    lines = _lines(path)
    added: list[str] = []
    if not lines:
        lines = [header(program, name)]
        added.append(lines[0])
    else:
        # Nothing is added to a log that was changed: the new entry would
        # chain onto the change and hide it.
        _rows, problem = read_log(program, name, path)
        if problem and not problem.startswith("more than"):
            raise LogError(f"{path.name}: {problem}; nothing was added")
    fields = [f.name for f in _record(program, name).fields]
    line = line_of([len(lines), _now(), by, hash_of(lines[-1])] + [entry[f] for f in fields])
    added.append(line)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write("".join(text + "\n" for text in added))
    return {"n": len(lines), "hash": hash_of(line)}


def bind_log_tool(
    program: Program, tool: ToolDecl, inside: Callable[[str], Path]
) -> Callable[[ToolDecl, dict[str, Any]], Any]:
    """A `system "log"` tool, its paths resolved inside the granted folder."""

    name = entry_type(program, tool)

    def append(_tool: ToolDecl, arguments: dict[str, Any]) -> dict[str, Any]:
        return append_log(program, name, inside(arguments["path"]), arguments["by"], arguments["entry"])

    def read(_tool: ToolDecl, arguments: dict[str, Any]) -> dict[str, Any]:
        rows, problem = read_log(program, name, inside(arguments["path"]))
        return {"rows": rows, "ok": problem == "", "problem": problem}

    return append if tool.operation == "append" else read


def show(path: Path, named: bool = False, width: int = 60) -> str:
    """A log as a person reads it: a table, or one named record per line.
    It needs no program: the header names the fields."""

    lines = _lines(path)
    if not lines:
        return "(empty log)"
    head = json.loads(lines[0])
    fields: list[str] = list(head.get("fields") or [])
    out: list[str] = []
    rows: list[list[str]] = []
    prev = hash_of(lines[0])
    broken = ""
    for at, line in enumerate(lines[1:], start=2):
        cells = json.loads(line)
        if not broken and (cells[3] != prev or cells[0] != at - 1):
            broken = f"line {at - 1} was changed or removed (line {at} no longer follows it)"
        prev = hash_of(line)
        if named:
            record = {"n": cells[0], "at": cells[1], "by": cells[2]}
            record.update(zip(fields, cells[4:]))
            out.append(json.dumps(record, ensure_ascii=False))
            continue
        row = [str(cells[0]), str(cells[1])[11:19] or str(cells[1]), str(cells[2])]
        for value in cells[4:]:
            if value is True:
                text = "✓"
            elif value is False:
                text = "✗"
            elif isinstance(value, str):
                text = value
            else:
                text = json.dumps(value, ensure_ascii=False)
            text = " ".join(text.split())
            row.append(text if len(text) <= width else text[: width - 1] + "…")
        rows.append(row)
    if not named:
        titles = ["n", "at", "by"] + fields
        widths = [max(len(t), *(len(r[i]) for r in rows)) if rows else len(t) for i, t in enumerate(titles)]
        out.append("  ".join(t.ljust(w) for t, w in zip(titles, widths)).rstrip())
        for row in rows:
            out.append("  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip())
    title = f"{head.get('type')} log, {len(lines) - 1} entries, shape {head.get('shape')}"
    status = f"CHANGED: {broken}" if broken else "chain intact"
    return title + "\n" + "\n".join(out) + "\n" + status


def main(argv: list[str] | None = None) -> int:
    """`python -m kosika.klog show FILE [--named]`: a log, readable."""

    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="kosika log")
    sub = parser.add_subparsers(dest="command", required=True)
    shown = sub.add_parser("show", help="print a log as a table, or as named records")
    shown.add_argument("path")
    shown.add_argument("--named", action="store_true")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    text = show(Path(args.path), named=args.named)
    print(text)
    return 1 if "\nCHANGED:" in text else 0


if __name__ == "__main__":
    raise SystemExit(main())
