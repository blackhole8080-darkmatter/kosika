from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kosika.events import (
    EVENT_BRIDGE_VERSION,
    EVENT_SCHEMA,
    EventValidationError,
    read_event_jsonl,
    validate_event,
    validate_events,
    write_event_jsonl,
)
from kosika.synapse_bridge import demo_run, normalize_event, replay_bridge


class EventEnvelopeTests(unittest.TestCase):
    def _event(self, sequence: int = 1, event_id: str | None = None) -> dict:
        return {
            "schema": EVENT_SCHEMA,
            "bridge_version": EVENT_BRIDGE_VERSION,
            "event_id": event_id or f"kosika-{sequence:04d}",
            "sequence": sequence,
            "timestamp": "2026-09-23T00:00:00+00:00",
            "bus": "synapse",
            "source": "kosika",
            "region": "kosika",
            "type": "kosika_workflow_start",
            "status": "started",
            "replay": False,
            "payload": {"arguments": {"type": "object", "size": 1}},
        }

    def test_valid_event_is_accepted_and_normalized(self) -> None:
        event = validate_event(self._event())

        self.assertEqual(event["schema"], EVENT_SCHEMA)
        self.assertEqual(event["sequence"], 1)
        self.assertEqual(event["payload"]["arguments"]["size"], 1)

    def test_missing_required_fields_are_rejected(self) -> None:
        for field in (
            "schema",
            "bridge_version",
            "event_id",
            "sequence",
            "timestamp",
            "bus",
            "source",
            "region",
            "type",
            "status",
            "replay",
        ):
            event = self._event()
            del event[field]
            with self.subTest(field=field):
                with self.assertRaises(EventValidationError):
                    validate_event(event)

    def test_invalid_schema_version_type_and_status_are_rejected(self) -> None:
        invalid_values = (
            ("schema", "other.schema/v1"),
            ("bridge_version", 2),
            ("type", "not valid"),
            ("status", "unknown"),
        )
        for field, value in invalid_values:
            event = self._event()
            event[field] = value
            with self.subTest(field=field):
                with self.assertRaises(EventValidationError):
                    validate_event(event)

    def test_sequence_gaps_and_duplicate_ids_are_rejected(self) -> None:
        with self.assertRaises(EventValidationError):
            validate_events([self._event(1), self._event(3)])
        with self.assertRaises(EventValidationError):
            validate_events([self._event(1, "same"), self._event(2, "same")])

    def test_payload_is_bounded(self) -> None:
        event = self._event()
        event["payload"] = {"message": "x" * 513}
        with self.assertRaises(EventValidationError):
            validate_event(event)

    def test_jsonl_rejects_duplicate_keys_non_finite_numbers_blank_and_malformed_lines(
        self,
    ) -> None:
        cases = (
            '{"schema": "x", "schema": "y"}\n',
            '{"value": NaN}\n',
            "\n",
            "not-json\n",
            "[]\n",
        )
        with tempfile.TemporaryDirectory() as directory:
            for index, content in enumerate(cases):
                path = Path(directory) / f"invalid-{index}.jsonl"
                path.write_text(content, encoding="utf-8")
                with self.subTest(case=index):
                    with self.assertRaises(EventValidationError):
                        read_event_jsonl(path)

    def test_jsonl_round_trip_is_strict_and_ordered(self) -> None:
        events = [self._event(1), self._event(2)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            written = write_event_jsonl(path, events)
            loaded = read_event_jsonl(path)
            encoded = path.read_text(encoding="utf-8")

        self.assertEqual(tuple(written), loaded)
        self.assertEqual([event["sequence"] for event in loaded], [1, 2])
        self.assertTrue(encoded.endswith("\n"))

    def test_normalized_replay_preserves_order_and_failure_status(self) -> None:
        raw = [
            {
                "event": "run_start",
                "timestamp": "2026-09-23T00:00:00+00:00",
                "workflow": "main",
            },
            {
                "event": "run_complete",
                "timestamp": "2026-09-23T00:00:01+00:00",
                "workflow": "main",
            },
        ]
        normalized = [
            normalize_event(item, index, replay=True)
            for index, item in enumerate(raw, 2)
        ]
        validated = validate_events(normalized, sequence_start=2)
        self.assertEqual([event["sequence"] for event in validated], [2, 3])
        self.assertTrue(all(event["replay"] for event in validated))

        failed = normalize_event(
            {"event": "check", "timestamp": "2026-09-23T00:00:00+00:00"},
            1,
        )
        failed["status"] = "failed"
        self.assertEqual(validate_event(failed)["status"], "failed")

    def test_raw_trace_replay_remains_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.jsonl"
            original = demo_run(path)
            replayed = replay_bridge(path)

        self.assertTrue(original.ok)
        self.assertTrue(replayed.ok)
        self.assertEqual(replayed.result, original.result)
        self.assertEqual(replayed.events[0]["type"], "kosika_replay")
        self.assertTrue(all(event["replay"] for event in replayed.events))


if __name__ == "__main__":
    unittest.main()
