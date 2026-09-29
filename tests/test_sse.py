from __future__ import annotations

import copy
import http.client
import tempfile
import time
import unittest
from pathlib import Path

from kosika.sse import (
    EventStream,
    EventStreamError,
    ReplayGapError,
    SubscriberLimitError,
    format_sse,
)
from kosika.synapse_bridge import normalize_event
from kosika.workbench import WorkbenchServer, dispatch
from kosika.workbench_samples import SAMPLES


def event(sequence: int) -> dict:
    return normalize_event(
        {
            "event": "step_start",
            "timestamp": f"2026-09-23T00:00:{sequence:02d}+00:00",
            "workflow": "main",
            "step": f"step_{sequence}",
        },
        sequence,
    )


def bulk_event(sequence: int) -> dict:
    """Like ``event`` but valid for arbitrary sequence numbers."""

    return normalize_event(
        {
            "event": "step_start",
            "timestamp": "2026-09-23T00:00:00+00:00",
            "workflow": "main",
            "step": f"step_{sequence}",
        },
        sequence,
    )


class EventStreamTests(unittest.TestCase):
    def test_sequence_and_event_id_reconnect_are_bounded(self) -> None:
        stream = EventStream(buffer_size=2, max_replay=2)
        stream.publish([event(1), event(2), event(3)])

        self.assertEqual([item["sequence"] for item in stream.read_after()], [2, 3])
        self.assertEqual([item["sequence"] for item in stream.read_after(2)], [3])
        self.assertEqual(
            [item["sequence"] for item in stream.read_after(event(2)["event_id"])],
            [3],
        )
        with self.assertRaises(ReplayGapError):
            stream.read_after(0)
        with self.assertRaises(EventStreamError):
            stream.read_after(2, limit=3)

    def test_publish_rejects_gaps_and_invalid_events(self) -> None:
        stream = EventStream()
        stream.publish([event(1)])
        with self.assertRaises(EventStreamError):
            stream.publish([event(3)])
        broken = event(2)
        broken["status"] = "not-a-status"
        with self.assertRaises(EventStreamError):
            stream.publish([broken])

    def test_jsonl_source_round_trips_and_wait_can_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "normalized.jsonl"
            stream = EventStream(jsonl_path=path, buffer_size=4)
            stream.publish([event(1), event(2)])
            restored = EventStream(jsonl_path=path, buffer_size=4)
            self.assertEqual(
                [item["sequence"] for item in restored.read_after(1)], [2]
            )
            self.assertEqual(restored.wait_for(2, timeout=0), ())

    def test_subscriber_limits_and_cleanup_are_bounded(self) -> None:
        stream = EventStream(max_subscribers=1)
        stream.acquire_subscriber()
        self.assertEqual(stream.subscriber_count, 1)
        with self.assertRaises(SubscriberLimitError):
            stream.acquire_subscriber()
        stream.release_subscriber()
        stream.release_subscriber()
        self.assertEqual(stream.subscriber_count, 0)
        stream.close()
        with self.assertRaises(EventStreamError):
            stream.acquire_subscriber()

    def test_sse_frame_has_event_id_type_and_json_data(self) -> None:
        encoded = format_sse(event(1)).decode("utf-8")
        self.assertIn("id: kosika-0001-", encoded)
        self.assertIn("event: kosika_step_start", encoded)
        self.assertIn('"sequence":1', encoded)
        self.assertTrue(encoded.endswith("\n\n"))

    def test_workbench_run_publishes_normalized_events(self) -> None:
        stream = EventStream(buffer_size=32, max_replay=32)
        sample = copy.deepcopy(SAMPLES[0])
        result = dispatch(
            "run",
            {
                key: sample[key]
                for key in ("files", "entry", "workflow", "inputs", "fixtures")
            },
            event_stream=stream,
        )

        self.assertEqual(result["mode"], "fixture")
        normalized = stream.read_after(limit=32)
        self.assertTrue(normalized)
        self.assertTrue(all(item["schema"] == "synapse.kosika.event/v1" for item in normalized))
        self.assertEqual(
            [item["sequence"] for item in normalized],
            list(range(1, len(normalized) + 1)),
        )
        self.assertIn("kosika_workflow_start", [item["type"] for item in normalized])

    def test_workbench_run_appends_after_check_event_with_unique_ids(self) -> None:
        stream = EventStream(buffer_size=64, max_replay=64)
        stream.publish([normalize_event({"event": "step_start"}, 1)])
        sample = copy.deepcopy(SAMPLES[0])
        dispatch(
            "run",
            {
                key: sample[key]
                for key in ("files", "entry", "workflow", "inputs", "fixtures")
            },
            event_stream=stream,
        )
        normalized = stream.read_after(limit=64)
        types = [item["type"] for item in normalized]
        self.assertEqual(types[0], "kosika_step_start")
        self.assertEqual(types[1], "kosika_check")
        self.assertIn("kosika_workflow_start", types)
        ids = [item["event_id"] for item in normalized]
        self.assertEqual(len(ids), len(set(ids)))
        sequences = [item["sequence"] for item in normalized]
        self.assertEqual(sequences, list(range(1, len(sequences) + 1)))

    def test_workbench_check_failure_publishes_failure_events(self) -> None:
        stream = EventStream(buffer_size=16, max_replay=16)
        sample = copy.deepcopy(SAMPLES[0])
        files = {"main.kos": sample["files"]["main.kos"] + "\nrecord Broken {"}
        with self.assertRaises(ValueError):
            dispatch(
                "check",
                {
                    "files": files,
                    "entry": sample["entry"],
                    "workflow": sample["workflow"],
                },
                event_stream=stream,
            )
        normalized = stream.read_after(limit=16)
        self.assertEqual(
            [item["type"] for item in normalized],
            ["kosika_error"],
        )
        self.assertEqual(normalized[0]["status"], "error")
        self.assertIn("error_type", normalized[0]["payload"])

    def test_workbench_timeline_action_returns_stages_and_renderings(self) -> None:
        raw_events = [
            {"event": "run_start", "workflow": "main"},
            {"event": "step_start", "workflow": "main", "step": "only"},
            {"event": "step_complete", "workflow": "main", "step": "only"},
            {"event": "run_complete", "workflow": "main"},
        ]
        result = dispatch("timeline", {"events": raw_events})
        self.assertEqual(
            result["design"]["stages"],
            ["execution", "execution", "execution", "execution"],
        )
        self.assertIn("flowchart LR", result["mermaid"])
        self.assertIn("<svg", result["svg"])

    def test_workbench_timeline_action_rejects_malformed_input(self) -> None:
        with self.assertRaises(ValueError):
            dispatch("timeline", {"events": "not-an-array"})
        with self.assertRaises(ValueError):
            dispatch("timeline", {"events": [{"event": "run_start"}, 7]})
        with self.assertRaises(ValueError):
            dispatch(
                "timeline",
                {"events": [{"event": "run_start", "extra": {}} for _ in range(513)]},
            )


