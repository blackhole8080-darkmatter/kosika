from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kosika.agent import ApprovalRecord
from kosika.durable import ApprovalPending, ExecutionSession
from kosika.modules import load_program
from kosika.runtime import replay, run

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "approval_mutation.kos"


class ApprovalMutationTests(unittest.TestCase):
    def test_pause_exact_resume_single_effect_and_replay(self) -> None:
        program = load_program(EXAMPLE)
        calls: list[tuple[str, dict[str, str]]] = []

        def apply_change(tool: str, arguments: dict[str, str]) -> dict[str, str]:
            calls.append((tool, arguments))
            return {
                "status": "applied",
                "change": arguments["change"],
                "sink": "test-sink.json",
            }

        inputs = {"request": {"change": "Set the reviewed deployment flag to ready."}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = root / "run.sqlite"
            trace = root / "run.jsonl"
            binding_id = "approval-mutation-test-v1"

            with self.assertRaises(ApprovalPending) as pending:
                run(
                    program,
                    "apply_change",
                    trace,
                    inputs=inputs,
                    tool_bindings={"apply_change": apply_change},
                    session=ExecutionSession(journal, binding_id=binding_id),
                )

            request = pending.exception.request
            self.assertEqual(request["tool"], "apply_change")
            self.assertEqual(request["step"], ["apply_change", "mutate"])
            self.assertEqual(calls, [])

            wrong = ApprovalRecord(
                request["plan_hash"],
                "reviewer",
                action_hash="wrong-action",
            )
            with self.assertRaises(ApprovalPending):
                run(
                    program,
                    "apply_change",
                    trace,
                    inputs=inputs,
                    tool_bindings={"apply_change": apply_change},
                    session=ExecutionSession(
                        journal, binding_id=binding_id, approvals=[wrong]
                    ),
                )
            self.assertEqual(calls, [])

            approval = ApprovalRecord(
                request["plan_hash"],
                "reviewer",
                action_hash=request["action_hash"],
            )
            self.assertEqual(
                run(
                    program,
                    "apply_change",
                    trace,
                    inputs=inputs,
                    tool_bindings={"apply_change": apply_change},
                    session=ExecutionSession(
                        journal, binding_id=binding_id, approvals=[approval]
                    ),
                ),
                {
                    "status": "applied",
                    "change": "Set the reviewed deployment flag to ready.",
                    "sink": "test-sink.json",
                },
            )
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0].name, "apply_change")
            self.assertEqual(calls[0][1], inputs["request"])

            self.assertEqual(
                run(
                    program,
                    "apply_change",
                    trace,
                    inputs=inputs,
                    tool_bindings={"apply_change": apply_change},
                    session=ExecutionSession(journal, binding_id=binding_id),
                )["status"],
                "applied",
            )
            self.assertEqual(len(calls), 1)
            self.assertEqual(replay(trace)["status"], "applied")


if __name__ == "__main__":
    unittest.main()
