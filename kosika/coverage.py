"""How much of the checker the conformance suite can actually refuse.

`kosika/checker.py` refuses a program from many places, and a refusal is only
a promise once a program in `conformance/refused/` proves it happens. Counting
that is the only way to say "the suite covers N of the checker's rules"
honestly, and the only way to see the number grow.

A rule is one `raise CheckError(...)` in the checker. A rule is covered when
some refused program ends at that line. The line is read from the exception's
traceback rather than matched against the message text, because most messages
carry the offending name and would otherwise need a matcher per rule.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from .checker import CheckError
from .modules import load_program

ROOT = Path(__file__).resolve().parents[1]
CHECKER = Path(__file__).resolve().parent / "checker.py"
REFUSED = ROOT / "conformance" / "refused"


@dataclass(frozen=True)
class Coverage:
    """What the refused programs reach, and what they leave untried."""

    #: Every rule: the line it is raised on, and the start of its message.
    rules: dict[int, str]
    #: The rules a refused program reaches, by line, named by that program.
    covered: dict[int, str]
    #: Programs refused by something other than the checker (a parse, say).
    refused_elsewhere: tuple[str, ...]
    #: Programs under `refused/` that the loader accepts: a case gone stale.
    accepted: tuple[str, ...]

    @property
    def ratio(self) -> str:
        return f"{len(self.covered)}/{len(self.rules)}"

    @property
    def uncovered(self) -> dict[int, str]:
        return {
            line: message
            for line, message in sorted(self.rules.items())
            if line not in self.covered
        }


def _message(call: ast.Call) -> str:
    """The fixed start of a refusal's message, for a report to name it by."""

    if not call.args:
        return ""
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    if isinstance(first, ast.JoinedStr):
        return "".join(
            part.value
            for part in first.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
    return ""


def rule_sites(path: Path = CHECKER) -> dict[int, str]:
    """Every `raise CheckError(...)` in the checker, by line."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    sites: dict[int, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
            continue
        raised = node.exc
        if isinstance(raised.func, ast.Name) and raised.func.id == "CheckError":
            sites[node.lineno] = _message(raised)
    return dict(sorted(sites.items()))


def _raised_on(exc: BaseException, path: Path = CHECKER) -> int | None:
    """The last line in the checker this refusal passed through."""

    wanted = path.resolve()
    line: int | None = None
    trace = exc.__traceback__
    while trace is not None:
        if Path(trace.tb_frame.f_code.co_filename).resolve() == wanted:
            line = trace.tb_lineno
        trace = trace.tb_next
    return line


def measure(folder: Path = REFUSED, path: Path = CHECKER) -> Coverage:
    """Run every refused program and record which rule refused it."""

    rules = rule_sites(path)
    covered: dict[int, str] = {}
    elsewhere: list[str] = []
    accepted: list[str] = []
    for case in sorted(folder.glob("*.kos")):
        try:
            load_program(case)
        except CheckError as exc:
            line = _raised_on(exc, path) if path == CHECKER else None
            if line is None or line not in rules:
                elsewhere.append(case.stem)
            else:
                covered.setdefault(line, case.stem)
        except ValueError:
            # A parse or a module error: refused, but not by a checker rule.
            elsewhere.append(case.stem)
        else:
            accepted.append(case.stem)
    return Coverage(rules, covered, tuple(elsewhere), tuple(accepted))


def to_text(report: Coverage) -> str:
    """The count, and what it would take to raise it."""

    lines = [
        f"Checker rules refused by conformance/refused: {report.ratio}",
        f"  reached elsewhere (parse or module): {len(report.refused_elsewhere)}",
        f"  accepted, so stale: {', '.join(report.accepted) or 'none'}",
    ]
    if report.uncovered:
        lines.append("  rules with no refused program:")
        for line, message in report.uncovered.items():
            lines.append(f"    checker.py:{line}  {message or '(no fixed text)'}")
    return "\n".join(lines)
