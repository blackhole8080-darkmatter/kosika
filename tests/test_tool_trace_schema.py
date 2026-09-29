from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kosika.parser import parse
from kosika.runtime import replay, run
from kosika.schema import SchemaValidationError

SOURCE = """
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
  step first {
    let initial = execute monitor.check_disk(mount: "/")
    return initial
  }
  step second {
    let final = execute monitor.check_disk(mount: initial.mount)
    return final
  }
}
"""


class ToolTraceSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.trace = Path(self.temporary.name) / "trace.jsonl"
        self.result = run(parse(SOURCE), "main", self.trace)

    def read_events(self) -> list[dict]:
        return [
            json.loads(line)
            for line in self.trace.read_text(encoding="utf-8").splitlines()
        ]

    def write_events(self, events: list[dict]) -> None:
        self.trace.write_text(
            "\n".join(json.dumps(event) for event in events) + "\n",
            encoding="utf-8",
        )

    def test_new_tool_trace_records_schemas_and_replays(self) -> None:
        events = self.read_events()
        calls = [event for event in events if event["event"] == "tool_call"]

        self.assertEqual(events[0]["trace_version"], 5)
        self.assertEqual(calls[0]["input_schema"]["required"], ["mount"])
        self.assertEqual(
            calls[0]["output_schema"]["required"], ["mount", "used_percent"]
        )
        self.assertEqual(replay(self.trace), self.result)

    def test_replay_rejects_invalid_tool_arguments(self) -> None:
        events = self.read_events()
        first_call = next(event for event in events if event["event"] == "tool_call")
        first_call["arguments"] = {"mount": 7}
        self.write_events(events)

        with self.assertRaises(SchemaValidationError):
            replay(self.trace)

    def test_replay_rejects_invalid_intermediate_tool_result(self) -> None:
        events = self.read_events()
        first_return = next(
            event for event in events if event["event"] == "tool_return"
        )
        first_complete = next(
            event for event in events if event["event"] == "step_complete"
        )
        invalid = {"mount": "/", "used_percent": "not-a-number"}
        first_return["result"] = invalid
        first_complete["result"] = invalid
        self.write_events(events)

        with self.assertRaises(SchemaValidationError):
            replay(self.trace)

    def test_current_tool_trace_requires_both_schemas(self) -> None:
        original = self.read_events()
        for missing in ("input_schema", "output_schema"):
            with self.subTest(missing=missing):
                events = [event.copy() for event in original]
                first_call = next(
                    event for event in events if event["event"] == "tool_call"
                )
                first_call.pop(missing)
                self.write_events(events)

                with self.assertRaisesRegex(
                    ValueError, "Tool trace requires input and output schemas"
                ):
                    replay(self.trace)

    def test_version_two_tool_trace_remains_replayable(self) -> None:
        events = self.read_events()
        events[0]["trace_version"] = 2
        for event in events:
            if event.get("event") == "tool_call":
                event.pop("input_schema")
                event.pop("output_schema")
        self.write_events(events)

        self.assertEqual(replay(self.trace), self.result)


if __name__ == "__main__":
    unittest.main()
