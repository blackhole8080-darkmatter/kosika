"""Persistent, one-use approval claims for execute-capability tool calls."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ApprovalLedgerError(RuntimeError):
    """An approval cannot be claimed or its outcome cannot be recorded."""


class ApprovalLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        connection: sqlite3.Connection | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=5)
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS actions (
                    action_hash TEXT PRIMARY KEY,
                    plan_hash TEXT NOT NULL,
                    approved_by TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    state TEXT NOT NULL,
                    outcome_hash TEXT,
                    error_type TEXT,
                    claimed_at TEXT NOT NULL,
                    finished_at TEXT
                )"""
            )
            connection.commit()
            return connection
        except (OSError, sqlite3.Error) as exc:
            if connection is not None:
                connection.close()
            raise ApprovalLedgerError(f"Cannot open approval ledger: {exc}") from exc

    def claim(
        self, action_hash: str, plan_hash: str, approved_by: str, tool: str
    ) -> None:
        """Commit a one-use claim before invoking the tool."""
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    """INSERT INTO actions
                    (action_hash, plan_hash, approved_by, tool, state, claimed_at)
                    VALUES (?, ?, ?, ?, 'claimed', ?)""",
                    (
                        action_hash,
                        plan_hash,
                        approved_by,
                        tool,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ApprovalLedgerError(
                f"Approval for action {action_hash} was already claimed"
            ) from exc
        except sqlite3.Error as exc:
            raise ApprovalLedgerError(f"Cannot claim approval: {exc}") from exc
        finally:
            connection.close()

    def finish(
        self, action_hash: str, *, result: Any = None, error: Exception | None = None
    ) -> None:
        if error is None:
            try:
                encoded = json.dumps(
                    result, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise ApprovalLedgerError(
                    "Tool result cannot be recorded as JSON"
                ) from exc
            state = "succeeded"
            outcome_hash = hashlib.sha256(encoded).hexdigest()
            error_type = None
        else:
            state = "failed"
            outcome_hash = None
            error_type = type(error).__name__
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    """UPDATE actions SET state = ?, outcome_hash = ?, error_type = ?,
                    finished_at = ? WHERE action_hash = ? AND state = 'claimed'""",
                    (
                        state,
                        outcome_hash,
                        error_type,
                        datetime.now(timezone.utc).isoformat(),
                        action_hash,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ApprovalLedgerError(
                        "Approval claim is missing or already finished"
                    )
        except sqlite3.Error as exc:
            raise ApprovalLedgerError(f"Cannot finish approval record: {exc}") from exc
        finally:
            connection.close()
