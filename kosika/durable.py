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

    def finished(self, scope: tuple[str, ...]) -> bool:
        """Whether this step already completed in an earlier attempt."""

        with self._db() as db:
            row = db.execute(
                "SELECT state FROM steps WHERE name=?", (json.dumps(scope),)
            ).fetchone()
        return row is not None and row[0] == "completed"

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

    def recover(self, scope: tuple[str, ...]) -> None:
        """Mark a failed, non-mutating step as not started.

        Used when a step's `otherwise` call takes over. Only steps without a
        claimed action reach here, so nothing uncertain is being forgotten.
        """

        self._save(scope, "pending", {"recovered": True})

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


class AgentExecutionSession:
    """Durable state for one direct-agent conversation.

    A journal is resumable only from a checkpoint known not to contain an
    in-flight model or tool call. An existing journal must be opened with
    ``resume=True``; binding and plan changes are rejected.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        binding_id: str,
        resume: bool = False,
    ):
        if not binding_id.strip():
            raise ValueError("Durable agent execution requires a host binding identity")
        self.path = Path(path)
        self.binding_id = binding_id
        self.resume = resume
        self.active = False
        self.owner: int | None = None

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = sqlite3.connect(str(self.path) + ".lock.sqlite", timeout=0)
        try:
            try:
                lock.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                raise ExecutionError(
                    "run_busy", "Another process owns this agent journal"
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
                """
                CREATE TABLE IF NOT EXISTS agent_run (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    plan_hash TEXT NOT NULL,
                    binding_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            with db:
                yield db
        finally:
            db.close()

    def bind(self, plan_hash: str, metadata: dict[str, Any]) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute(
                "SELECT plan_hash, binding_id, state, payload FROM agent_run WHERE id=1"
            ).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO agent_run VALUES (1, ?, ?, ?, ?)",
                    (
                        plan_hash,
                        self.binding_id,
                        "ready",
                        json.dumps(
                            {"metadata": metadata},
                            sort_keys=True,
                            allow_nan=False,
                        ),
                    ),
                )
                return None
            if row[0] != plan_hash or row[1] != self.binding_id:
                raise ExecutionError(
                    "plan_mismatch",
                    "Agent journal belongs to a different plan or host binding",
                )
            if not self.resume:
                raise ExecutionError(
                    "journal_exists",
                    "Agent journal already exists; use resume=True to continue it",
                )
            payload = json.loads(row[3])
            if not isinstance(payload, dict) or payload.get("metadata") != metadata:
                raise ExecutionError(
                    "plan_mismatch",
                    "Agent journal metadata differs from this run",
                )
            if not isinstance(payload, dict):
                raise ExecutionError(
                    "journal_invalid", "Agent journal payload is invalid"
                )
            return {"state": row[2], "payload": payload}

    def checkpoint(self, state: str, payload: dict[str, Any]) -> None:
        if state not in {
            "ready",
            "model_pending",
            "model_response",
            "tool_pending",
            "tool_result",
            "completed",
        }:
            raise ValueError(f"Unknown agent journal state {state!r}")
        with self._db() as db:
            db.execute(
                "UPDATE agent_run SET state=?, payload=? WHERE id=1",
                (state, json.dumps(payload, sort_keys=True, allow_nan=False)),
            )

    def inspect(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            row = db.execute(
                "SELECT plan_hash, binding_id, state, payload FROM agent_run WHERE id=1"
            ).fetchone()
            if row is None:
                return None
            return {
                "plan_hash": row[0],
                "binding_id": row[1],
                "state": row[2],
                "payload": json.loads(row[3]),
            }
        finally:
            db.close()


def inspect_journal(path: str | Path) -> dict[str, Any]:
    """Return bounded, read-only status for a workflow or agent journal."""
    journal = Path(path)
    if not journal.exists():
        raise FileNotFoundError(journal)
    db = sqlite3.connect(journal.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        tables = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        has_agent = "agent_run" in tables
        has_workflow = "steps" in tables
        if not has_agent and not has_workflow:
            raise ExecutionError(
                "journal_invalid", "Journal contains no Kosika run state"
            )
        result: dict[str, Any] = {
            "path": str(journal),
            "kind": (
                "mixed"
                if has_agent and has_workflow
                else "agent"
                if has_agent
                else "workflow"
            ),
        }
        if has_agent:
            row = db.execute(
                "SELECT plan_hash, binding_id, state, payload FROM agent_run WHERE id=1"
            ).fetchone()
            if row is None:
                raise ExecutionError(
                    "journal_invalid", "Agent journal state is missing"
                )
            try:
                payload = json.loads(row[3])
            except (TypeError, ValueError) as exc:
                raise ExecutionError(
                    "journal_invalid", "Agent journal payload is invalid"
                ) from exc
            if not isinstance(payload, dict):
                raise ExecutionError(
                    "journal_invalid", "Agent journal payload is invalid"
                )
            used = payload.get("used")
            checkpoint = {
                "step": payload.get("step"),
                "tool_index": payload.get("tool_index"),
                "model_steps": payload.get("model_steps"),
                "model_calls": used.get("model_calls")
                if isinstance(used, dict)
                else None,
                "tool_calls": used.get("tool_calls")
                if isinstance(used, dict)
                else None,
                "event_count": (
                    len(payload["trace_events"])
                    if isinstance(payload.get("trace_events"), list)
                    else None
                ),
            }
            result["agent"] = {
                "state": row[2],
                "plan_hash": row[0],
                "binding_id": row[1],
                "checkpoint": checkpoint,
            }
        if has_workflow:
            result["workflow"] = {
                "steps": [
                    {
                        "step": json.loads(name),
                        "state": state,
                    }
                    for name, state in db.execute(
                        "SELECT name, state FROM steps ORDER BY rowid"
                    )
                ]
            }
        return result
    except sqlite3.DatabaseError as exc:
        raise ExecutionError("journal_invalid", "Journal is not readable") from exc
    finally:
        db.close()
