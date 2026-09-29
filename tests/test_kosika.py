from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kosika.ast import FieldExpr, LiteralExpr, VariableExpr
from kosika.checker import CheckError, check
from kosika.parser import parse
from kosika.runtime import replay, run
from kosika.schema import SchemaValidationError

EXAMPLE = Path(__file__).parents[1] / "examples" / "test_host.syn"
TYPED_SOURCE = """
record DiskRequest {
  mount: Text
}

record DiskStatus {
  mount: Text
  used_percent: Number
}

tool check_disk {
  source mcp "local_mock"
  input: DiskRequest
  operation: "get_disk_info"
  access: observe
  output: DiskStatus
}

agent monitor {
  can: [check_disk]
  cannot: []
}

workflow main() -> DiskStatus {
  step inspect {
    let result = execute monitor.check_disk(mount: "/")
    return result
  }
}
"""
DATAFLOW_SOURCE = TYPED_SOURCE.replace(
    '    let result = execute monitor.check_disk(mount: "/")\n    return result\n',
    '    let status: DiskStatus = execute monitor.check_disk(mount: "/")\n'
    "    return status\n"
    "  }\n"
    "  step recheck {\n"
    "    let final: DiskStatus = execute monitor.check_disk(mount: status.mount)\n"
    "    return final\n",
)
STRUCTURED_SOURCE = """
record Metadata {
  name: Text
}

record StructuredRequest {
  mount: Text
  tags: Text[]
  metadata: Metadata
}

record StructuredStatus {
  mount: Text
  tags: Text[]
  metadata: Metadata
}

tool inspect_structured {
  source mcp "local_mock"
  input: StructuredRequest
  operation: "get_structured_info"
  access: observe
  output: StructuredStatus
}

agent monitor {
  can: [inspect_structured]
  cannot: []
}

workflow main() -> StructuredStatus {
  step inspect {
    let status: StructuredStatus = execute monitor.inspect_structured(
      mount: "/",
      tags: ["disk", "local"],
      metadata: {name: "root-disk"}
    )
    return status
  }
  step recheck {
    let final: StructuredStatus = execute monitor.inspect_structured(
      mount: status.mount,
      tags: status.tags,
      metadata: status.metadata
    )
    return final
  }
}
"""


