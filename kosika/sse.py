"""Dependency-light SSE delivery for validated normalized Kosika events.

The stream is read-only. Producers publish already-normalized events, and this
module validates sequence continuity before retaining or persisting them. The
HTTP workbench adapter is intentionally kept separate so the event store can
also support a future CLI or another local transport.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from pathlib import Path
from typing import Any, Mapping, Sequence

from .events import (
    EventValidationError,
    read_event_jsonl,
    validate_events,
    write_event_jsonl,
)

DEFAULT_BUFFER_SIZE = 512
DEFAULT_REPLAY_LIMIT = 128
DEFAULT_MAX_SUBSCRIBERS = 8
HEARTBEAT_SECONDS = 15.0
SSE_WAIT_SECONDS = 1.0


class EventStreamError(ValueError):
    """Raised when an event stream request or source is invalid."""


class ReplayGapError(EventStreamError):
    """Raised when a reconnect cursor is older than the bounded replay buffer."""

    def __init__(self, oldest: int, latest: int | None) -> None:
        self.oldest = oldest
        self.latest = latest
        latest_text = str(latest) if latest is not None else "none"
        super().__init__(
            f"Replay cursor is older than the retained buffer "
            f"(oldest={oldest}, latest={latest_text})."
        )


class SubscriberLimitError(EventStreamError):
    """Raised when a stream has reached its bounded subscriber count."""


class EventStream:
    """Thread-safe bounded stream of one contiguous normalized event sequence.

    ``jsonl_path`` is optional. When supplied, the complete normalized event
    history is validated and rewritten atomically on every publication. The
    in-memory deque remains bounded for reconnect replay and slow consumers.
    """

    def __init__(
        self,
        *,
        jsonl_path: str | Path | None = None,
        buffer_size: int = DEFAULT_BUFFER_SIZE,
        max_replay: int = DEFAULT_REPLAY_LIMIT,
        max_subscribers: int = DEFAULT_MAX_SUBSCRIBERS,
    ) -> None:
        if buffer_size < 1:
            raise ValueError("Event buffer size must be positive")
        if max_replay < 1:
            raise ValueError("Replay limit must be positive")
        if max_subscribers < 1:
            raise ValueError("Subscriber limit must be positive")
        self.buffer_size = buffer_size
        self.max_replay = min(max_replay, buffer_size)
        self.max_subscribers = max_subscribers
        self.jsonl_path = Path(jsonl_path) if jsonl_path is not None else None
        self._events: deque[dict[str, Any]] = deque(maxlen=buffer_size)
        self._history: list[dict[str, Any]] = []
        self._next_sequence = 1
        self._subscribers = 0
        self._closed = False
        self._condition = threading.Condition()
        if self.jsonl_path is not None and self.jsonl_path.exists():
            try:
                loaded = list(read_event_jsonl(self.jsonl_path))
            except EventValidationError as exc:
                raise EventStreamError(
                    f"Event JSONL source is invalid: {exc}"
                ) from exc
            self._history = loaded
            self._events.extend(loaded[-buffer_size:])
            if loaded:
                self._next_sequence = loaded[-1]["sequence"] + 1

    @property
    def next_sequence(self) -> int:
        with self._condition:
            return self._next_sequence

    @property
    def subscriber_count(self) -> int:
        with self._condition:
            return self._subscribers

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed

    def publish(self, events: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
        """Validate and publish one contiguous batch of normalized events."""

        if not events:
            return ()
        with self._condition:
            if self._closed:
                raise EventStreamError("Event stream is closed")
            try:
                validated = validate_events(events, sequence_start=self._next_sequence)
            except EventValidationError as exc:
                raise EventStreamError(str(exc)) from exc
            if self.jsonl_path is not None:
                candidate_history = [*self._history, *validated]
                write_event_jsonl(self.jsonl_path, candidate_history)
                self._history = candidate_history
            self._events.extend(validated)
            self._next_sequence += len(validated)
            self._condition.notify_all()
            return validated

    def _cursor_sequence(self, cursor: int | str | None) -> int:
        if cursor is None or cursor == "":
            return 0
        if isinstance(cursor, int):
            if cursor < 0:
                raise EventStreamError("Replay sequence must not be negative")
            return cursor
        if not isinstance(cursor, str):
            raise EventStreamError("Replay cursor must be a sequence or event ID")
        try:
            sequence = int(cursor)
        except ValueError:
            sequence = next(
                (
                    event["sequence"]
                    for event in self._events
                    if event["event_id"] == cursor
                ),
                -1,
            )
            if sequence < 0:
                raise ReplayGapError(self.oldest_sequence, self.latest_sequence)
            return sequence
        if sequence < 0:
            raise EventStreamError("Replay sequence must not be negative")
        return sequence

    @property
    def oldest_sequence(self) -> int:
        with self._condition:
            return self._events[0]["sequence"] if self._events else self._next_sequence

    @property
    def latest_sequence(self) -> int | None:
        with self._condition:
            return self._events[-1]["sequence"] if self._events else None

    def _read_after_locked(
        self, cursor: int | str | None, limit: int | None = None
    ) -> tuple[dict[str, Any], ...]:
        bounded_limit = self.max_replay if limit is None else limit
        if bounded_limit < 1 or bounded_limit > self.max_replay:
            raise EventStreamError(
                f"Replay limit must be between one and {self.max_replay}"
            )
        if cursor is None or cursor == "":
            return tuple(self._events)[-bounded_limit:]
        sequence = self._cursor_sequence(cursor)
        if self._events and sequence < self._events[0]["sequence"] - 1:
            raise ReplayGapError(self._events[0]["sequence"], self._events[-1]["sequence"])
        return tuple(
            event for event in self._events if event["sequence"] > sequence
        )[:bounded_limit]

    def read_after(
        self, cursor: int | str | None = None, *, limit: int | None = None
    ) -> tuple[dict[str, Any], ...]:
        """Return bounded events after a sequence or event-ID cursor."""

        with self._condition:
            return self._read_after_locked(cursor, limit)

    def wait_for(
        self,
        cursor: int | str | None = None,
        *,
        timeout: float = HEARTBEAT_SECONDS,
        limit: int | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Wait for events after ``cursor`` or return an empty heartbeat batch."""

        if timeout < 0:
            raise EventStreamError("Wait timeout must not be negative")
        with self._condition:
            events = self._read_after_locked(cursor, limit)
            if events or self._closed:
                return events
            self._condition.wait(timeout)
            return self._read_after_locked(cursor, limit)

    def acquire_subscriber(self) -> None:
        with self._condition:
            if self._closed:
                raise EventStreamError("Event stream is closed")
            if self._subscribers >= self.max_subscribers:
                raise SubscriberLimitError("The event stream has reached its subscriber limit")
            self._subscribers += 1

    def release_subscriber(self) -> None:
        with self._condition:
            if self._subscribers > 0:
                self._subscribers -= 1
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()


def format_sse(event: Mapping[str, Any]) -> bytes:
    """Encode one validated normalized event as an SSE frame."""

    validated = validate_events((event,), sequence_start=int(event["sequence"]))[0]
    payload = json.dumps(
        validated, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return (
        f"id: {validated['event_id']}\n"
        f"event: {validated['type']}\n"
        f"data: {payload}\n\n"
    ).encode("utf-8")


__all__ = [
    "DEFAULT_BUFFER_SIZE",
    "DEFAULT_MAX_SUBSCRIBERS",
    "DEFAULT_REPLAY_LIMIT",
    "EventStream",
    "EventStreamError",
    "HEARTBEAT_SECONDS",
    "ReplayGapError",
    "SSE_WAIT_SECONDS",
    "SubscriberLimitError",
    "format_sse",
]
