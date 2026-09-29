"""Whole-run cooperative controls; never interrupt or retry in-flight effects."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

from .controls import ExecutionError
from .model import ModelProvider, ModelRequest, ModelResponse


@dataclass(frozen=True)
class ExecutionLimits:
    max_function_calls: int = 10_000
    max_steps: int = 10_000
    max_model_calls: int = 64
    max_tool_calls: int = 256
    deadline_seconds: float | None = None

    def __post_init__(self) -> None:
        for name in (
            "max_function_calls",
            "max_steps",
            "max_model_calls",
            "max_tool_calls",
        ):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= 1_000_000:
                raise ValueError(f"{name} must be an integer from 1 to 1000000")
        if self.deadline_seconds is not None and (
            type(self.deadline_seconds) not in (int, float)
            or not math.isfinite(self.deadline_seconds)
            or self.deadline_seconds <= 0
        ):
            raise ValueError("deadline_seconds must be finite and positive")


class ExecutionControl:
    def __init__(self, limits: ExecutionLimits, cancel: threading.Event | None = None):
        self.limits = limits
        self.cancel = cancel if cancel is not None else threading.Event()
        self.started = time.monotonic()
        self.counts = dict.fromkeys(
            ("function_calls", "steps", "model_calls", "tool_calls"), 0
        )
        self.scope: tuple[str, ...] = ()
        self.failure_stack: list[str] = []

    def checkpoint(self) -> None:
        if self.cancel.is_set():
            raise ExecutionError("cancelled", "Execution was cancelled")
        if (
            self.limits.deadline_seconds is not None
            and time.monotonic() - self.started >= self.limits.deadline_seconds
        ):
            raise ExecutionError("deadline", "Execution deadline exceeded")

    def consume(self, kind: str) -> None:
        self.checkpoint()
        maximum = getattr(self.limits, f"max_{kind}")
        if self.counts[kind] >= maximum:
            label = (
                "Pure function call"
                if kind == "function_calls"
                else kind.replace("_", " ")
            )
            raise ExecutionError("budget", f"{label} budget exceeds {maximum}")
        self.counts[kind] += 1

    def restore(self, used: dict[str, Any]) -> None:
        """Restore counters at a durable checkpoint."""
        for kind in self.counts:
            value = used.get(kind, 0)
            maximum = getattr(self.limits, f"max_{kind}")
            if type(value) is not int or not 0 <= value <= maximum:
                raise ExecutionError("journal_invalid", f"Invalid {kind} checkpoint")
            self.counts[kind] = value

    def snapshot(self) -> dict[str, Any]:
        return {
            "limits": asdict(self.limits),
            "used": dict(self.counts),
            "function_stack": list(self.failure_stack),
            "elapsed_seconds": max(0.0, time.monotonic() - self.started),
        }

    def provider(self, provider: ModelProvider) -> ModelProvider:
        if isinstance(provider, _ExecutionProvider) and provider.control is self:
            return provider
        return _ExecutionProvider(provider, self)


class _ExecutionProvider:
    def __init__(self, provider: ModelProvider, control: ExecutionControl):
        self.provider, self.control = provider, control

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.control.consume("model_calls")
        result = self.provider.complete(request)
        self.control.checkpoint()
        return result
