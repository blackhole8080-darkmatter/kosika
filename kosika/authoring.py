"""A bounded AI authoring loop over the authoritative Kosika toolchain.

The loop is `intent -> check -> propose -> repair -> run -> inspect`. Each step
delegates to an existing authority rather than reimplementing one: `check` and
`run` are `kosika.development/v1` operations, the diagnostic text is the K04
contract, and the patch format is the benchmark's search/replace seam.

Four decisions make this safe to point at a real repository.

**Model output is a proposal, never a mutation.** Every patch is applied to an
in-memory copy of one source file and returned under `proposals`. This module
never writes to the filesystem. Applying a proposal is a host decision, which
is why K11 (approval-controlled AI changes) can add review without reshaping
this loop.

**The host holds every capability.** The loop accepts a `ModelProvider` for
proposals and passes host-supplied model/tool bindings, execution limits, and
trace path through to the run step. It mints no authority, declares no
capability, and cannot widen one.

**The failing file is the only patchable file.** A diagnostic names its
originating path, so a proposal is applied to that file and nowhere else. A
model cannot turn a bad guess in `helpers.kos` into an edit in `main.kos`, and
a search text that matches zero or several places is rejected rather than
applied somewhere arbitrary.

**Attempts are bounded and every one is recorded.** The loop makes at most
`max_attempts` provider calls and stops on a provider or configuration failure
instead of retrying an infrastructure problem. A result that scores nothing is
attributable: an attempt record distinguishes "never proposed a patch" from
"proposed one that did not converge", and `usage.model_calls` counts only
requests actually made.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .agent import ToolExecutor
from .development import check_project, inspect_trace, run_project
from .durable import ExecutionSession
from .execution import ExecutionLimits
from .manifest import MCPManifest
from .model import ChatMessage, ModelProvider, ModelRequest
from .repair import (
    PatchError,
    RepairStrategy,
    apply_replacement,
    build_proposal_messages,
    excerpt,
    parse_replacements,
)

AUTHORING_SCHEMA = "kosika.authoring/v1"

#: How much of each model answer is kept in an attempt record. Enough to see a
#: formatting mistake, small enough that a report stays readable.
RESPONSE_EXCERPT = 600


class AuthoringError(ValueError):
    """Raised when a loop cannot be started with the authority it was given."""


@dataclass(frozen=True)
class AuthoringLimits:
    """Bounds the host puts on one authoring loop.

    ``max_attempts`` is the whole budget: each attempt is at most one provider
    call, so this bounds both model spend and how much a loop can churn through
    a program.
    """

    max_attempts: int = 2
    max_output_tokens: int = 2048
    max_context_lines: int = 12
    strategy: RepairStrategy = RepairStrategy.CONTEXTUAL

    def validated(self) -> AuthoringLimits:
        if self.max_attempts < 1:
            raise AuthoringError("max_attempts must be at least 1")
        if self.max_output_tokens < 1:
            raise AuthoringError("max_output_tokens must be at least 1")
        if self.max_context_lines < 1:
            raise AuthoringError("max_context_lines must be at least 1")
        return self


@dataclass
class Attempt:
    """One check-then-propose step, including its observed model cost.

    ``seconds`` measures only the provider call. Checks, patch application, and
    execution remain part of the enclosing loop's wall time.
    """

    index: int
    path: str | None
    code: str | None
    applied: bool
    error: str | None = None
    remaining: tuple[str, ...] = ()
    response: str = ""
    model_called: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "path": self.path,
            "code": self.code,
            "applied": self.applied,
            "error": self.error,
            "remaining": list(self.remaining),
            "response": self.response,
            "model_called": self.model_called,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "seconds": round(self.seconds, 6),
        }


@dataclass
class AuthoringOutcome:
    """The full result of one loop."""

    intent: str
    status: str
    attempts: list[Attempt] = field(default_factory=list)
    proposals: dict[str, str] = field(default_factory=dict)
    diagnostics: dict[str, Any] | None = None
    execution: dict[str, Any] | None = None
    trace: dict[str, Any] | None = None
    error: dict[str, str] | None = None
    provider_calls: int = 0
    usage_prompt_tokens: int = 0
    usage_completion_tokens: int = 0

    @property
    def model_calls(self) -> int:
        """Requests actually made to the proposing provider.

        Attempts that never reached the provider -- an unmapped diagnostic, no
        granted authority, an unreadable file -- are not model calls, so they
        are not counted as spend.
        """

        return self.provider_calls


def _project_reference(source: str | Path, root: str | Path | None) -> dict[str, str]:
    entry = Path(source).resolve()
    boundary = Path(root).resolve() if root is not None else entry.parent
    try:
        relative_entry = entry.relative_to(boundary).as_posix()
    except ValueError:
        relative_entry = entry.name
    return {"root": str(boundary), "entry": relative_entry}


def _read_source(path: Path, overrides: Mapping[Path, str]) -> str:
    """Return the current in-memory source for one project file."""

    resolved = path.resolve()
    for candidate, source in overrides.items():
        if candidate.resolve() == resolved:
            return source
    return resolved.read_text(encoding="utf-8")


def _resolve_diagnostic_path(
    entry: Path, diagnostic: Mapping[str, Any], boundary: Path
) -> Path | None:
    """Resolve the file a diagnostic came from inside the project boundary.

    A diagnostic with no path belongs to the entry document. A diagnostic whose
    path resolves outside the project returns ``None`` instead of a fallback
    file: a proposal has to be applied to a file the loop can read, show, and
    hand back to the host, so an out-of-project diagnostic is reported as
    unmapped rather than repaired against the wrong source.
    """

    raw = diagnostic.get("path")
    if not isinstance(raw, str) or not raw:
        return entry
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = boundary / candidate
    resolved = candidate.resolve()
    if resolved != boundary and boundary not in resolved.parents:
        return None
    return resolved


def _select_diagnostic(
    diagnostics: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """Choose the diagnostic to act on, deterministically.

    The first reported diagnostic is used so the loop does not re-order the
    project's own report: an authoring run should repair what the checker
    reports first, not what a sort happens to prefer.
    """

    return diagnostics[0] if diagnostics else None


def _attempt_messages(
    source: str,
    path: str,
    diagnostic: Mapping[str, Any],
    *,
    limits: AuthoringLimits,
    feedback: Sequence[str],
) -> tuple[ChatMessage, ...]:
    return build_proposal_messages(
        source,
        diagnostic,
        path=path,
        strategy=limits.strategy,
        padding=limits.max_context_lines,
        feedback=tuple(feedback),
    )


def author(
    source: str | Path,
    *,
    intent: str,
    provider: ModelProvider | None = None,
    root: str | Path | None = None,
    limits: AuthoringLimits | None = None,
    model: str | None = None,
    temperature: float = 0.0,
    workflow: str = "main",
    inputs: Mapping[str, Any] | None = None,
    trace_path: str | Path | None = None,
    execute: bool = True,
    model_bindings: Mapping[str, ModelProvider] | None = None,
    tool_bindings: Mapping[str, ToolExecutor] | None = None,
    mcp_commands: dict[str, Sequence[str]] | None = None,
    manifest: MCPManifest | None = None,
    session: ExecutionSession | None = None,
    execution_limits: ExecutionLimits | None = None,
    cancel: threading.Event | None = None,
    source_overrides: Mapping[Path, str] | None = None,
) -> dict[str, Any]:
    """Run one bounded authoring loop and return a JSON-compatible envelope.

    ``intent`` is recorded for the host; this loop repairs what the checker
    reports rather than asking a model to invent new source. Authoring new
    declarations is a different operation with a different authority question.

    ``provider`` is optional because a project that already checks needs no
    model at all. When a diagnostic does need a proposal and no provider was
    supplied, the loop stops with ``authoring/no-provider`` rather than
    reporting success it did not achieve.
    """

    if not isinstance(intent, str) or not intent.strip():
        raise AuthoringError("intent must be non-empty text")
    entry = Path(source).resolve()
    project = _project_reference(entry, root)
    boundary = Path(project["root"])
    effective = (limits or AuthoringLimits()).validated()

    overrides: dict[Path, str] = {
        Path(path).resolve(): text for path, text in (source_overrides or {}).items()
    }
    outcome = AuthoringOutcome(intent=intent, status="exhausted")
    feedback: list[str] = []

    for index in range(effective.max_attempts):
        report = check_project(entry, root=boundary, source_overrides=overrides)
        diagnostics = report.get("diagnostics") or {}
        listed = list(diagnostics.get("diagnostics") or ())
        outcome.diagnostics = report.get("diagnostics")
        if report["ok"]:
            outcome.status = "checked"
            break

        diagnostic = _select_diagnostic(listed)
        if diagnostic is None:
            outcome.status = "checked"
            break
        target = _resolve_diagnostic_path(entry, diagnostic, boundary)
        code = str(diagnostic.get("code") or "")
        if target is None:
            # Repairing it would mean patching a file the loop cannot show the
            # model and could not hand back, so the loop stops and says why.
            message = (
                f"diagnostic {code} names {diagnostic.get('path')!r}, which is "
                "outside the project root"
            )
            outcome.attempts.append(Attempt(index, None, code, False, message))
            outcome.error = {"code": "authoring/unmapped", "message": message}
            break
        try:
            current = _read_source(target, overrides)
        except (OSError, UnicodeDecodeError) as exc:
            # An unreadable file is not a repairable diagnostic; retrying would
            # only ask the model to patch text the loop cannot read back.
            outcome.attempts.append(Attempt(index, None, code, False, str(exc)))
            outcome.error = {"code": "authoring/unreadable", "message": str(exc)}
            break

        if provider is None:
            # A project that already checks needs no model; one that does not
            # check cannot be repaired without one, and pretending otherwise
            # would report a repair that never happened.
            message = (
                f"{code} needs a proposal and no proposing model was granted by "
                "the host"
            )
            outcome.attempts.append(Attempt(index, target.name, code, False, message))
            outcome.error = {"code": "authoring/no-provider", "message": message}
            break

        messages = _attempt_messages(
            current,
            target.name,
            diagnostic,
            limits=effective,
            feedback=feedback,
        )
        provider_started = time.perf_counter()
        try:
            # Counted before the call, because a call that fails still cost the
            # host a request. Reporting spend is not something to under-count.
            outcome.provider_calls += 1
            response = provider.complete(
                ModelRequest(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_output_tokens=effective.max_output_tokens,
                )
            )
        except Exception as exc:
            outcome.attempts.append(
                Attempt(
                    index,
                    target.name,
                    code,
                    False,
                    f"provider error: {exc}",
                    model_called=True,
                    seconds=time.perf_counter() - provider_started,
                )
            )
            outcome.error = {"code": "authoring/provider", "message": str(exc)}
            break
        provider_seconds = time.perf_counter() - provider_started
        prompt_tokens = 0
        completion_tokens = 0
        if response.usage is not None:
            prompt_tokens = response.usage.prompt_tokens or 0
            completion_tokens = response.usage.completion_tokens or 0
            outcome.usage_prompt_tokens += prompt_tokens
            outcome.usage_completion_tokens += completion_tokens
        content = response.content or ""
        if not content.strip() and response.reasoning:
            # A reasoning-only answer is a configuration problem, not a bad
            # answer, so it is named as one instead of being retried.
            message = (
                "model produced only a reasoning trace and no answer; disable "
                "thinking for structured output"
            )
            outcome.attempts.append(
                Attempt(
                    index,
                    target.name,
                    code,
                    False,
                    message,
                    model_called=True,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    seconds=provider_seconds,
                )
            )
            outcome.error = {"code": "authoring/no-answer", "message": message}
            break
        try:
            replacements = parse_replacements(content)
        except PatchError as exc:
            outcome.attempts.append(
                Attempt(
                    index,
                    target.name,
                    code,
                    False,
                    str(exc),
                    response=excerpt(content, RESPONSE_EXCERPT),
                    model_called=True,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    seconds=provider_seconds,
                )
            )
            feedback.append(f"attempt {index + 1}: {exc}")
            continue
        try:
            patched = current
            for replacement in replacements:
                patched, _edit = apply_replacement(patched, replacement)
        except PatchError as exc:
            outcome.attempts.append(
                Attempt(
                    index,
                    target.name,
                    code,
                    False,
                    str(exc),
                    response=excerpt(content, RESPONSE_EXCERPT),
                    model_called=True,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    seconds=provider_seconds,
                )
            )
            feedback.append(f"attempt {index + 1}: {exc}")
            continue

        overrides[target] = patched
        verify = check_project(entry, root=boundary, source_overrides=overrides)
        outcome.diagnostics = verify.get("diagnostics")
        if verify["ok"]:
            outcome.attempts.append(
                Attempt(
                    index,
                    target.name,
                    code,
                    True,
                    model_called=True,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    seconds=provider_seconds,
                )
            )
            outcome.status = "repaired"
            break
        remaining = tuple(
            str(item.get("code"))
            for item in (verify.get("diagnostics") or {}).get("diagnostics", [])
        )
        outcome.attempts.append(
            Attempt(
                index,
                target.name,
                code,
                True,
                remaining=remaining,
                response=excerpt(content, RESPONSE_EXCERPT),
                model_called=True,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                seconds=provider_seconds,
            )
        )
        feedback.append(
            f"attempt {index + 1} applied but the project still failed with "
            + ", ".join(remaining)
        )
    else:
        outcome.status = "exhausted"

    # A loop that never reached a clean check has no runnable program, so it
    # reports the proposal it reached and stops rather than executing the last
    # known-broken state. `proposals` is the whole in-memory state, which
    # includes any `source_overrides` the host supplied, so the host can review
    # exactly what was executed.
    if outcome.status in {"repaired", "checked"}:
        outcome.proposals = {
            str(path.relative_to(boundary).as_posix()): text
            for path, text in sorted(overrides.items())
            if boundary in path.parents or path == boundary
        }
        if execute:
            execution = run_project(
                entry,
                root=boundary,
                workflow=workflow,
                inputs=dict(inputs or {}),
                trace_path=trace_path,
                source_overrides=overrides,
                model_bindings=model_bindings,
                tool_bindings=tool_bindings,
                mcp_commands=mcp_commands,
                manifest=manifest,
                session=session,
                limits=execution_limits,
                cancel=cancel,
            )
            outcome.execution = execution
            if trace_path is not None and execution["ok"]:
                outcome.trace = inspect_trace(trace_path)

    ok = outcome.status in {"repaired", "checked"} and (
        outcome.execution is None or outcome.execution["ok"]
    )
    if not ok and outcome.error is None:
        if outcome.status == "exhausted":
            outcome.error = {
                "code": "authoring/uncheckable",
                "message": (
                    "the project still reports diagnostics after the attempt budget"
                ),
            }
        else:
            # The program checks; the run is what failed, so the host is told
            # the run's own reason rather than a checking reason.
            failure = (outcome.execution or {}).get("error") or {}
            outcome.error = {
                "code": str(failure.get("code") or "authoring/execution"),
                "message": str(
                    failure.get("message")
                    or "the repaired program did not complete its run"
                ),
            }
    outcome.status = "ok" if ok else outcome.status
    return {
        "schema": AUTHORING_SCHEMA,
        "ok": ok,
        "intent": outcome.intent,
        "status": outcome.status,
        "project": project,
        "limits": asdict(effective) | {"strategy": effective.strategy.value},
        "attempts": [attempt.to_dict() for attempt in outcome.attempts],
        "proposals": outcome.proposals,
        "diagnostics": outcome.diagnostics,
        "execution": outcome.execution,
        "trace": outcome.trace,
        "error": outcome.error,
        "usage": {
            "model_calls": outcome.model_calls,
            "prompt_tokens": outcome.usage_prompt_tokens,
            "completion_tokens": outcome.usage_completion_tokens,
        },
    }


__all__ = [
    "AUTHORING_SCHEMA",
    "AuthoringError",
    "AuthoringLimits",
    "AuthoringOutcome",
    "Attempt",
    "author",
]
