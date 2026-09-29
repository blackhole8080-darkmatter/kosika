"""Language adapter registry for bounded, read-only source analysis.

The registry owns suffix dispatch. Individual adapters own parsing and the
language-specific analysis contract; callers do not need language conditionals.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .javascript_adapter import MAX_SOURCE_BYTES as JAVASCRIPT_MAX_SOURCE_BYTES
from .javascript_adapter import MAX_SYMBOLS as JAVASCRIPT_MAX_SYMBOLS
from .javascript_adapter import scan_javascript_file, scan_javascript_source
from .python_adapter import MAX_SOURCE_BYTES as PYTHON_MAX_SOURCE_BYTES
from .python_adapter import MAX_SYMBOLS as PYTHON_MAX_SYMBOLS
from .python_adapter import analyze_python_file, analyze_python_source
from .rust_adapter import MAX_SOURCE_BYTES as RUST_MAX_SOURCE_BYTES
from .rust_adapter import MAX_SYMBOLS as RUST_MAX_SYMBOLS
from .rust_adapter import analyze_rust_file, analyze_rust_source

SourceAnalyzer = Callable[[str, str], dict[str, Any]]
FileAnalyzer = Callable[[str | Path], dict[str, Any]]


class LanguageAdapterError(ValueError):
    """Raised when a language adapter cannot be selected safely."""


@dataclass(frozen=True)
class LanguageAdapter:
    """One source-language adapter and the suffixes it owns."""

    language: str
    suffixes: tuple[str, ...]
    max_source_bytes: int
    max_symbols: int
    _source_analyzer: SourceAnalyzer
    _file_analyzer: FileAnalyzer

    def analyze_source(self, source: str, *, path: str) -> dict[str, Any]:
        """Analyze source text without importing or executing it."""

        return self._source_analyzer(source, path)

    def analyze_file(self, path: str | Path) -> dict[str, Any]:
        """Analyze one source file without importing or executing it."""

        return self._file_analyzer(path)


class LanguageAdapterRegistry:
    """Immutable dispatch table for the source-language adapters."""

    def __init__(self, adapters: Iterable[LanguageAdapter]) -> None:
        ordered = tuple(adapters)
        if not ordered:
            raise LanguageAdapterError("At least one language adapter is required")
        by_language: dict[str, LanguageAdapter] = {}
        by_suffix: dict[str, LanguageAdapter] = {}
        for adapter in ordered:
            language = adapter.language.lower()
            if not language or language != adapter.language:
                raise LanguageAdapterError(
                    "Adapter language names must be non-empty lowercase strings"
                )
            if adapter.max_symbols <= 0:
                raise LanguageAdapterError(
                    f"Adapter {language!r} must have a positive symbol limit"
                )
            if language in by_language:
                raise LanguageAdapterError(f"Duplicate language adapter {language!r}")
            if not adapter.suffixes:
                raise LanguageAdapterError(
                    f"Adapter {language!r} must declare at least one suffix"
                )
            by_language[language] = adapter
            for suffix in adapter.suffixes:
                normalized = suffix.lower()
                if not normalized.startswith(".") or normalized != suffix:
                    raise LanguageAdapterError(
                        f"Adapter {language!r} has invalid suffix {suffix!r}"
                    )
                if normalized in by_suffix:
                    owner = by_suffix[normalized].language
                    raise LanguageAdapterError(
                        f"Suffix {suffix!r} is claimed by {owner!r} and {language!r}"
                    )
                by_suffix[normalized] = adapter
        self._adapters = ordered
        self._by_language = by_language
        self._by_suffix = by_suffix

    @property
    def adapters(self) -> tuple[LanguageAdapter, ...]:
        """Return adapters in deterministic registration order."""

        return self._adapters

    @property
    def languages(self) -> tuple[str, ...]:
        """Return registered language names in deterministic order."""

        return tuple(adapter.language for adapter in self._adapters)

    def for_language(self, language: str) -> LanguageAdapter | None:
        """Return an adapter by language name, or ``None`` when unsupported."""

        return self._by_language.get(language.lower())

    def for_suffix(self, suffix: str) -> LanguageAdapter | None:
        """Return an adapter by file suffix, or ``None`` when unsupported."""

        return self._by_suffix.get(suffix.lower())

    def require_language(self, language: str) -> LanguageAdapter:
        """Return an adapter or raise a clear unsupported-language error."""

        adapter = self.for_language(language)
        if adapter is None:
            supported = ", ".join(self.languages)
            raise LanguageAdapterError(
                f"No structural adapter for language {language!r}; "
                f"supported languages: {supported}"
            )
        return adapter

    def require_suffix(self, suffix: str) -> LanguageAdapter:
        """Return an adapter or raise a clear unsupported-suffix error."""

        adapter = self.for_suffix(suffix)
        if adapter is None:
            supported = ", ".join(self.languages)
            raise LanguageAdapterError(
                f"No structural adapter for {suffix or 'this file type'}; "
                f"supported languages: {supported}"
            )
        return adapter


def _analyze_python_source(source: str, path: str) -> dict[str, Any]:
    return analyze_python_source(source, path=path)


def _analyze_javascript_source(source: str, path: str) -> dict[str, Any]:
    return scan_javascript_source(source, path=path, language="javascript")


def _analyze_typescript_source(source: str, path: str) -> dict[str, Any]:
    return scan_javascript_source(source, path=path, language="typescript")
def _analyze_rust_source(source: str, path: str) -> dict[str, Any]:
    return analyze_rust_source(source, path=path)



REGISTRY = LanguageAdapterRegistry(
    (
        LanguageAdapter(
            "python",
            (".py", ".pyi"),
            PYTHON_MAX_SOURCE_BYTES,
            PYTHON_MAX_SYMBOLS,
            _analyze_python_source,
            analyze_python_file,
        ),
        LanguageAdapter(
            "javascript",
            (".js", ".mjs", ".cjs", ".jsx"),
            JAVASCRIPT_MAX_SOURCE_BYTES,
            JAVASCRIPT_MAX_SYMBOLS,
            _analyze_javascript_source,
            scan_javascript_file,
        ),
        LanguageAdapter(
            "typescript",
            (".ts", ".mts", ".cts", ".tsx"),
            JAVASCRIPT_MAX_SOURCE_BYTES,
            JAVASCRIPT_MAX_SYMBOLS,
            _analyze_typescript_source,
            scan_javascript_file,
        ),
        LanguageAdapter(
            "rust",
            (".rs",),
            RUST_MAX_SOURCE_BYTES,
            RUST_MAX_SYMBOLS,
            _analyze_rust_source,
            analyze_rust_file,
        ),
    )
)

SUPPORTED_LANGUAGES = REGISTRY.languages


def adapter_for_language(language: str) -> LanguageAdapter | None:
    """Return a registered adapter by language name."""

    return REGISTRY.for_language(language)


def adapter_for_suffix(suffix: str) -> LanguageAdapter | None:
    """Return a registered adapter by file suffix."""

    return REGISTRY.for_suffix(suffix)


__all__ = [
    "FileAnalyzer",
    "LanguageAdapter",
    "LanguageAdapterError",
    "LanguageAdapterRegistry",
    "REGISTRY",
    "SUPPORTED_LANGUAGES",
    "SourceAnalyzer",
    "adapter_for_language",
    "adapter_for_suffix",
]
