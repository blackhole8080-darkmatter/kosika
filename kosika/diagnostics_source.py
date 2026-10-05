"""Verify that the diagnostic code registry covers every raise site.

:class:`kosika.diagnostics` classifies failures by matching an existing error
message against a registry of templates. That works, but nothing stops a
message from drifting away from its template: if someone rewords an error
string, the rule stops matching and the failure silently degrades to
``kosika/internal-error``. The existing tests would not catch it, because they
assert on the same prose that just changed.

This module closes that gap. It reads the *source* of the modules that raise
errors, recovers the message template of every raise site, renders each one
into a concrete sample, and checks two invariants:

**Coverage** -- every raise site's message is claimed by at least one
registered template.

**Reachability** -- every registered template is claimed by at least one raise
site, so a stale rule cannot linger and quietly mislabel a future message.

Together these make the message strings an enforced contract: changing one
fails the suite instead of silently degrading behaviour at runtime.

This module is verification tooling. It is not on any runtime path, and the
lexer, parser, and checker are left untouched.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping, Sequence, TypedDict


class CoverageItem(TypedDict):
    label: str
    sample: str


class CoverageReport(TypedDict):
    site_count: int
    classified: int
    unclassified: list[CoverageItem]
    unreachable_templates: list[str]


#: Modules that raise user-facing diagnostics, and the exception each raises.
RAISE_SOURCES: Mapping[str, str] = {
    "kosika/checker.py": "CheckError",
    "kosika/modules.py": "CheckError",
    "kosika/parser.py": "ParseError",
    "kosika/lexer.py": "LexError",
}

#: Raise sites that deliberately forward an existing message rather than
#: declaring a new one. Each must justify itself, so a site cannot be hidden
#: by wrapping it.
PASS_THROUGH: Mapping[tuple[str, int], str] = {
    ("kosika/parser.py", 963): "re-raises a lexer/parser ValueError unchanged",
}

#: Placeholder substituted for an interpolated value when rendering a
#: template into a sample. ``!r`` interpolation is rendered quoted because the
#: registry's patterns expect repr-style quoting; plain interpolation is
#: rendered as a bare digit so that numeric patterns such as ``\d+`` still
#: match, which is what ``{token.line}`` style interpolations require.
QUOTED_SENTINEL = "'{}'"
PLAIN_SENTINEL = "{}"
SENTINEL_SEED = 7


@dataclass(frozen=True)
class RaiseSite:
    """One ``raise`` of a diagnostic-carrying exception."""

    path: str
    line: int
    kind: str
    text: str
    holes: tuple[bool, ...]
    reason: str | None = None

    @property
    def is_pass_through(self) -> bool:
        return self.reason is not None

    @property
    def label(self) -> str:
        return f"{self.path}:{self.line} ({self.kind})"

    def sample(self) -> str:
        """Render the template into a concrete string for pattern matching.

        Each hole gets a distinct value so that a template which reorders its
        interpolations does not still match.
        """

        parts = self.text.split(HOLE)
        if len(parts) - 1 != len(self.holes):
            raise ValueError(
                f"{self.label}: {len(parts) - 1} holes but {len(self.holes)} markers"
            )
        rendered = parts[0]
        for index, quoted in enumerate(self.holes):
            template = QUOTED_SENTINEL if quoted else PLAIN_SENTINEL
            rendered += template.format(SENTINEL_SEED + index) + parts[index + 1]
        return rendered

    def unhandled_reason(self) -> str | None:
        if self.reason is not None:
            return None
        if HOLE not in self.text and "\x00" in self.text:
            return "template contains a raw null placeholder"
        return None


#: Sentinel used inside extracted templates to mark an interpolation.
HOLE = "\x00"

#: Exception arguments that are not literal messages and therefore cannot be
#: extracted. Allowed only when declared in :data:`PASS_THROUGH`.
_NON_LITERAL = "non-literal message argument"


def _flatten(node: ast.AST) -> tuple[str, list[bool]] | None:
    """Flatten a string or implicitly-concatenated f-string into a template.

    Returns the literal text with :data:`HOLE` at each interpolation, plus a
    flag per hole recording whether it used ``!r``.
    """

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, []
    if isinstance(node, ast.JoinedStr):
        text = ""
        holes: list[bool] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                text += value.value
            elif isinstance(value, ast.FormattedValue):
                # 114 is ord("r"), the repr conversion flag.
                holes.append(value.conversion == 114)
                text += HOLE
            else:
                return None
        return text, holes
    return None


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    return None


def _extract(path: Path, relative: str, kind: str) -> list[RaiseSite]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    sites: list[RaiseSite] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        call = node.exc
        if isinstance(call, ast.Call):
            if _call_name(call.func) != kind or not call.args:
                continue
            argument = call.args[0]
        else:
            # A bare `raise ParseError(...)` without a call, or a re-raise of
            # some other exception: not a message declaration.
            continue
        flattened = _flatten(argument)
        if flattened is None:
            reason = PASS_THROUGH.get((relative, node.lineno))
            sites.append(
                RaiseSite(
                    path=relative,
                    line=node.lineno,
                    kind=kind,
                    text=_NON_LITERAL,
                    holes=(),
                    reason=reason or f"undeclared {_NON_LITERAL}",
                )
            )
            continue
        text, holes = flattened
        declared = PASS_THROUGH.get((relative, node.lineno))
        sites.append(
            RaiseSite(
                path=relative,
                line=node.lineno,
                kind=kind,
                text=text,
                holes=tuple(holes),
                reason=declared,
            )
        )
    return sorted(sites, key=lambda site: site.line)


def raise_sites(root: Path | None = None) -> list[RaiseSite]:
    """Extract every diagnostic raise site from the source tree.

    ``root`` defaults to the repository root inferred from this module's
    location, so tests need not know where the package is installed.
    """

    base = root or Path(__file__).resolve().parent.parent
    sites: list[RaiseSite] = []
    for relative, kind in RAISE_SOURCES.items():
        path = base / relative
        if not path.is_file():
            raise FileNotFoundError(f"expected diagnostic source at {path}")
        sites.extend(_extract(path, relative, kind))
    return sorted(sites, key=lambda site: (site.path, site.line))


def unclassified(
    sites: Sequence[RaiseSite], templates: Sequence[tuple[str, re.Pattern[str]]]
) -> list[RaiseSite]:
    """Raise sites whose message no registered template claims."""

    missing: list[RaiseSite] = []
    for site in sites:
        if site.is_pass_through:
            continue
        reason = site.unhandled_reason()
        if reason is not None:
            missing.append(site)
            continue
        sample = site.sample()
        if not any(pattern.search(sample) for _, pattern in templates):
            missing.append(site)
    return missing


def unreachable_templates(
    sites: Sequence[RaiseSite], templates: Sequence[tuple[str, re.Pattern[str]]]
) -> list[str]:
    """Registered templates that no raise site claims.

    A template nothing can reach is dead weight: it cannot fail loudly, and it
    invites the false belief that a category is still detected.
    """

    samples = [site.sample() for site in sites if not site.is_pass_through]
    dead: list[str] = []
    for code, pattern in templates:
        if not any(pattern.search(sample) for sample in samples):
            dead.append(code)
    return sorted(set(dead))


def coverage_report(
    sites: Sequence[RaiseSite], templates: Sequence[tuple[str, re.Pattern[str]]]
) -> CoverageReport:
    """Summarise registry coverage for reporting."""

    missing = unclassified(sites, templates)
    dead = unreachable_templates(sites, templates)
    return {
        "site_count": len(sites),
        "classified": len(sites) - len(missing),
        "unclassified": [
            {
                "label": site.label,
                "sample": (
                    site.sample() if not site.is_pass_through else (site.reason or "")
                ),
            }
            for site in missing
        ],
        "unreachable_templates": dead,
    }


def iter_templates() -> Iterator[tuple[str, re.Pattern[str]]]:
    """Iterate the registered classification templates in the runtime order."""

    from .diagnostics import classification_rules

    yield from classification_rules()


__all__ = [
    "HOLE",
    "PASS_THROUGH",
    "RAISE_SOURCES",
    "RaiseSite",
    "coverage_report",
    "iter_templates",
    "raise_sites",
    "unclassified",
    "unreachable_templates",
]
