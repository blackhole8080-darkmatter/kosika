from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kosika import devtools
from kosika.runtime import replay

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples" / "research_assistant.kos"
CASES = ROOT / "examples" / "research_evaluations.json"
MCP_SERVER = ROOT / "examples" / "documents_mcp_server.py"
MCP_MANIFEST = ROOT / "examples" / "research_documents_manifest.json"
DOCUMENTS = ROOT / "examples" / "research_documents"


class EvaluationCliTests(unittest.TestCase):
    def call_cli(self, *arguments: str) -> tuple[int, str]:
        output = io.StringIO()
        with (
            patch.object(sys, "argv", ["kosika", *arguments]),
            patch.dict(os.environ, {"KOSIKA_DOCUMENTS_DIR": str(DOCUMENTS)}),
            contextlib.redirect_stdout(output),
        ):
            result = devtools.main()
        return result, output.getvalue()

    def test_evaluate_writes_report_and_replayable_case_traces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "evaluation"
            result, rendered = self.call_cli(
                "eval",
                str(SOURCE),
                str(CASES),
                "--workflow",
                "answer",
                "--output",
                str(output),
                "--mcp-server",
                str(MCP_SERVER),
                "--mcp-manifest",
                str(MCP_MANIFEST),
                "--mcp-source",
                "research_documents",
            )

            self.assertEqual(result, 0)
            self.assertIn('"passed": true', rendered)
            report = json.loads((output / "report.json").read_text(encoding="utf-8"))
            self.assertTrue(report["passed"])
            self.assertEqual(
                replay(output / "auditable_answer.jsonl")["sources"],
                [
                    "capabilities.md",
                    "traces.md",
                ],
            )

    def test_evaluate_returns_failure_for_failed_assertion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = root / "cases.json"
            cases.write_text(
                json.dumps(
                    [
                        {
                            "name": "wrong_answer",
                            "inputs": {
                                "question": {
                                    "text": "How does Kosika keep AI workflows auditable?"
                                }
                            },
                            "expected": {"text": "not the fixture", "sources": []},
                            "responses": {
                                "reader": [
                                    {
                                        "text": "Kosika keeps AI workflows auditable through explicit capabilities, bounded execution, and replayable traces.",
                                        "sources": ["capabilities.md", "traces.md"],
                                    }
                                ]
                            },
                        }
                    ]
                ),
            )
            output = root / "evaluation"
            result, rendered = self.call_cli(
                "eval",
                str(SOURCE),
                str(cases),
                "--workflow",
                "answer",
                "--output",
                str(output),
                "--mcp-server",
                str(MCP_SERVER),
                "--mcp-manifest",
                str(MCP_MANIFEST),
                "--mcp-source",
                "research_documents",
            )

            self.assertEqual(result, 1)
            self.assertIn('"passed": false', rendered)
            report = json.loads((output / "report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            self.assertEqual(report["cases"][0]["failures"], ["exact output mismatch"])


if __name__ == "__main__":
    unittest.main()
