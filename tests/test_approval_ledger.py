from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from kosika.agent import AgentLoop, ApprovalRecord, ApprovalRequiredError, action_hash
from kosika.approval_ledger import ApprovalLedger, ApprovalLedgerError
from kosika.model import ModelResponse, ModelToolCall, ScriptedModelProvider
from kosika.parser import parse

SOURCE = """
record Result { ok: Bool }
tool mutate {
  source mcp "local_mock"
  operation: "mutate"
  access: execute
  output: Result
}
agent operator { can: [mutate] cannot: [] }
"""


class ApprovalLedgerTests(unittest.TestCase):
    def make_loop(self, ledger: ApprovalLedger | None, execute):
        provider = ScriptedModelProvider(
            [
                ModelResponse(tool_calls=(ModelToolCall("one", "mutate", {}),)),
                ModelResponse(content="Done"),
            ]
        )
        return AgentLoop(parse(SOURCE), provider, execute, approval_ledger=ledger)

    def approval_for(self, loop: AgentLoop) -> ApprovalRecord:
        plan = loop.plan_hash_for("operator", "Apply")
        return ApprovalRecord(
            plan_hash=plan,
            approved_by="reviewer",
            action_hash=action_hash(plan, "mutate", {}),
        )

    def test_success_is_recorded_and_retry_in_new_run_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "approvals.sqlite3"
            calls = []

            def execute(tool, arguments):
                calls.append(tool.name)
                return {"ok": True}

            first = self.make_loop(ApprovalLedger(path), execute)
            approval = self.approval_for(first)
            self.assertEqual(
                first.run("operator", "Apply", approval=approval).content, "Done"
            )
            second = self.make_loop(ApprovalLedger(path), execute)
            with self.assertRaisesRegex(ApprovalRequiredError, "already claimed"):
                second.run("operator", "Apply", approval=approval)
            self.assertEqual(calls, ["mutate"])
            with closing(sqlite3.connect(path)) as connection:
                row = connection.execute(
                    "SELECT state, approved_by, outcome_hash FROM actions"
                ).fetchone()
            self.assertEqual(row[0:2], ("succeeded", "reviewer"))
            self.assertEqual(len(row[2]), 64)

    def test_failed_or_interrupted_action_stays_consumed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "approvals.sqlite3"
            loop = self.make_loop(ApprovalLedger(path), lambda tool, args: 1 / 0)
            approval = self.approval_for(loop)
            with self.assertRaises(ZeroDivisionError):
                loop.run("operator", "Apply", approval=approval)
            with closing(sqlite3.connect(path)) as connection:
                row = connection.execute(
                    "SELECT state, error_type FROM actions"
                ).fetchone()
            self.assertEqual(row, ("failed", "ZeroDivisionError"))
            with self.assertRaisesRegex(ApprovalRequiredError, "already claimed"):
                self.make_loop(
                    ApprovalLedger(path), lambda tool, args: {"ok": True}
                ).run("operator", "Apply", approval=approval)

            # A claim with no outcome models a process stopping during the tool call.
            other = ApprovalLedger(Path(directory) / "interrupted.sqlite3")
            action_hash_value = approval.action_hash
            assert action_hash_value is not None
            other.claim(action_hash_value, approval.plan_hash, "reviewer", "mutate")
            with self.assertRaisesRegex(ApprovalLedgerError, "already claimed"):
                other.claim(action_hash_value, approval.plan_hash, "reviewer", "mutate")

    def test_execute_requires_ledger_before_tool_invocation(self) -> None:
        calls = []
        loop = self.make_loop(None, lambda tool, args: calls.append(tool.name))
        with self.assertRaisesRegex(
            ApprovalRequiredError, "persistent approval ledger"
        ):
            loop.run("operator", "Apply", approval=self.approval_for(loop))
        self.assertEqual(calls, [])

    def test_concurrent_claims_allow_only_one_worker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "approvals.sqlite3"

            def claim() -> bool:
                try:
                    ApprovalLedger(path).claim(
                        "same-action", "plan", "reviewer", "mutate"
                    )
                    return True
                except ApprovalLedgerError:
                    return False

            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(lambda _: claim(), range(2)))
            self.assertEqual(outcomes.count(True), 1)
            with closing(sqlite3.connect(path)) as connection:
                count = connection.execute("SELECT count(*) FROM actions").fetchone()[0]
            self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