class KosikaV01Tests(unittest.TestCase):
    def test_parse_and_check_example(self) -> None:
        program = parse(EXAMPLE.read_text(encoding="utf-8"))
        check(program)
        self.assertEqual(program.records[0].name, "DiskStatus")
        self.assertEqual(program.tools[0].operation, "get_disk_info")
        self.assertEqual(program.agents[0].can, ("check_disk",))

    def test_typed_workflow_arguments_are_checked_and_traced(self) -> None:
        program = parse(TYPED_SOURCE)
        check(program)
        self.assertEqual(program.tools[0].input, "DiskRequest")
        expression = program.workflows[0].steps[0].call.arguments[0][1]
        self.assertIsInstance(expression, LiteralExpr)
        assert isinstance(expression, LiteralExpr)
        self.assertEqual(expression.value, "/")
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "typed.jsonl"
            result = run(program, "main", trace)
            events = [
                json.loads(line)
                for line in trace.read_text(encoding="utf-8").splitlines()
            ]
            tool_call = next(event for event in events if event["event"] == "tool_call")
            self.assertEqual(tool_call["arguments"], {"mount": "/"})
            self.assertEqual(result["used_percent"], 42.0)

    def test_step_to_step_dataflow_evaluates_bound_fields(self) -> None:
        program = parse(DATAFLOW_SOURCE)
        check(program)
        second_expression = program.workflows[0].steps[1].call.arguments[0][1]
        self.assertIsInstance(second_expression, FieldExpr)
        assert isinstance(second_expression, FieldExpr)
        self.assertIsInstance(second_expression.base, VariableExpr)
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "dataflow.jsonl"
            result = run(program, "main", trace)
            events = [
                json.loads(line)
                for line in trace.read_text(encoding="utf-8").splitlines()
            ]
            calls = [event for event in events if event["event"] == "tool_call"]
            completions = [
                event for event in events if event["event"] == "step_complete"
            ]
            self.assertEqual(
                [call["arguments"] for call in calls],
                [
                    {"mount": "/"},
                    {"mount": "/"},
                ],
            )
            self.assertEqual(
                [completion["binding"] for completion in completions],
                ["status", "final"],
            )
            self.assertEqual(result["used_percent"], 42.0)

    def test_structured_values_flow_through_runtime(self) -> None:
        program = parse(STRUCTURED_SOURCE)
        check(program)
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "structured.jsonl"
            result = run(program, "main", trace)
            events = [
                json.loads(line)
                for line in trace.read_text(encoding="utf-8").splitlines()
            ]
            calls = [event for event in events if event["event"] == "tool_call"]
            self.assertEqual(
                calls[0]["arguments"],
                {
                    "mount": "/",
                    "tags": ["disk", "local"],
                    "metadata": {"name": "root-disk"},
                },
            )
            self.assertEqual(calls[1]["arguments"], calls[0]["arguments"])
            self.assertEqual(result["metadata"]["name"], "root-disk")

    def test_dataflow_rejects_unknown_and_mismatched_references(self) -> None:
        with self.assertRaisesRegex(CheckError, "unknown variable"):
            check(parse(DATAFLOW_SOURCE.replace("status.mount", "missing.mount")))
        with self.assertRaisesRegex(CheckError, "unknown field"):
            check(parse(DATAFLOW_SOURCE.replace("status.mount", "status.missing")))
        with self.assertRaisesRegex(CheckError, "expects 'Text'"):
            check(parse(DATAFLOW_SOURCE.replace("status.mount", "status.used_percent")))
        with self.assertRaisesRegex(CheckError, "binding 'status' is"):
            check(
                parse(
                    DATAFLOW_SOURCE.replace(
                        "let status: DiskStatus", "let status: DiskRequest"
                    )
                )
            )

    def test_typed_workflow_arguments_reject_invalid_literals(self) -> None:
        with self.assertRaisesRegex(CheckError, "expects 'Text'"):
            check(parse(TYPED_SOURCE.replace('mount: "/"', "mount: 7")))
        with self.assertRaisesRegex(CheckError, "unknown tool arguments"):
            check(parse(TYPED_SOURCE.replace('mount: "/"', 'other: "/"')))
        with self.assertRaisesRegex(CheckError, "duplicate tool arguments"):
            check(
                parse(TYPED_SOURCE.replace('mount: "/"', 'mount: "/", mount: "/tmp"'))
            )

    def test_unknown_tool_input_record_is_rejected(self) -> None:
        with self.assertRaisesRegex(CheckError, "accepts unknown record"):
            check(parse(TYPED_SOURCE.replace("input: DiskRequest", "input: Missing")))

    def test_capability_violation_is_rejected(self) -> None:
        source = EXAMPLE.read_text(encoding="utf-8").replace(
            "let result = execute monitor.check_disk()",
            "let result = execute unknown.check_disk()",
        )
        with self.assertRaises(CheckError):
            check(parse(source))

    def test_two_steps_run_in_order_and_replay_last_result(self) -> None:
        source = EXAMPLE.read_text(encoding="utf-8").replace(
            "  step inspect {",
            "  step first {\n"
            "    let result = execute monitor.check_disk()\n"
            "    return result\n"
            "  }\n"
            "  step inspect {",
        )
        program = parse(source)
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "run.jsonl"
            result = run(program, "main", trace)
            events = [
                json.loads(line)
                for line in trace.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                [event["step"] for event in events if event["event"] == "step_start"],
                ["first", "inspect"],
            )
            self.assertEqual(replay(trace), result)

    def test_duplicate_steps_are_rejected(self) -> None:
        source = EXAMPLE.read_text(encoding="utf-8").replace(
            "  step inspect {",
            "  step inspect {\n"
            "    let result = execute monitor.check_disk()\n"
            "    return result\n"
            "  }\n"
            "  step inspect {",
        )
        with self.assertRaisesRegex(CheckError, "duplicate step names"):
            check(parse(source))

    def test_execute_access_requires_runtime_approval(self) -> None:
        source = (
            EXAMPLE.read_text(encoding="utf-8")
            .replace("access: observe", "access: execute")
            .replace("  step inspect {", "  require approval\n  step inspect {")
        )
        program = parse(source)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(PermissionError, "approval record"):
                run(program, "main", Path(directory) / "run.jsonl")

    def test_late_execute_step_prevents_earlier_tool_call(self) -> None:
        source = (
            EXAMPLE.read_text(encoding="utf-8")
            .replace(
                "  step inspect {",
                "  step first {\n"
                "    let result = execute monitor.check_disk()\n"
                "    return result\n"
                "  }\n"
                "  step inspect {",
            )
            .replace("  step inspect {", "  require approval\n  step inspect {")
        )
        source = (
            source.replace(
                "agent monitor {",
                "tool mutate {\n"
                '  source mcp "local_mock"\n'
                '  operation: "get_disk_info"\n'
                "  access: execute\n"
                "  output: DiskStatus\n"
                "}\n\n"
                "agent monitor {",
            )
            .replace("can: [check_disk]", "can: [check_disk, mutate]")
            .replace(
                "execute monitor.check_disk()\n    return result\n  }\n}",
                "execute monitor.mutate()\n    return result\n  }\n}",
            )
        )
        program = parse(source)
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "run.jsonl"
            with patch("kosika.runtime._call_declared_tool") as call_tool:
                with self.assertRaises(PermissionError):
                    run(program, "main", trace)
                call_tool.assert_not_called()
            self.assertEqual(json.loads(trace.read_text().splitlines()[-1])["event"], "run_failed")
            with self.assertRaises(ValueError):
                replay(trace)

    def test_workflow_output_must_match_final_tool(self) -> None:
        source = EXAMPLE.read_text(encoding="utf-8").replace(
            "workflow main() -> DiskStatus",
            "record Other { value: Text }\nworkflow main() -> Other",
        )
        with self.assertRaisesRegex(CheckError, "final tool returns"):
            check(parse(source))

    def test_mock_tool_output_is_validated(self) -> None:
        source = EXAMPLE.read_text(encoding="utf-8").replace(
            "used_percent: Number", "used_percent: Text"
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SchemaValidationError):
                run(parse(source), "main", Path(directory) / "run.jsonl")

    def test_run_writes_trace_and_replays(self) -> None:
        program = parse(EXAMPLE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "run.jsonl"
            result = run(program, "main", trace)
            self.assertEqual(result, {"mount": "/", "used_percent": 42.0})
            self.assertEqual(replay(trace), result)
            self.assertGreaterEqual(
                len(trace.read_text(encoding="utf-8").splitlines()), 5
            )


if __name__ == "__main__":
    unittest.main()
