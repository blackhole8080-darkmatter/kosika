"""Bounded, read-only project indexing built on language-specific adapters."""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter, deque
from pathlib import Path
from typing import Any

from .language_adapters import adapter_for_language, adapter_for_suffix
from .source_schema import SourceAdapterError

MAX_PROJECT_FILES = 40
MAX_PROJECT_ENTRIES = 20000
MAX_PROJECT_SOURCE_BYTES = 4 * 1024 * 1024
MAX_PROJECT_SYMBOLS = 3000
MAX_PROJECT_IMPORTS = 10000
MAX_PROJECT_CALLS = 12000
MAX_CONTEXT_FILES = 8
MAX_CONTEXT_FILE_BYTES = 8 * 1024
MAX_PROJECT_REPORT_BYTES = 180 * 1024

_IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        "build",
        "dist",
        "site-packages",
        "coverage",
        "htmlcov",
        "target",
        "Pods",
        ".next",
        ".turbo",
    }
)
_LANGUAGE_SUFFIXES = {
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".go": "go",
    ".h": "c",
    ".hpp": "cpp",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".kt": "kotlin",
    ".m": "objective-c",
    ".mm": "objective-cpp",
    ".php": "php",
    ".py": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".scala": "scala",
    ".sh": "shell",
    ".swift": "swift",
    ".ts": "typescript",
    ".tsx": "typescript",
}
_DEPENDENCY_FILES = frozenset(
    {
        "Cargo.lock",
        "Gemfile.lock",
        "go.sum",
        "package-lock.json",
        "pnpm-lock.yaml",
        "poetry.lock",
        "requirements.lock",
        "uv.lock",
        "yarn.lock",
    }
)
_CONTEXT_NAMES = frozenset(
    {
        "Cargo.toml",
        "Gemfile",
        "Makefile",
        "README.md",
        "README.rst",
        "README.txt",
        "go.mod",
        "pyproject.toml",
        "requirements.txt",
        "setup.cfg",
        "setup.py",
    }
)


class ProjectAnalysisError(ValueError):
    """Raised when a project cannot be indexed within explicit safety limits."""


def _discover(root: Path) -> tuple[list[Path], list[dict[str, str]]]:
    pending = deque([root])
    analyzed_files: list[Path] = []
    other_files: list[dict[str, str]] = []
    visited_entries = 0
    while pending:
        directory = pending.popleft()
        try:
            with os.scandir(directory) as iterator:
                entries = []
                for entry in iterator:
                    visited_entries += 1
                    if visited_entries > MAX_PROJECT_ENTRIES:
                        raise ProjectAnalysisError(
                            f"Project exceeds the {MAX_PROJECT_ENTRIES}-entry discovery limit"
                        )
                    entries.append(entry)
                entries.sort(key=lambda item: item.name)
        except ProjectAnalysisError:
            raise
        except OSError as exc:
            raise ProjectAnalysisError(
                f"Unable to scan project directory {directory}: {exc}"
            ) from exc
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if (
                        entry.name not in _IGNORED_DIRECTORIES
                        and not entry.name.startswith(".")
                    ):
                        pending.append(Path(entry.path))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError as exc:
                raise ProjectAnalysisError(
                    f"Unable to inspect project entry {entry.path}: {exc}"
                ) from exc

            path = Path(entry.path)
            suffix = path.suffix.lower()
            adapter = adapter_for_suffix(suffix)
            if adapter is not None:
                analyzed_files.append(path)
            elif suffix in _LANGUAGE_SUFFIXES:
                other_files.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "language": _LANGUAGE_SUFFIXES[suffix],
                    }
                )
            if len(analyzed_files) > MAX_PROJECT_FILES:
                raise ProjectAnalysisError(
                    f"Project has more than {MAX_PROJECT_FILES} analyzed files; analyze a smaller source root"
                )
    return (
        sorted(analyzed_files, key=lambda item: item.relative_to(root).as_posix()),
        sorted(other_files, key=lambda item: (item["language"], item["path"])),
    )


