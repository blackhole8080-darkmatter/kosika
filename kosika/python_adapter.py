"""Read-only structural analysis for Python source files.

This adapter reports syntax-backed facts (definitions, imports, and calls). It
intentionally does not claim to infer full behavior, resolve dynamic dispatch,
or execute the source.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .source_schema import SourceAdapterError

MAX_SOURCE_BYTES = 1024 * 1024
MAX_SYMBOLS = 2000
MAX_IMPORTS = 4000
MAX_CALLS_PER_SCOPE = 2000
MAX_CALL_OBSERVATIONS = 20000


class PythonSourceError(SourceAdapterError):
    """Raised when Python source cannot be analyzed within adapter limits."""

@dataclass(frozen=True)
class PythonSymbol:
    kind: str
    name: str
    qualified_name: str
    start_line: int
    end_line: int
    docstring: str | None
    calls: tuple[dict[str, Any], ...]
    async_function: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "docstring": self.docstring,
            "async": self.async_function,
            "calls": list(self.calls),
        }


class _StructureVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.symbols: list[PythonSymbol] = []
        self.imports: list[dict[str, Any]] = []
        self.scope: list[str] = []
        self._calls: list[dict[str, Any]] | None = []
        self._call_count = 0
        self.module_calls = self._calls

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if len(self.imports) >= MAX_IMPORTS:
                raise PythonSourceError(
                    f"Python source exceeds the {MAX_IMPORTS}-import analysis limit"
                )
            self.imports.append(
                {
                    "kind": "import",
                    "module": alias.name,
                    "name": None,
                    "alias": alias.asname,
                    "line": node.lineno,
                }
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if len(self.imports) >= MAX_IMPORTS:
                raise PythonSourceError(
                    f"Python source exceeds the {MAX_IMPORTS}-import analysis limit"
                )
            self.imports.append(
                {
                    "kind": "from_import",
                    "module": node.module or "",
                    "name": alias.name,
                    "alias": alias.asname,
                    "level": node.level,
                    "line": node.lineno,
                }
            )

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualified_name = ".".join((*self.scope, node.name))
        for expression in (*node.decorator_list, *node.bases):
            self.visit(expression)
        for keyword in node.keywords:
            self.visit(keyword.value)

        previous_calls = self._calls
        class_calls: list[dict[str, Any]] = []
        self._calls = class_calls
        self.scope.append(node.name)
        try:
            for statement in node.body:
                self.visit(statement)
        finally:
            self.scope.pop()
            self._calls = previous_calls
        self._add_symbol(
            PythonSymbol(
                kind="class",
                name=node.name,
                qualified_name=qualified_name,
                start_line=node.lineno,
                end_line=_end_line(node),
                docstring=ast.get_docstring(node, clean=True),
                calls=tuple(class_calls),
            )
        )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node, async_function=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node, async_function=True)

    def _visit_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, *, async_function: bool
    ) -> None:
        qualified_name = ".".join((*self.scope, node.name))
        arguments = node.args
        evaluated_expressions: list[ast.expr] = [*node.decorator_list, *arguments.defaults]
        evaluated_expressions.extend(
            default for default in arguments.kw_defaults if default is not None
        )
        all_arguments = [
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
        ]
        if arguments.vararg is not None:
            all_arguments.append(arguments.vararg)
        if arguments.kwarg is not None:
            all_arguments.append(arguments.kwarg)
        evaluated_expressions.extend(
            argument.annotation
            for argument in all_arguments
            if argument.annotation is not None
        )
        if node.returns is not None:
            evaluated_expressions.append(node.returns)
        for expression in evaluated_expressions:
            self.visit(expression)

        previous_calls = self._calls
        calls: list[dict[str, Any]] = []
        self._calls = calls
        self.scope.append(node.name)
        try:
            for statement in node.body:
                self.visit(statement)
        finally:
            self.scope.pop()
            self._calls = previous_calls
        self._add_symbol(
            PythonSymbol(
                kind="function",
                name=node.name,
                qualified_name=qualified_name,
                start_line=node.lineno,
                end_line=_end_line(node),
                docstring=ast.get_docstring(node, clean=True),
                calls=tuple(calls),
                async_function=async_function,
            )
        )

    def _add_symbol(self, symbol: PythonSymbol) -> None:
        if len(self.symbols) >= MAX_SYMBOLS:
            raise PythonSourceError(
                f"Python source exceeds the {MAX_SYMBOLS}-symbol analysis limit"
            )
        self.symbols.append(symbol)

    def visit_Call(self, node: ast.Call) -> None:
        if self._calls is not None:
            if self._call_count >= MAX_CALL_OBSERVATIONS:
                raise PythonSourceError(
                    f"Python source exceeds the {MAX_CALL_OBSERVATIONS} call observation limit"
                )
            if len(self._calls) >= MAX_CALLS_PER_SCOPE:
                raise PythonSourceError(
                    f"A scope exceeds the {MAX_CALLS_PER_SCOPE} call observation limit"
                )
            self._call_count += 1
            self._calls.append(
                {
                    "callee": _dotted_name(node.func),
                    "line": node.lineno,
                    "observed_effect": _observed_effect(node.func),
                }
            )
        self.generic_visit(node)


def _end_line(node: ast.AST) -> int:
    return int(getattr(node, "end_lineno", getattr(node, "lineno", 1)))


def _dotted_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Call):
        return f"{_dotted_name(node.func)}(...)"
    return "<dynamic>"


def _observed_effect(node: ast.expr) -> str | None:
    """Classify a few obvious direct calls without claiming complete analysis."""

    callee = _dotted_name(node).lower()
    root = callee.split(".", 1)[0]
    if callee in {"open", "io.open"} or ".write_text" in callee or ".write_bytes" in callee:
        return "filesystem"
    if root in {"subprocess", "os.system", "os.popen"} or callee.startswith(
        ("os.system", "os.popen", "os.exec", "os.spawn")
    ):
        return "process"
    if root in {"socket", "requests", "httpx", "urllib", "aiohttp"}:
        return "network"
    if root in {"sqlite3", "psycopg", "mysql", "sqlalchemy"}:
        return "database"
    return None


def analyze_python_source(source: str, *, path: str = "<memory>") -> dict[str, Any]:
    """Return deterministic structural facts for Python source without execution."""

    if not isinstance(source, str):
        raise PythonSourceError("Python source must be text")
    try:
        source_bytes = source.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise PythonSourceError("Python source is not valid UTF-8 text") from exc
    if len(source_bytes) > MAX_SOURCE_BYTES:
        raise PythonSourceError(
            f"Python source exceeds the {MAX_SOURCE_BYTES}-byte analysis limit"
        )

    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError, TypeError, RecursionError) as exc:
        line = getattr(exc, "lineno", None)
        column = getattr(exc, "offset", None)
        location = f" at line {line}, column {column}" if line else ""
        raise PythonSourceError(f"Python syntax could not be parsed{location}: {exc}") from exc

    visitor = _StructureVisitor()
    try:
        visitor.visit(tree)
    except RecursionError as exc:
        raise PythonSourceError("Python source nesting exceeds the analysis limit") from exc
    if len(visitor.imports) > MAX_IMPORTS:
        raise PythonSourceError(
            f"Python source exceeds the {MAX_IMPORTS}-import analysis limit"
        )

    symbols = sorted(visitor.symbols, key=lambda item: (item.start_line, item.qualified_name))
    imports = sorted(
        visitor.imports,
        key=lambda item: (item["line"], item["module"], item.get("name") or ""),
    )
    effects: list[dict[str, Any]] = []
    for call in visitor.module_calls:
        if call["observed_effect"] is not None:
            effects.append(
                {
                    "symbol": "<module>",
                    "callee": call["callee"],
                    "line": call["line"],
                    "kind": call["observed_effect"],
                }
            )
    for symbol in symbols:
        for call in symbol.calls:
            if call["observed_effect"] is not None:
                effects.append(
                    {
                        "symbol": symbol.qualified_name,
                        "callee": call["callee"],
                        "line": call["line"],
                        "kind": call["observed_effect"],
                    }
                )

    return {
        "schema": "kosika.source-analysis/v1",
        "language": "python",
        "analysis_mode": "structural_ast",
        "support_level": "structure_and_direct_call_observations",
        "source": {
            "path": path,
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
            "byte_length": len(source_bytes),
            "line_count": len(source.splitlines()),
        },
        "module_docstring": ast.get_docstring(tree, clean=True),
        "imports": imports,
        "module_calls": visitor.module_calls,
        "symbols": [symbol.to_dict() for symbol in symbols],
        "observed_effects": effects,
        "limitations": [
            "Call observations are syntactic and are not resolved to definitions.",
            "Dynamic dispatch, reflection, generated code, and runtime behavior are not analyzed.",
            "Observed effects are a small heuristic set; absence does not prove purity or safety.",
            "Calls in type annotations, decorators, defaults, and module/class bodies may run at import time.",
            "Purpose and business intent must come from documentation, tests, or developer input.",
        ],
    }


def analyze_python_file(path: str | Path) -> dict[str, Any]:
    """Read one bounded UTF-8 Python file and analyze it without importing it."""

    source_path = Path(path)
    try:
        with source_path.open("rb") as source_file:
            raw = source_file.read(MAX_SOURCE_BYTES + 1)
    except OSError as exc:
        raise PythonSourceError(f"Unable to read Python source: {exc}") from exc
    if len(raw) > MAX_SOURCE_BYTES:
        raise PythonSourceError(
            f"Python source exceeds the {MAX_SOURCE_BYTES}-byte analysis limit"
        )
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PythonSourceError("Python source must be UTF-8 encoded") from exc
    return analyze_python_source(source, path=source_path.as_posix())
