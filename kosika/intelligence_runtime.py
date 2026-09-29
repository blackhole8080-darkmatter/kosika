"""
Connects the intelligence system to real runtime execution.

Converts runtime trace events into intelligence records so the platform
learns from actual model calls, tool calls, function calls, and workflow
steps — not synthetic profiles. Also provides an optional execution cache
for pure function results keyed by canonical arguments.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Mapping, Optional

from .intelligence import (
    ExecutionProfile,
    ExecutionStrategy,
    IntelligenceSystem,
    PatternExtractor,
    ResourcePredictor,
    create_intelligence_system,
)

# ---------------------------------------------------------------------------
# Operation naming
# ---------------------------------------------------------------------------

def _operation_key(event: Dict[str, Any]) -> str:
    """Return a stable operation identifier for an intelligence event."""
    kind = event.get("event")
    if kind is None:
        return "other/unknown"
    # Map return events back to their call counterpart so start/end pairs match.
    if kind.endswith("_return"):
        base = kind[:-7]  # strip "_return"
        arg = event.get(base[:-1] if base != "step" else "step")
        if arg is None:
            arg = event.get("workflow") or event.get("model") or event.get("agent") or event.get("function") or event.get("tool") or "?"
        return f"{base}/{arg}"
    if kind == "model_call":
        model = event.get("model", "?")
        return f"model/{model}"
    if kind == "tool_call":
        tool = event.get("tool", "?")
        return f"tool/{tool}"
    if kind == "function_call":
        fn = event.get("function", "?")
        return f"function/{fn}"
    if kind == "agent_call":
        agent = event.get("agent", "?")
        return f"agent/{agent}"
    if kind == "workflow_call":
        wf = event.get("workflow", "?")
        return f"workflow/{wf}"
    if kind == "step_start":
        return f"step/{event.get('step', '?')}"
    return f"other/{kind}"


def _outcome_event(event: Dict[str, Any]) -> str:
    """Return the outcome category for an event."""
    kind = event.get("event")
    if kind in ("model_return", "tool_return", "function_return",
                "agent_return", "workflow_return", "step_complete",
                "run_complete", "workflow_return"):
        return "success"
    if kind in ("run_failed", "run_cancelled", "run_paused",
                "workflow_failed", "function_call"):
        # function_call events are paired with function_return; treat the
        # pair, not the call alone.
        return "pending"
    return "unknown"


# ---------------------------------------------------------------------------
# Event-driven recorder
# ---------------------------------------------------------------------------

class IntelligenceRecorder:
    """
    Observes runtime trace events and feeds them into an
    ``IntelligenceSystem`` so later ``analyze_and_optimize`` calls reflect
    real behavior.

    Usage with the runtime::

        from kosika.intelligence_runtime import IntelligenceRecorder
        from kosika.runtime import run

        recorder = IntelligenceRecorder()
        result = run(program, "main", "trace.jsonl",
                     intelligence_recorder=recorder)
        print(recorder.analyze("model/reader"))
        print(recorder.insights)
    """

    def __init__(self, system: IntelligenceSystem | None = None) -> None:
        self.system = system or create_intelligence_system()
        self._active: Dict[str, float] = {}          # operation -> start_monotonic
        self._function_return_pending: Dict[str, Dict[str, Any]] = {}

    # -- public API ---------------------------------------------------------

    def record_event(self, event: Dict[str, Any]) -> None:
        """Ingest one runtime trace event."""
        kind = event.get("event")

        if kind == "run_start":
            self._active.clear()
            self._function_return_pending.clear()
            return

        if kind in ("model_call", "tool_call", "agent_call", "workflow_call"):
            key = _operation_key(event)
            self._active[key] = time.monotonic()
            return

        if kind == "function_call":
            # Paired with function_return; stash the call so we can compute
            # the combined duration when the return arrives.
            fn = event.get("function", "?")
            self._function_return_pending[fn] = event
            key = _operation_key(event)
            self._active[key] = time.monotonic()
            return

        if kind == "function_return":
            call = self._function_return_pending.pop(event.get("function", "?"), None)
            if call is not None:
                start = self._active.pop(_operation_key(call), None)
                if start is not None:
                    self._record_completed(
                        _operation_key(call),
                        time.monotonic() - start,
                        "success",
                        call,
                        event,
                    )
            return

        if kind in ("model_return", "tool_return", "agent_return",
                    "workflow_return", "step_complete", "run_complete"):
            key = _operation_key(event)
            start = self._active.pop(key, None)
            if start is not None:
                self._record_completed(key, time.monotonic() - start, "success", event, event)
            return

        if kind in ("run_failed", "run_cancelled", "run_paused"):
            # Flush any still-open operations as failures so the intelligence
            # layer sees the full picture of a failed run.
            for key, start in list(self._active.items()):
                self._record_completed(key, time.monotonic() - start, "failure", {}, event)
            self._active.clear()
            return

    def record_events(self, events: List[Dict[str, Any]]) -> None:
        """Ingest a complete event list (e.g. from a finished trace)."""
        for ev in events:
            self.record_event(ev)

    def analyze(self, operation: str,
                context: Dict[str, Any] | None = None) -> Dict[str, Any]:
        """Analysis + recommendation for one operation."""
        return self.system.analyze_and_optimize(
            operation, context or {}
        )

    @property
    def patterns(self) -> Dict[str, Any]:
        """Serializable snapshot of extracted execution patterns."""
        out: Dict[str, Any] = {}
        for pid, pat in self.system.adaptive_executor.pattern_extractor.patterns.items():
            out[pid] = {
                "operation_type": pat.operation_type,
                "avg_duration": pat.avg_duration,
                "success_rate": pat.success_rate,
                "frequency": pat.frequency,
                "characteristics": pat.characteristics,
            }
        return out

    @property
    def insights(self) -> Dict[str, Any]:
        """Serializable snapshot of system-wide intelligence insights."""
        raw = self.system.get_system_insights()
        raw["patterns"] = self.patterns
        return raw

    # -- internals ----------------------------------------------------------

    def _record_completed(self, key: str, duration: float,
                          outcome: str,
                          call_event: Dict[str, Any],
                          return_event: Dict[str, Any]) -> None:
        resource_usage = _resource_usage(call_event, return_event)
        success = outcome == "success"
        profile = ExecutionProfile(
            execution_id="",
            operation=key,
            duration=duration,
            resource_usage=resource_usage,
            success=success,
        )
        self.system.adaptive_executor.record_execution(profile)


def _resource_usage(call: Dict[str, Any], ret: Dict[str, Any]) -> Dict[str, float]:
    usage: Dict[str, float] = {}
    # Token usage attached to model_return / model_call events.
    for payload in (ret, call):
        if "usage" in payload and isinstance(payload["usage"], dict):
            for k, v in payload["usage"].items():
                if isinstance(v, (int, float)):
                    usage[k] = float(v)
    return usage


# ---------------------------------------------------------------------------
# Pure-function result cache
# ---------------------------------------------------------------------------

def _canonical_arguments(arguments: Mapping[str, Any]) -> bytes:
    return json.dumps(arguments, sort_keys=True, separators=(",", ":")).encode("utf-8")


class FunctionResultCache:
    """
    Bounded cache for pure function results.

    Pure functions are deterministic given their arguments, so caching
    ``function_name(canonical_arguments) -> result`` is safe and removes
    redundant computation. The cache is intentionally small and bounded so
    it never becomes a memory leak; it is most useful for hot pure helpers
    evaluated many times across steps or workflow calls.
    """

    def __init__(self, max_entries: int = 256) -> None:
        self._max = max(1, max_entries)
        self._store: Dict[str, Any] = {}
        self._order: List[str] = []
        self.hits = 0
        self.misses = 0

    def get(self, function_name: str,
            arguments: Mapping[str, Any]) -> Any | None:
        key = f"{function_name}::{_canonical_arguments(arguments)}"
        if key in self._store:
            self.hits += 1
            return self._store[key]
        self.misses += 1
        return None

    def put(self, function_name: str,
            arguments: Mapping[str, Any],
            result: Any) -> None:
        key = f"{function_name}::{_canonical_arguments(arguments)}"
        if key in self._store:
            return
        if len(self._store) >= self._max:
            oldest = self._order.pop(0)
            self._store.pop(oldest, None)
        self._store[key] = result
        self._order.append(key)

    def stats(self) -> Dict[str, Any]:
        total = self.hits + self.misses
        return {
            "entries": len(self._store),
            "max_entries": self._max,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": (self.hits / total) if total else 0.0,
        }

    def clear(self) -> None:
        self._store.clear()
        self._order.clear()
        self.hits = 0
        self.misses = 0