def _module_name(relative_path: str) -> str:
    parts = list(Path(relative_path).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _local_import_edges(
    analyses: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    modules: dict[str, list[str]] = {}
    for relative_path in analyses:
        name = _module_name(relative_path)
        if name:
            modules.setdefault(name, []).append(relative_path)

    edges: list[dict[str, Any]] = []
    unresolved: set[str] = set()
    ambiguous: set[str] = set()
    for relative_path, analysis in sorted(analyses.items()):
        source_parts = list(Path(relative_path).with_suffix("").parts)
        package_parts = source_parts[:-1]
        for item in analysis["imports"]:
            if item["kind"] == "import":
                target_module = item["module"]
                candidates = [(target_module, "module_import", None)]
            else:
                module_parts = [part for part in item["module"].split(".") if part]
                level = int(item.get("level", 0))
                if level:
                    keep = len(package_parts) - level + 1
                    if keep < 0:
                        ambiguous.add("." * level + item["module"])
                        continue
                    base_parts = package_parts[:keep]
                else:
                    base_parts = []
                target_module = ".".join((*base_parts, *module_parts))
                imported_name = item.get("name")
                candidates = []
                if target_module:
                    candidates.append(
                        (target_module, "candidate_from_import", imported_name)
                    )
                if imported_name and imported_name != "*":
                    candidates.append(
                        (
                            ".".join(
                                part for part in (target_module, imported_name) if part
                            ),
                            "candidate_from_import",
                            imported_name,
                        )
                    )

            matched = False
            for candidate, resolution, imported_name in candidates:
                target_paths = modules.get(candidate, [])
                if len(target_paths) == 1:
                    edges.append(
                        {
                            "source": relative_path,
                            "target": target_paths[0],
                            "module": candidate,
                            "name": imported_name,
                            "line": item["line"],
                            "resolution": resolution,
                        }
                    )
                    matched = True
                elif len(target_paths) > 1:
                    ambiguous.add(candidate)
            if not matched and target_module and target_module not in ambiguous:
                unresolved.add(target_module.split(".", 1)[0])

    unique_edges = {
        (
            edge["source"],
            edge["target"],
            edge["module"],
            edge["name"] or "",
            edge["line"],
            edge["resolution"],
        ): edge
        for edge in edges
    }
    return (
        [unique_edges[key] for key in sorted(unique_edges)],
        sorted(unresolved),
        sorted(ambiguous),
    )


def _context_evidence(root: Path) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    try:
        candidates = sorted(
            (
                path
                for path in root.iterdir()
                if path.name in _CONTEXT_NAMES | _DEPENDENCY_FILES
                and not path.is_symlink()
            ),
            key=lambda item: (item.name not in _CONTEXT_NAMES, item.name.lower()),
        )
    except OSError as exc:
        raise ProjectAnalysisError(
            f"Unable to inspect project context in {root}: {exc}"
        ) from exc

    for path in candidates[:MAX_CONTEXT_FILES]:
        try:
            with path.open("rb") as source_file:
                raw = source_file.read(MAX_CONTEXT_FILE_BYTES + 1)
        except OSError as exc:
            raise ProjectAnalysisError(
                f"Unable to read project context {path}: {exc}"
            ) from exc
        truncated = len(raw) > MAX_CONTEXT_FILE_BYTES
        content_bytes = raw[:MAX_CONTEXT_FILE_BYTES]
        try:
            content = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            continue
        evidence.append(
            {
                "kind": (
                    "documentation"
                    if path.name.lower().startswith("readme")
                    else "dependency_lock"
                    if path.name in _DEPENDENCY_FILES
                    else "project_metadata"
                ),
                "path": path.name,
                "sha256": hashlib.sha256(content_bytes).hexdigest(),
                "content": content,
                "truncated": truncated,
            }
        )
    return evidence


def _compact_file(path: str, analysis: dict[str, Any]) -> dict[str, Any]:
    symbols = [
        {
            "kind": symbol["kind"],
            "name": symbol["name"],
            "qualified_name": symbol["qualified_name"],
            "start_line": symbol["start_line"],
            "end_line": symbol["end_line"],
            "docstring": (symbol.get("docstring") or "")[:500] or None,
            "calls": symbol["calls"],
        }
        for symbol in analysis["symbols"]
    ]
    return {
        "path": path,
        "language": analysis["language"],
        "sha256": analysis["source"]["sha256"],
        "line_count": analysis["source"]["line_count"],
        "support_level": analysis["support_level"],
        "module_docstring": (analysis.get("module_docstring") or "")[:1000] or None,
        "imports": analysis["imports"],
        "module_calls": analysis["module_calls"],
        "observed_effects": analysis["observed_effects"],
        "symbols": symbols,
    }


def _analyze_project_file(
    source: str, relative_path: str, language: str
) -> dict[str, Any]:
    """Analyze one project file with the adapter matching its language."""

    adapter = adapter_for_language(language)
    if adapter is None:
        raise ProjectAnalysisError(
            f"No registered source adapter for language {language!r}"
        )
    try:
        return adapter.analyze_source(source, path=relative_path)
    except SourceAdapterError as exc:
        raise ProjectAnalysisError(f"Unable to analyze {relative_path}: {exc}") from exc


def analyze_python_project(path: str | Path) -> dict[str, Any]:
    """Build a bounded project map from analyzed files and root-level context evidence."""

    try:
        root = Path(path).resolve(strict=True)
    except OSError as exc:
        raise ProjectAnalysisError(f"Project root is unavailable: {exc}") from exc
    if not root.is_dir():
        raise ProjectAnalysisError(f"Project root is not a directory: {root}")

    python_files, other_files = _discover(root)
    analyses: dict[str, dict[str, Any]] = {}
    total_bytes = 0
    total_symbols = 0
    total_imports = 0
    total_calls = 0
    for source_path in python_files:
        relative_path = source_path.relative_to(root).as_posix()
        adapter = adapter_for_suffix(source_path.suffix.lower())
        if adapter is None:
            continue
        language = adapter.language
        per_file_limit = adapter.max_source_bytes
        try:
            with source_path.open("rb") as source_file:
                raw = source_file.read(per_file_limit + 1)
        except OSError as exc:
            raise ProjectAnalysisError(
                f"Unable to read source file {relative_path}: {exc}"
            ) from exc
        if len(raw) > per_file_limit:
            raise ProjectAnalysisError(
                f"Source file {relative_path!r} exceeds the {per_file_limit}-byte limit"
            )
        total_bytes += len(raw)
        if total_bytes > MAX_PROJECT_SOURCE_BYTES:
            raise ProjectAnalysisError(
                f"Project exceeds the {MAX_PROJECT_SOURCE_BYTES}-byte source limit"
            )
        try:
            source = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProjectAnalysisError(
                f"Source file {relative_path!r} is not UTF-8"
            ) from exc
        analysis = _analyze_project_file(source, relative_path, language)

        total_symbols += len(analysis["symbols"])
        total_imports += len(analysis["imports"])
        total_calls += sum(len(item["calls"]) for item in analysis["symbols"])
        total_calls += len(analysis["module_calls"])
        if total_symbols > MAX_PROJECT_SYMBOLS:
            raise ProjectAnalysisError(
                f"Project exceeds the {MAX_PROJECT_SYMBOLS}-symbol analysis limit"
            )
        if total_imports > MAX_PROJECT_IMPORTS:
            raise ProjectAnalysisError(
                f"Project exceeds the {MAX_PROJECT_IMPORTS}-import analysis limit"
            )
        if total_calls > MAX_PROJECT_CALLS:
            raise ProjectAnalysisError(
                f"Project exceeds the {MAX_PROJECT_CALLS}-call observation limit"
            )
        per_symbol_limit = adapter.max_symbols
        if len(analysis["symbols"]) > per_symbol_limit:
            raise ProjectAnalysisError(f"Too many symbols in {relative_path}")
        analyses[relative_path] = analysis

    python_analyses = {
        relative_path: analysis
        for relative_path, analysis in analyses.items()
        if analysis["language"] == "python"
    }
    import_edges, unresolved_imports, ambiguous_imports = _local_import_edges(
        python_analyses
    )
    tests = [
        relative_path
        for relative_path in analyses
        if Path(relative_path).name.startswith("test_")
        or "tests" in Path(relative_path).parts
    ]
    context_evidence = _context_evidence(root)
    languages = Counter(item["language"] for item in other_files)
    for analysis in analyses.values():
        languages[analysis["language"]] += 1
    report = {
        "schema": "kosika.project-analysis/v1",
        "analysis_mode": "bounded_project_structure",
        "project": {"name": root.name},
        "support_level": "polyglot_structure_and_python_import_edges",
        "limits": {
            "max_analyzed_files": MAX_PROJECT_FILES,
            "max_python_files": MAX_PROJECT_FILES,
            "max_entries": MAX_PROJECT_ENTRIES,
            "max_total_source_bytes": MAX_PROJECT_SOURCE_BYTES,
            "max_symbols": MAX_PROJECT_SYMBOLS,
            "max_imports": MAX_PROJECT_IMPORTS,
            "max_call_observations": MAX_PROJECT_CALLS,
        },
        "summary": {
            "file_count": len(analyses),
            "symbol_count": total_symbols,
            "import_count": total_imports,
            "call_observation_count": total_calls,
            "test_file_count": len(tests),
            "local_import_edge_count": len(import_edges),
            "language_file_counts": dict(sorted(languages.items())),
        },
        "files": [
            _compact_file(relative_path, analysis)
            for relative_path, analysis in sorted(analyses.items())
        ],
        "local_import_edges": import_edges,
        "unresolved_import_candidates": unresolved_imports,
        "ambiguous_local_imports": ambiguous_imports,
        "test_files": tests,
        "other_language_files": other_files[:100],
        "other_language_files_truncated": len(other_files) > 100,
        "context_evidence": context_evidence,
        "limitations": [
            "Python, JavaScript, TypeScript, and Rust files receive syntax-backed structural analysis; other listed languages are inventory only.",
            "Local import edges link Python files only; JavaScript, TypeScript, and Rust imports are reported per file without cross-file resolution.",
            "Imports link only when they resolve to one discovered file; unresolved candidates may be local, external, namespace-package, or dynamic imports.",
            "Calls are syntactic observations and are not resolved to definitions or runtime behavior.",
            "Dynamic imports, reflection, generated code, plugins, and monkey patching may hide relationships.",
            "README and project metadata are evidence, not verified statements of intent or runtime configuration.",
            "Tests are inventoried by path only; this adapter does not execute or infer their assertions.",
        ],
    }
    report_size = len(json.dumps(report, ensure_ascii=False).encode("utf-8"))
    if report_size > MAX_PROJECT_REPORT_BYTES:
        raise ProjectAnalysisError(
            f"Project report exceeds the {MAX_PROJECT_REPORT_BYTES}-byte context limit; analyze a smaller source root"
        )
    return report
