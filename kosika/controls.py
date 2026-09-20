"""Host-configured budgets, cooperative cancellation, and explicit safe retries."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import asdict, dataclass, replace
from typing import Any

from .model import ModelProvider, ModelProviderError, ModelRequest, ModelResponse


class ExecutionError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "retryable": self.retryable}


class TransientFailure(ExecutionError):
    """A host adapter explicitly declares this failure safe to retry."""

    def __init__(self, message: str):
        super().__init__("transient", message, retryable=True)


@dataclass(frozen=True)
class ModelLimits:
    max_requests: int = 8
    max_tokens: int | None = None
    max_output_tokens: int = 1024
    attempts: int = 1
    deadline_seconds: float | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("max_requests", self.max_requests),
            ("max_output_tokens", self.max_output_tokens),
            ("attempts", self.attempts),
        ):
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.attempts > 3:
            raise ValueError("At most three explicit model attempts")
        if self.max_tokens is not None and (
            type(self.max_tokens) is not int or self.max_tokens < 1
        ):
            raise ValueError("max_tokens must be a positive integer")
        if self.deadline_seconds is not None and (
            not math.isfinite(self.deadline_seconds) or self.deadline_seconds <= 0
        ):
            raise ValueError("deadline_seconds must be finite and positive")


class ControlledProvider:
    """Serializes access to a provider and accounts for every attempted request.

    Cancellation/deadline checks occur between requests and after responses.
    In-flight network requests rely on the underlying transport timeout.
    Token usage is known only after a response: this is an accounting limit,
    not a guarantee that the remote provider will never bill beyond it.
    """

    def __init__(
        self,
        provider: ModelProvider,
        limits: ModelLimits = ModelLimits(),
        *,
        cancel: threading.Event | None = None,
        input_cost_per_token: float | None = None,
        output_cost_per_token: float | None = None,
    ):
        for rate in (input_cost_per_token, output_cost_per_token):
            if rate is not None and (not math.isfinite(rate) or rate < 0):
                raise ValueError("Cost rates must be finite and nonnegative")
        self.provider, self.limits = provider, limits
        self.cancel = cancel if cancel is not None else threading.Event()
        self.rates = (input_cost_per_token, output_cost_per_token)
        self.requests = 0
        self.tokens = 0
        self.telemetry: list[dict[str, Any]] = []
        self._start: float | None = None
        self._lock = threading.Lock()
        self._accounting_failed = False

    def _check(self) -> None:
        if self.cancel.is_set():
            raise ExecutionError("cancelled", "Execution was cancelled")
        if (
            self._start is not None
            and self.limits.deadline_seconds is not None
            and time.monotonic() - self._start >= self.limits.deadline_seconds
        ):
            raise ExecutionError("deadline", "Execution deadline exceeded")
        if self._accounting_failed:
            raise ExecutionError(
                "usage_unknown", "Cannot continue a token budget after missing usage"
            )

    def complete(self, request: ModelRequest) -> ModelResponse:
        with self._lock:
            if self._start is None:
                self._start = time.monotonic()
            for attempt in range(self.limits.attempts):
                self._check()
                if self.requests >= self.limits.max_requests or (
                    self.limits.max_tokens is not None
                    and self.tokens >= self.limits.max_tokens
                ):
                    raise ExecutionError("budget", "Model execution budget exhausted")
                self.requests += 1
                start = time.monotonic()
                metric: dict[str, Any] = {
                    "request": self.requests,
                    "attempt": attempt + 1,
                    "usage": None,
                    "estimated_cost": None,
                    "error": None,
                }
                try:
                    capped = min(
                        request.max_output_tokens or self.limits.max_output_tokens,
                        self.limits.max_output_tokens,
                    )
                    response = self.provider.complete(
                        replace(request, max_output_tokens=capped)
                    )
                    if response.usage is not None:
                        values = asdict(response.usage)
                        if any(
                            value is not None and (type(value) is not int or value < 0)
                            for value in values.values()
                        ):
                            self._accounting_failed = True
                            raise ExecutionError(
                                "usage_invalid", "Provider returned invalid token usage"
                            )
                        metric["usage"] = values
                        total = response.usage.total_tokens
                        if (
                            total is None
                            and response.usage.prompt_tokens is not None
                            and response.usage.completion_tokens is not None
                        ):
                            total = (
                                response.usage.prompt_tokens
                                + response.usage.completion_tokens
                            )
                        if total is not None:
                            self.tokens += total
                        elif self.limits.max_tokens is not None:
                            self._accounting_failed = True
                        if (
                            all(rate is not None for rate in self.rates)
                            and response.usage.prompt_tokens is not None
                            and response.usage.completion_tokens is not None
                        ):
                            metric["estimated_cost"] = response.usage.prompt_tokens * (
                                self.rates[0] or 0
                            ) + response.usage.completion_tokens * (self.rates[1] or 0)
                    elif self.limits.max_tokens is not None:
                        self._accounting_failed = True
                    self._check()
                    if (
                        self.limits.max_tokens is not None
                        and self.tokens > self.limits.max_tokens
                    ):
                        raise ExecutionError(
                            "budget", "Response exceeded token accounting budget"
                        )
                    return response
                except TransientFailure as exc:
                    metric["error"] = exc.code
                    if attempt + 1 >= self.limits.attempts:
                        raise
                except ExecutionError as exc:
                    metric["error"] = exc.code
                    raise
                except (TimeoutError, ModelProviderError) as exc:
                    metric["error"] = "provider"
                    raise ExecutionError("provider", str(exc)) from exc
                finally:
                    metric["latency_ms"] = (time.monotonic() - start) * 1000
                    self.telemetry.append(metric)
            raise AssertionError("Unreachable retry state")
