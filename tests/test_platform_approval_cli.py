import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kosika import cli
from kosika.checker import check
from kosika.parser import parse


class ApprovalCliTests(unittest.TestCase):
    def test_pause_and_wrong_approval_remain_pending(self):
        source = """record R { text: Text }
        tool save { source mcp "host" operation: "save" access: execute input: R output: R }
        agent a { can: [save] }
        workflow main(q: R) -> R { require approval
          step x { let r: R = execute a.save(text: q.text) return r }
        }"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.syn").write_text(source)
            (root / "input.json").write_text('{"q":{"text":"review me"}}')
            args = [
                "kosika",
                "run",
                str(root / "main.syn"),
                "--input",
                str(root / "input.json"),
                "--journal",
                str(root / "journal.sqlite"),
                "--binding-id",
                "explicit-host",
            ]
            output = io.StringIO()
            with patch("sys.argv", args), contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(), 3)
            pending = json.loads(output.getvalue())
            self.assertEqual(pending["arguments"], {"text": "review me"})
            approval = {
                "plan_hash": pending["plan_hash"],
                "action_hash": "wrong",
                "approved_by": "reviewer",
            }
            (root / "approval.json").write_text(json.dumps(approval))
            with (
                patch(
                    "sys.argv",
                    [*args, "--approval-record", str(root / "approval.json")],
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(cli.main(), 3)
            self.assertFalse((root / "journal.sqlite.approvals.sqlite").exists())

    def test_nested_mutations_require_parent_approval(self):
        source = """record R { text: Text }
        tool save { source mcp "host" operation: "save" access: execute output: R }
        agent a { can: [save] }
        workflow child() -> R { require approval
          step x { let r: R = execute a.save() return r }
        }
        workflow main() -> R {
          step x { let r: R = call child() return r }
        }"""
        with self.assertRaisesRegex(ValueError, "must require approval"):
            check(parse(source))

    def test_failure_telemetry_is_written(self):
        source = """record R { text: Text }
        model m { input: R output: R }
        workflow main() -> R {
          step a { let first: R = use_model m(text: "first") return first }
          step b { let second: R = use_model m(text: first.text) return second }
        }"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.syn").write_text(source)
            (root / "responses.json").write_text('{"m":[{"text":"only one"}]}')
            telemetry = root / "telemetry.json"
            args = [
                "kosika",
                "run",
                str(root / "main.syn"),
                "--model-fixture",
                str(root / "responses.json"),
                "--trace",
                str(root / "run.jsonl"),
                "--telemetry",
                str(telemetry),
            ]
            with patch("sys.argv", args), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(), 2)
            metrics = json.loads(telemetry.read_text())["m"]
            self.assertEqual(len(metrics), 2)
            self.assertEqual(metrics[-1]["error"], "provider")
            trace = [json.loads(line) for line in (root / "run.jsonl").read_text().splitlines()]
            self.assertEqual(trace[-1]["event"], "run_failed")
            self.assertNotIn("run_complete", [event["event"] for event in trace])

    def test_telemetry_write_failure_returns_error(self):
        source = """record R { text: Text }
        model m { input: R output: R }
        workflow main(q: R) -> R {
          step answer { let result: R = use_model m(text: q.text) return result }
        }"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.syn").write_text(source)
            (root / "input.json").write_text('{"q":{"text":"hello"}}')
            (root / "responses.json").write_text('{"m":[{"text":"done"}]}')
            trace = root / "run.jsonl"
            telemetry = root / "missing" / "telemetry.json"
            args = [
                "kosika",
                "run",
                str(root / "main.syn"),
                "--input",
                str(root / "input.json"),
                "--model-fixture",
                str(root / "responses.json"),
                "--trace",
                str(trace),
                "--telemetry",
                str(telemetry),
            ]
            error = io.StringIO()
            with patch("sys.argv", args), contextlib.redirect_stderr(error):
                self.assertEqual(cli.main(), 2)
            self.assertIn("Telemetry write failed", error.getvalue())
            self.assertTrue(trace.exists())
