import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kosika import cli
from kosika.manifest import load_manifest
from kosika.model import ModelResponse, ModelToolCall, ScriptedModelProvider

ROOT = Path(__file__).parents[1]
SERVER = ROOT / "examples" / "mcp_host_server.py"
MANIFEST = ROOT / "examples" / "mcp_manifest.json"
MOCK_SOURCE = ROOT / "examples" / "test_host.syn"
LIVE_SOURCE = ROOT / "examples" / "test_host_live.syn"


class CliCoverageTests(unittest.TestCase):
    def call_cli(self, *arguments: str) -> tuple[int, str]:
        output = io.StringIO()
        with (
            patch.object(sys, "argv", ["kosika", *arguments]),
            contextlib.redirect_stdout(output),
        ):
            result = cli.main()
        return result, output.getvalue()

    def call_cli_error(self, *arguments: str) -> tuple[int, str]:
        error = io.StringIO()
        with (
            patch.object(sys, "argv", ["kosika", *arguments]),
            contextlib.redirect_stderr(error),
        ):
            result = cli.main()
        return result, error.getvalue()

    def test_check_run_and_replay_commands(self) -> None:
        result, output = self.call_cli("check", str(MOCK_SOURCE))
        self.assertEqual(result, 0)
        self.assertIn("OK:", output)

        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            result, output = self.call_cli(
                "run", str(MOCK_SOURCE), "--trace", str(trace)
            )
            self.assertEqual(result, 0)
            self.assertIn("Trace:", output)
            self.assertTrue(trace.exists())

            result, output = self.call_cli("replay", str(trace))
            self.assertEqual(result, 0)
            self.assertIn("'mount': '/'", output)
            self.assertIn("'used_percent': 42.0", output)

    def test_agent_command_runs_with_deterministic_provider(self) -> None:
        provider = ScriptedModelProvider(
            [
                ModelResponse(tool_calls=(ModelToolCall("call-1", "check_disk", {}),)),
                ModelResponse(content="Disk usage is 42 percent."),
            ]
        )
        with patch.object(cli, "OpenAICompatibleProvider", return_value=provider):
            result, output = self.call_cli(
                "agent",
                str(MOCK_SOURCE),
                "--agent",
                "monitor",
                "--task",
                "Check the disk",
                "--model",
                "test-model",
            )
        self.assertEqual(result, 0)
        self.assertIn("Plan hash:", output)
        self.assertIn("Disk usage is 42 percent.", output)
        self.assertIn("Model steps: 2", output)
        self.assertEqual(len(provider.requests), 2)

    def test_agent_command_requires_both_approval_hashes(self) -> None:
        with (
            patch.object(
                sys,
                "argv",
                [
                    "kosika",
                    "agent",
                    str(MOCK_SOURCE),
                    "--agent",
                    "monitor",
                    "--task",
                    "Check",
                    "--model",
                    "test-model",
                    "--approval-plan-hash",
                    "old-plan-only",
                ],
            ),
            self.assertRaises(SystemExit) as incomplete_approval,
        ):
            cli.main()
        self.assertEqual(incomplete_approval.exception.code, 2)

    def test_agent_command_requires_model_configuration(self) -> None:
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(
                sys,
                "argv",
                [
                    "kosika",
                    "agent",
                    str(MOCK_SOURCE),
                    "--agent",
                    "monitor",
                    "--task",
                    "Check the disk",
                ],
            ),
            self.assertRaises(SystemExit) as missing_model,
        ):
            cli.main()
        self.assertEqual(missing_model.exception.code, 2)

    def test_run_rejects_incomplete_live_mcp_arguments(self) -> None:
        with (
            patch.object(
                sys,
                "argv",
                [
                    "kosika",
                    "run",
                    str(MOCK_SOURCE),
                    "--mcp-server",
                    str(SERVER),
                ],
            ),
            self.assertRaises(SystemExit) as missing_manifest,
        ):
            cli.main()
        self.assertEqual(missing_manifest.exception.code, 2)

        with (
            patch.object(
                sys,
                "argv",
                [
                    "kosika",
                    "run",
                    str(MOCK_SOURCE),
                    "--mcp-manifest",
                    str(MANIFEST),
                ],
            ),
            self.assertRaises(SystemExit) as missing_server,
        ):
            cli.main()
        self.assertEqual(missing_server.exception.code, 2)

    def test_live_run_and_manifest_subcommands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            generated = Path(directory) / "generated.json"
            result, output = self.call_cli(
                "manifest",
                "generate",
                "--mcp-server",
                str(SERVER),
                "--output",
                str(generated),
            )
            self.assertEqual(result, 0)
            self.assertIn("Generated manifest", output)
            self.assertEqual(load_manifest(generated), load_manifest(MANIFEST))

            result, output = self.call_cli(
                "manifest",
                "validate",
                str(generated),
                "--mcp-server",
                str(SERVER),
            )
            self.assertEqual(result, 0)
            self.assertIn("Valid manifest", output)

            result, output = self.call_cli(
                "manifest",
                "diff",
                str(generated),
                "--mcp-server",
                str(SERVER),
            )
            self.assertEqual(result, 0)
            self.assertIn("No drift", output)

            result, output = self.call_cli(
                "manifest",
                "diff",
                str(generated),
                "--mcp-server",
                str(SERVER),
                "--json",
            )
            self.assertEqual(result, 0)
            self.assertFalse(json.loads(output)["drift"])

            result, output = self.call_cli(
                "manifest",
                "check",
                str(generated),
                "--mcp-server",
                str(SERVER),
                "--json",
            )
            self.assertEqual(result, 0)
            self.assertFalse(json.loads(output)["drift"])

            drifted_data = json.loads(generated.read_text(encoding="utf-8"))
            drifted_data["server"]["version"] = "9.9.9"
            drifted = Path(directory) / "drifted.json"
            drifted.write_text(json.dumps(drifted_data), encoding="utf-8")
            result, output = self.call_cli(
                "manifest",
                "check",
                str(drifted),
                "--mcp-server",
                str(SERVER),
            )
            self.assertEqual(result, 1)
            self.assertIn("MCP manifest drift detected", output)

            trace = Path(directory) / "live-trace.jsonl"
            result, output = self.call_cli(
                "run",
                str(LIVE_SOURCE),
                "--trace",
                str(trace),
                "--mcp-server",
                str(SERVER),
                "--mcp-manifest",
                str(generated),
            )
            self.assertEqual(result, 0)
            self.assertNotIn("error", output.lower())
            trace_events = [
                json.loads(line)
                for line in trace.read_text(encoding="utf-8").splitlines()
            ]
            tool_return = next(
                event for event in trace_events if event["event"] == "tool_return"
            )
            self.assertEqual(tool_return["transport"], "stdio")

    def test_run_rejects_invalid_inputs_and_approval_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid_input = root / "invalid-input.json"
            invalid_input.write_text("[]", encoding="utf-8")
            result, error = self.call_cli_error(
                "run", str(MOCK_SOURCE), "--input", str(invalid_input)
            )
            self.assertEqual(result, 2)
            self.assertIn("JSON object", error)

            journal = root / "journal.jsonl"
            result, error = self.call_cli_error(
                "run", str(MOCK_SOURCE), "--journal", str(journal)
            )
            self.assertEqual(result, 2)
            self.assertIn("journal with --binding-id", error)

            result, error = self.call_cli_error(
                "run", str(MOCK_SOURCE), "--binding-id", "worker"
            )
            self.assertEqual(result, 2)
            self.assertIn("journal with --binding-id", error)

            approval = root / "approval.json"
            approval.write_text(json.dumps({"plan_hash": "only-one"}), encoding="utf-8")
            result, error = self.call_cli_error(
                "run",
                str(MOCK_SOURCE),
                "--journal",
                str(journal),
                "--binding-id",
                "worker",
                "--approval-record",
                str(approval),
            )
            self.assertEqual(result, 2)
            self.assertIn("Approval record needs", error)

    def test_cli_rejects_invalid_model_and_assistant_fixtures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_fixture = root / "model.json"
            model_fixture.write_text(json.dumps({"writer": []}), encoding="utf-8")
            result, error = self.call_cli_error(
                "run",
                str(MOCK_SOURCE),
                "--model-fixture",
                str(model_fixture),
            )
            self.assertEqual(result, 2)
            self.assertIn("requires a nonempty list", error)

            assistant_fixture = root / "assistant.json"
            assistant_fixture.write_text(json.dumps({"responses": []}), encoding="utf-8")
            result, error = self.call_cli_error(
                "assistant",
                str(MOCK_SOURCE),
                "--task",
                "Explain",
                "--fixture",
                str(assistant_fixture),
            )
            self.assertEqual(result, 2)
            self.assertIn("Assistant fixture", error)


if __name__ == "__main__":
    unittest.main()
