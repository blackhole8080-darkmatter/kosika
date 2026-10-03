"""A declared swarm runs only under one matching, one-use approval."""

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from kosika import cli
from kosika.approval_ledger import ApprovalLedger
from kosika.ast import ToolDecl
from kosika.execution import ExecutionLimits
from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.modules import load_program
from kosika.swarm import (
    SWARM_RUN_SCHEMA,
    SwarmApproval,
    SwarmRunError,
    run_swarm,
    swarm_plan_hash,
)

ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "examples" / "swarm_review.kos"
MODELS = ROOT / "examples" / "swarm_review_models.json"
TOOLS = ROOT / "examples" / "swarm_review_tools.json"
SWARM = "EditorialBoard"


def scripted(*texts: str) -> dict[str, ScriptedModelProvider]:
    return {
        "writer_model": ScriptedModelProvider(
            [ModelResponse(content=json.dumps({"text": text})) for text in texts]
        )
    }


class SwarmRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.program = load_program(EXAMPLE)
        self.plan = swarm_plan_hash(self.program, SWARM)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.ledger_path = Path(self.directory.name) / "ledger.sqlite"
        self.tool_calls = 0

    def read_docs(self, _declaration: ToolDecl, _arguments: dict[str, Any]) -> Any:
        self.tool_calls += 1
        return {"text": "notes"}

    def run_swarm(self, *texts: str, **overrides: Any):
        options: dict[str, Any] = {
            "approval": SwarmApproval(self.plan, "aryan"),
            "ledger": ApprovalLedger(self.ledger_path),
            "model_bindings": scripted(*texts),
            "tool_bindings": {"read_docs": self.read_docs},
        }
        options.update(overrides)
        return run_swarm(self.program, SWARM, **options)

    def ledger_row(self) -> tuple[str, str | None, str | None]:
        connection = sqlite3.connect(self.ledger_path)
        try:
            return connection.execute(
                "SELECT state, outcome_hash, error_type FROM actions"
            ).fetchone()
        finally:
            connection.close()

    def test_identical_rounds_stop_at_a_fixed_point(self) -> None:
        outcome = self.run_swarm("same", "same", "unused")
        self.assertEqual(outcome.terminated_by, "fixed_point")
        self.assertEqual([item.index for item in outcome.rounds], [1, 2])
        self.assertEqual([item.changed for item in outcome.rounds], [True, False])
        self.assertEqual(outcome.result, {"text": "same"})
        self.assertEqual(outcome.counts["model_calls"], 2)
        self.assertEqual(self.tool_calls, 2)
        self.assertEqual(outcome.to_dict()["schema"], SWARM_RUN_SCHEMA)
        self.assertNotIn("events", outcome.to_dict())
        self.assertTrue(outcome.events)

    def test_changing_rounds_stop_at_the_round_cap(self) -> None:
        outcome = self.run_swarm("one", "two", "three")
        self.assertEqual(outcome.terminated_by, "max_rounds")
        self.assertEqual(len(outcome.rounds), 3)
        self.assertEqual(outcome.result, {"text": "three"})

    def test_the_run_digest_is_deterministic(self) -> None:
        first = self.run_swarm("same", "same")
        other = Path(self.directory.name) / "other.sqlite"
        second = self.run_swarm("same", "same", ledger=ApprovalLedger(other))
        self.assertEqual(first.digest, second.digest)

    def test_a_completed_run_records_its_outcome(self) -> None:
        self.run_swarm("same", "same")
        state, outcome_hash, error_type = self.ledger_row()
        self.assertEqual(state, "succeeded")
        self.assertIsNotNone(outcome_hash)
        self.assertIsNone(error_type)

    def test_one_approval_authorizes_one_run(self) -> None:
        self.run_swarm("same", "same")
        calls = self.tool_calls
        with self.assertRaises(SwarmRunError) as raised:
            self.run_swarm("same", "same")
        self.assertEqual(raised.exception.code, "swarm/approval-claimed")
        self.assertEqual(self.tool_calls, calls)

    def test_missing_or_wrong_approval_runs_nothing(self) -> None:
        cases: dict[str, dict[str, Any]] = {
            "swarm/approval-required": {"approval": None},
            "swarm/approval-unattributed": {"approval": SwarmApproval(self.plan, "  ")},
            "swarm/approval-plan-mismatch": {
                "approval": SwarmApproval("0" * 64, "aryan")
            },
            "swarm/ledger-required": {"ledger": None},
        }
        for code, overrides in cases.items():
            with self.subTest(code=code):
                with self.assertRaises(SwarmRunError) as raised:
                    self.run_swarm("same", **overrides)
                self.assertEqual(raised.exception.code, code)
        self.assertEqual(self.tool_calls, 0)
        self.assertFalse(self.ledger_path.exists())

    def test_unknown_swarm_names_the_declared_ones(self) -> None:
        with self.assertRaisesRegex(SwarmRunError, "EditorialBoard"):
            swarm_plan_hash(self.program, "Missing")

    def test_the_plan_hash_follows_the_program(self) -> None:
        changed = Path(self.directory.name) / "changed.kos"
        changed.write_text(
            EXAMPLE.read_text(encoding="utf-8").replace(
                "max_rounds: 3", "max_rounds: 2"
            ),
            encoding="utf-8",
        )
        self.assertNotEqual(swarm_plan_hash(load_program(changed), SWARM), self.plan)

    def test_a_failed_round_spends_the_approval(self) -> None:
        with self.assertRaises(SwarmRunError) as raised:
            self.run_swarm("one")
        self.assertEqual(raised.exception.code, "swarm/round-failed")
        self.assertIn("round 2", str(raised.exception))
        state, outcome_hash, error_type = self.ledger_row()
        self.assertEqual(state, "failed")
        self.assertIsNone(outcome_hash)
        self.assertTrue(error_type)
        with self.assertRaises(SwarmRunError) as again:
            self.run_swarm("same", "same")
        self.assertEqual(again.exception.code, "swarm/approval-claimed")

    def test_the_budget_is_shared_across_rounds(self) -> None:
        with self.assertRaises(SwarmRunError) as raised:
            self.run_swarm(
                "one", "two", "three", limits=ExecutionLimits(max_model_calls=1)
            )
        self.assertEqual(raised.exception.code, "swarm/round-failed")
        self.assertIn("round 2", str(raised.exception))

    def test_a_missing_binding_fails_before_any_member_acts(self) -> None:
        with self.assertRaises(SwarmRunError):
            self.run_swarm("same", tool_bindings=None, model_bindings=None)
        self.assertEqual(self.tool_calls, 0)


class SwarmRunCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.ledger = Path(self.directory.name) / "ledger.sqlite"

    def invoke(self, *arguments: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        argv = ["kosika", "swarm", str(EXAMPLE), "--swarm", SWARM, *arguments]
        with patch.object(sys, "argv", argv):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main()
        return code, out.getvalue(), err.getvalue()

    def approved(self, *extra: str) -> tuple[int, str, str]:
        _, plan, _ = self.invoke("--plan")
        return self.invoke(
            "--approved-by",
            "aryan",
            "--plan-hash",
            json.loads(plan)["plan_hash"],
            "--approval-ledger",
            str(self.ledger),
            "--model-fixture",
            str(MODELS),
            "--tool-fixture",
            str(TOOLS),
            *extra,
        )

    def test_plan_prints_the_hash_and_runs_nothing(self) -> None:
        code, out, _ = self.invoke("--plan")
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(payload["agents"], ["researcher", "editor"])
        self.assertEqual(len(payload["plan_hash"]), 64)
        self.assertFalse(self.ledger.exists())

    def test_running_without_approval_is_refused(self) -> None:
        code, _, err = self.invoke()
        self.assertEqual(code, 2)
        self.assertIn("--approved-by", err)

    def test_an_approved_run_reports_rounds_and_writes_json(self) -> None:
        output = Path(self.directory.name) / "run.json"
        code, out, _ = self.approved("--output", str(output))
        self.assertEqual(code, 0)
        self.assertIn("terminated  : fixed_point", out)
        self.assertEqual(
            json.loads(output.read_text(encoding="utf-8"))["schema"],
            SWARM_RUN_SCHEMA,
        )

    def test_json_format_and_reuse_of_an_approval(self) -> None:
        code, out, _ = self.approved("--format", "json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["terminated_by"], "fixed_point")
        code, _, err = self.approved()
        self.assertEqual(code, 2)
        self.assertIn("already claimed", err)

    def test_conflicting_tool_sources_are_refused(self) -> None:
        code, _, err = self.approved(
            "--mcp-server", str(TOOLS), "--mcp-manifest", str(TOOLS)
        )
        self.assertEqual(code, 2)
        self.assertIn("--tool-fixture or --mcp-server", err)

    def test_mcp_server_requires_a_manifest(self) -> None:
        code, _, err = self.approved("--mcp-server", str(TOOLS))
        self.assertEqual(code, 2)
        self.assertIn("used together", err)


if __name__ == "__main__":
    unittest.main()
