"""Local durable checkpoints and approval pauses; no automatic rollback."""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterator, Sequence

from .agent import ApprovalRecord, action_hash
from .approval_ledger import ApprovalLedger
from .controls import ExecutionError
from .trace_replay import replay_events


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


class ApprovalPending(ExecutionError):
    def __init__(self, request: dict[str, Any]):
        super().__init__(
            "approval_required",
            "Review the pending action and provide a matching approval",
        )
        self.request = request


class ExecutionSession:
    """One journal file represents one run, identified by its complete plan.

    binding_id is a host-owned identity for model, adapter and policy configuration.
    Change it when those bindings change. This store is trusted local application
    state, not authenticated evidence against an attacker who can rewrite it.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        binding_id: str,
        approvals: Sequence[ApprovalRecord] = (),
    ):
        if not binding_id.strip():
            raise ValueError("Durable execution requires a host binding identity")
        self.path = Path(path)
        self.binding_id = binding_id
        self.approvals = tuple(approvals)
        self.plan_hash = ""
        self.active = False
        self.owner: int | None = None
        self.ledger = ApprovalLedger(str(self.path) + ".approvals.sqlite")

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = sqlite3.connect(str(self.path) + ".lock.sqlite", timeout=0)
        try:
            try:
                lock.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                raise ExecutionError(
                    "run_busy", "Another process owns this run journal"
                ) from exc
            self.active = True
            self.owner = threading.get_ident()
            yield
        finally:
            self.active = False
            self.owner = None
            lock.close()

    @contextlib.contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path)
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS plan (id INTEGER PRIMARY KEY CHECK(id=1), hash TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS steps (name TEXT PRIMARY KEY, state TEXT NOT NULL, payload TEXT NOT NULL)"
            )
            with db:
                yield db
        finally:
            db.close()

    def bind(self, plan: Any) -> None:
        self.plan_hash = digest({"plan": plan, "binding_id": self.binding_id})
        with self._db() as db:
            db.execute("INSERT OR IGNORE INTO plan VALUES (1, ?)", (self.plan_hash,))
            if (
                db.execute("SELECT hash FROM plan WHERE id=1").fetchone()[0]
                != self.plan_hash
            ):
                raise ExecutionError(
                    "plan_mismatch",
                    "Journal belongs to a different program, input or binding configuration",
                )

    def cached(
        self,
        scope: tuple[str, ...],
        arguments: dict[str, Any],
        start: dict[str, Any],
        output_schema: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        with self._db() as db:
            row = db.execute(
                "SELECT state, payload FROM steps WHERE name=?", (json.dumps(scope),)
            ).fetchone()
        if row is None or row[0] == "pending":
            return None
        if row[0] != "completed":
            raise ExecutionError(
                "uncertain_outcome",
                "An interrupted or failed step requires reconciliation; it will not be repeated",
            )
        events = json.loads(row[1])
        if (
            not isinstance(events, list)
            or len(events) != 4
            or not isinstance(events[1], dict)
            or digest(events[1].get("arguments")) != digest(arguments)
        ):
            raise ExecutionError("checkpoint_mismatch", "Cached step arguments differ")
        replay_events(
            [
                {**start, "output_schema": output_schema},
                *events,
                {
                    "event": "run_complete",
                    "workflow": start["workflow"],
                    "result": events[-1].get("result"),
                },
            ]
        )
        return events

    def _save(self, scope: tuple[str, ...], state: str, payload: Any) -> None:
        with self._db() as db:
            db.execute(
                "INSERT OR REPLACE INTO steps VALUES (?, ?, ?)",
                (
                    json.dumps(scope),
                    state,
                    json.dumps(payload, sort_keys=True, allow_nan=False),
                ),
            )

    def start(
        self, scope: tuple[str, ...], tool: str | None, arguments: dict[str, Any]
    ) -> str | None:
        action = None
        if tool is not None:
            action = action_hash(self.plan_hash, json.dumps([*scope, tool]), arguments)
            approval = next(
                (
                    record
                    for record in self.approvals
                    if record.plan_hash == self.plan_hash
                    and record.action_hash == action
                    and record.approved_by.strip()
                ),
                None,
            )
            if approval is None:
                request = {
                    "plan_hash": self.plan_hash,
                    "action_hash": action,
                    "tool": tool,
                    "arguments": arguments,
                    "step": list(scope),
                }
                self._save(scope, "pending", request)
                raise ApprovalPending(request)
            self.ledger.claim(action, self.plan_hash, approval.approved_by, tool)
        self._save(scope, "started", {"arguments": arguments})
        return action

    def complete(
        self, scope: tuple[str, ...], events: list[dict[str, Any]], action: str | None
    ) -> None:
        if action is not None:
            self.ledger.finish(action, result=events[-1]["result"])
        self._save(scope, "completed", events)

    def inspect(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        # Open existing state read-only: inspecting does not initialize a run.
        db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            return [
                {
                    "step": json.loads(name),
                    "state": state,
                    "payload": json.loads(payload),
                }
                for name, state, payload in db.execute(
                    "SELECT name, state, payload FROM steps ORDER BY rowid"
                )
            ]
        finally:
            db.close()
