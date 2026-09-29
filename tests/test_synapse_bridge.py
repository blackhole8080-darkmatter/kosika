from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kosika.parser import parse
from kosika.synapse_bridge import (
    EVENT_SCHEMA,
    demo_run,
    execute,
    normalize_event,
    replay_bridge,
)


class SynapseBridgeTests(unittest.TestCase):
    def test_demo_emits_check_and_normalized_runtime_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = demo_run(Path(directory) / "demo.jsonl")

        self.assertTrue(result.ok)
        self.assertEqual(result.result, {"mount": "/", "used_percent": 42.0})
        event_types = [event["type"] for event in result.events]
        self.assertEqual(event_types[0], "kosika_check")
        self.assertIn("kosika_workflow_start", event_types)
        self.assertIn("kosika_tool_call", event_types)
        self.assertIn("kosika_tool_return", event_types)
        self.assertIn("kosika_workflow_complete", event_types)
        self.assertTrue(all(event["schema"] == EVENT_SCHEMA for event in result.events))
        self.assertEqual(
            [event["sequence"] for event in result.events],
            list(range(1, len(result.events) + 1)),
        )

    def test_bridge_does_not_forward_raw_values(self) -> None:
        raw = {
            "event": "tool_call",
            "timestamp": "2026-09-22T00:00:00+00:00",
            "workflow": "main",
            "step": "inspect",
            "tool": "secret_tool",
            "arguments": {"token": "do-not-forward", "path": "/private"},
        }
        event = normalize_event(raw, 7)
        encoded = json.dumps(event, sort_keys=True)

        self.assertEqual(event["type"], "kosika_tool_call")
        self.assertEqual(event["payload"]["arguments"]["type"], "object")
        self.assertNotIn("do-not-forward", encoded)
        self.assertNotIn("/private", encoded)
        self.assertNotIn("input_schema", encoded)

    def test_replay_is_offline_and_emits_replay_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "demo.jsonl"
            original = demo_run(trace)
            replayed = replay_bridge(trace)

        self.assertTrue(original.ok)
        self.assertTrue(replayed.ok)
        self.assertEqual(replayed.result, original.result)
        self.assertEqual(replayed.events[0]["type"], "kosika_replay")
        self.assertTrue(replayed.events[0]["replay"])
        self.assertTrue(all(event["replay"] for event in replayed.events))

    def test_failed_check_is_fail_closed(self) -> None:
        invalid = parse(
            """
            record Out { value: Text }
            workflow main() -> Out {
              step missing {
                let result = execute unknown.tool()
                return result
              }
            }
            """
        )
        result = execute(invalid, "main")

        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)
        self.assertEqual(result.events[0]["type"], "kosika_check")
        self.assertEqual(result.events[0]["status"], "failed")
        self.assertEqual(result.events[1]["type"], "kosika_error")

    def test_model_event_normalization_keeps_only_metadata(self) -> None:
        event = normalize_event(
            {
                "event": "model_call",
                "workflow": "answer",
                "step": "write",
                "model": "writer",
                "arguments": {"prompt": "private prompt"},
                "output_schema": {"type": "object"},
            },
            1,
        )

        self.assertEqual(event["type"], "kosika_model_call")
        self.assertEqual(event["model"], "writer")
        self.assertNotIn("private prompt", json.dumps(event))


if __name__ == "__main__":
    unittest.main()