class WorkbenchSSETests(unittest.TestCase):
    def setUp(self) -> None:
        self.stream = EventStream(buffer_size=2, max_replay=2)
        self.server = WorkbenchServer(0, event_stream=self.stream)
        self.thread = __import__("threading").Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, path: str, headers: dict[str, str] | None = None):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=3
        )
        connection.request("GET", path, headers=headers or {})
        return connection, connection.getresponse()

    def test_sse_requires_token_and_replays_from_last_event_id(self) -> None:
        connection, response = self.request("/api/events")
        try:
            self.assertEqual(response.status, 403)
            response.read()
        finally:
            connection.close()

        self.stream.publish([event(1), event(2)])
        connection, response = self.request(
            "/api/events?limit=1",
            {"X-Kosika-Token": self.server.token},
        )
        try:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Content-Type"), "text/event-stream; charset=utf-8")
            frame = "".join(response.fp.readline().decode("utf-8") for _ in range(4))
            self.assertIn("id: kosika-0002-", frame)
        finally:
            response.close()
            connection.close()

        deadline = time.monotonic() + 2
        while self.stream.subscriber_count and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.stream.subscriber_count, 0)

        connection, response = self.request(
            "/api/events?limit=1",
            {
                "X-Kosika-Token": self.server.token,
                "Last-Event-ID": event(1)["event_id"],
            },
        )
        try:
            self.assertEqual(response.status, 200)
            frame = "".join(response.fp.readline().decode("utf-8") for _ in range(4))
            self.assertIn("id: kosika-0002-", frame)
        finally:
            connection.close()

    def test_sse_returns_conflict_when_cursor_falls_out_of_buffer(self) -> None:
        self.stream.publish([event(1), event(2), event(3)])
        connection, response = self.request(
            "/api/events?after=0",
            {"X-Kosika-Token": self.server.token},
        )
        try:
            self.assertEqual(response.status, 409)
            self.assertIn(b"oldest", response.read())
        finally:
            connection.close()

    def test_sse_closes_cleanly_when_buffer_evicts_cursor(self) -> None:
        self.stream.publish([event(1), event(2)])
        connection, response = self.request(
            "/api/events?after=1&limit=2",
            {"X-Kosika-Token": self.server.token},
        )
        try:
            self.assertEqual(response.status, 200)
            frame = "".join(response.fp.readline().decode("utf-8") for _ in range(4))
            self.assertIn("id: kosika-0002-", frame)
            # Evict every retained event so the held cursor falls out of the
            # buffer; the handler must end the stream instead of raising.
            self.stream.publish([event(3), event(4)])
            deadline = time.monotonic() + 3
            while self.stream.subscriber_count and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(self.stream.subscriber_count, 0)
        finally:
            response.close()
            connection.close()

    def test_sse_closes_when_stream_shuts_down_mid_connection(self) -> None:
        self.stream.publish([event(1)])
        connection, response = self.request(
            "/api/events?after=1",
            {"X-Kosika-Token": self.server.token},
        )
        try:
            self.assertEqual(response.status, 200)
            deadline = time.monotonic() + 3
            while self.stream.subscriber_count == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(self.stream.subscriber_count, 1)
            self.stream.close()
            deadline = time.monotonic() + 3
            while self.stream.subscriber_count and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(self.stream.subscriber_count, 0)
        finally:
            response.close()
            connection.close()

    def test_concurrent_publishers_and_subscribers_stay_contiguous(self) -> None:
        import threading

        # The stream contract is one writer (the workbench) and many readers.
        # max_replay covers the whole run so late first reads still catch up.
        stream = EventStream(buffer_size=256, max_replay=128, max_subscribers=4)
        per_batch, batches = 10, 8
        total = per_batch * batches
        start = threading.Barrier(4)

        def publish_all() -> None:
            start.wait(timeout=5)
            # Publish only once every subscriber provably holds a slot, so
            # attachment precedes the first event and nobody can miss one.
            deadline = time.monotonic() + 5
            while stream.subscriber_count < 3 and time.monotonic() < deadline:
                time.sleep(0.005)
            for batch_index in range(batches):
                stream.publish(
                    [
                        bulk_event(batch_index * per_batch + i + 1)
                        for i in range(per_batch)
                    ]
                )

        def subscribe() -> list[int]:
            stream.acquire_subscriber()
            start.wait(timeout=5)
            seen: list[int] = []
            cursor: int | str = 0
            deadline = time.monotonic() + 10
            while len(seen) < total and time.monotonic() < deadline:
                batch = stream.wait_for(cursor, timeout=0.05, limit=128)
                for item in batch:
                    seen.append(item["sequence"])
                    cursor = item["event_id"]
            stream.release_subscriber()
            return seen

        results: list[list[int] | None] = [None, None, None]

        def run_subscriber(index: int) -> None:
            results[index] = subscribe()

        threads = [threading.Thread(target=publish_all)] + [
            threading.Thread(target=run_subscriber, args=(index,))
            for index in range(3)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        for seen in results:
            self.assertIsNotNone(seen)
            self.assertEqual(seen, list(range(1, total + 1)))
        ordered = sorted(
            stream.read_after(limit=64), key=lambda item: item["sequence"]
        )
        self.assertEqual(
            [item["sequence"] for item in ordered],
            list(range(total - 63, total + 1)),
        )

    def test_slow_consumer_on_tiny_buffer_never_receives_gapped_batches(self) -> None:
        import threading

        stream = EventStream(buffer_size=2, max_replay=2)
        stream.publish([bulk_event(1)])
        cursor: int | str = bulk_event(1)["event_id"]
        drained: list[int] = []
        fell_behind = threading.Event()
        done = threading.Event()

        def publish_burst() -> None:
            for sequence in range(2, 42):
                stream.publish([bulk_event(sequence)])
            done.set()

        publisher = threading.Thread(target=publish_burst)
        publisher.start()
        while not done.is_set() and not fell_behind.is_set():
            try:
                batch = stream.read_after(cursor, limit=2)
            except ReplayGapError:
                fell_behind.set()
                break
            if not batch:
                time.sleep(0.001)
                continue
            for item in batch:
                drained.append(item["sequence"])
                cursor = item["event_id"]
        publisher.join(timeout=5)

        # One deterministic classification read once the publisher has
        # finished: either the remaining tail is still readable, or the
        # bounded buffer correctly reports a replay gap.
        if not fell_behind.is_set():
            try:
                final_batch = stream.read_after(cursor, limit=2)
            except ReplayGapError:
                fell_behind.set()
            else:
                for item in final_batch:
                    drained.append(item["sequence"])
                    cursor = item["event_id"]

        self.assertTrue(done.is_set())
        for before, after in zip(drained, drained[1:]):
            self.assertEqual(after, before + 1)
        self.assertEqual(len(drained), len(set(drained)))
        self.assertTrue(fell_behind.is_set() or len(drained) == 41)


class BulkSSEHelpers(unittest.TestCase):
    def test_bulk_events_validate_up_to_large_sequences(self) -> None:
        for sequence in (1, 59, 60, 61, 120, 3601):
            item = bulk_event(sequence)
            self.assertEqual(item["sequence"], sequence)


if __name__ == "__main__":
    unittest.main()
