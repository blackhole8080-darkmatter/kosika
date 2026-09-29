from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from kosika.design import load_design_manifest, render_design
from kosika.event_stream_design import (
    DesignError as TimelineDesignError,
)
from kosika.event_stream_design import (
    build_timeline_design,
    timeline_stage_summary,
    timeline_svg_caption,
)
from kosika.synapse_bridge import normalize_events


def event(sequence: int) -> dict:
    return normalize_events(
        [
            {
                "event": "step_start",
                "timestamp": f"2026-09-24T00:00:{(sequence - 1) % 60:02d}+00:00",
                "workflow": "main",
                "step": f"step_{sequence}",
            }
        ],
        sequence_start=sequence,
    )[0]


class TimelineDesignTests(unittest.TestCase):
    def test_builds_deterministic_timeline_from_normalized_events(self) -> None:
        events = [event(1), event(2), event(3)]
        first = build_timeline_design(events, "tl-1", "Run timeline")
        second = build_timeline_design(events, "tl-1", "Run timeline")

        self.assertEqual(first.digest, second.digest)
        self.assertEqual(len(first.nodes), 3)
        self.assertEqual(len(first.edges), 2)
        self.assertEqual(first.design["kind"], "event_timeline")
        stages = timeline_stage_summary(first)["stages"]
        self.assertEqual(stages, ["execution", "execution", "execution"])

    def test_timeline_is_renderable_in_all_deterministic_formats(self) -> None:
        manifest = build_timeline_design([event(1), event(2)], "tl-2", "Run")
        for output_format in ("json", "text", "mermaid", "svg"):
            rendered = render_design(manifest, output_format)
            self.assertTrue(rendered.strip())
        self.assertIn("Timeline stage", timeline_svg_caption(manifest))

    def test_approval_and_failure_events_use_distinct_stages(self) -> None:
        started = event(1)
        started["type"] = "kosika_approval_pending"
        started["status"] = "pending"
        started["event_id"] = "kosika-0001-pending"
        failed = event(2)
        failed["type"] = "kosika_error"
        failed["status"] = "error"
        failed["event_id"] = "kosika-0002-error"
        manifest = build_timeline_design([started, failed], "tl-3", "Stages")
        stages = timeline_stage_summary(manifest)["stages"]
        self.assertEqual(stages, ["approval", "failure"])
        self.assertEqual(timeline_stage_summary(manifest)["first_stage"], "approval")

    def test_invalid_events_fail_closed(self) -> None:
        broken = event(1)
        broken["status"] = "not-a-status"
        with self.assertRaises(TimelineDesignError):
            build_timeline_design([broken], "tl-4", "Broken")

    def test_empty_and_oversized_streams_fail_closed(self) -> None:
        with self.assertRaises(TimelineDesignError):
            build_timeline_design([], "tl-5", "Empty")
        with self.assertRaises(TimelineDesignError):
            build_timeline_design(
                [event(1)], "tl-6", "Too long", max_nodes=0
            )

    def test_timestamps_pass_through_from_validated_events(self) -> None:
        # Validated events always carry a timezone-aware timestamp; the design
        # must preserve it verbatim rather than re-deriving wall-clock time.
        manifest = build_timeline_design([event(1), event(2)], "tl-8", "Timestamps")
        timestamps = [node["style"]["timestamp"] for node in manifest.nodes]
        self.assertEqual(
            timestamps,
            ["2026-09-24T00:00:00+00:00", "2026-09-24T00:00:01+00:00"],
        )

    def test_non_kosika_event_types_fail_closed(self) -> None:
        alien = event(1)
        alien["type"] = "not_kosika_prefix"
        alien["event_id"] = "kosika-0001-alien"
        with self.assertRaises(TimelineDesignError):
            build_timeline_design([alien], "tl-7", "Alien")


class BridgePublicationTests(unittest.TestCase):
    def test_check_event_passes_with_program_hash(self) -> None:
        from kosika.event_stream_design import publish_check_event
        from kosika.parser import parse

        program = parse('record R { text: Text }\n')
        event = publish_check_event(1, program, "main")
        self.assertEqual(event["type"], "kosika_check")
        self.assertEqual(event["status"], "passed")
        self.assertIn("program_hash", event["payload"])

    def test_check_failed_event_omits_hash_when_program_is_missing(self) -> None:
        from kosika.event_stream_design import publish_check_event

        error = ValueError("source has no declarations")
        event = publish_check_event(1, None, "main", error=error)
        self.assertEqual(event["type"], "kosika_check_failed")
        self.assertEqual(event["status"], "failed")
        self.assertNotIn("program_hash", event.get("payload", {}))
        self.assertEqual(event["payload"]["error_type"], "ValueError")
        self.assertNotIn("\n", event["payload"]["message"])

    def test_failure_event_is_bounded_and_validated(self) -> None:
        from kosika.event_stream_design import publish_failure_event

        event = publish_failure_event(2, "main", RuntimeError("boom"))
        self.assertEqual(event["type"], "kosika_error")
        self.assertEqual(event["sequence"], 2)
        self.assertEqual(event["workflow"], "main")
        self.assertEqual(event["payload"]["message"], "boom")

    def test_approval_event_uses_lifecycle_identity(self) -> None:
        from kosika.event_stream_design import publish_approval_event

        event = publish_approval_event(1)
        self.assertEqual(event["type"], "kosika_approval_pending")
        self.assertEqual(event["event_id"], "kosika-0001-kosika_approval_pending")

    def test_timeline_stage_maps_published_lifecycle_events(self) -> None:
        from kosika.event_stream_design import (
            build_timeline_design,
            publish_check_event,
            publish_failure_event,
            timeline_stage_summary,
        )

        manifest = build_timeline_design(
            [
                publish_check_event(1, None, "main"),
                publish_failure_event(2, "main", RuntimeError("no tools bound")),
            ],
            "tl-bridge",
            "Lifecycle",
        )
        stages = timeline_stage_summary(manifest)["stages"]
        self.assertEqual(stages, ["check", "failure"])


class CliApprovalEventTests(unittest.TestCase):
    def test_pause_publishes_normalized_stream_and_timeline(self) -> None:
        import contextlib
        import io
        from unittest.mock import patch

        from kosika import cli
        from kosika.events import read_event_jsonl

        source = """record R { text: Text }
tool save { source mcp "host" operation: "save" access: execute input: R output: R }
agent a { can: [save] }
workflow main(q: R) -> R { require approval
  step x { let r: R = execute a.save(text: q.text) return r }
}"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.kos").write_text(source)
            (root / "input.json").write_text('{"q":{"text":"review me"}}')
            event_trace = root / "events.jsonl"
            design = root / "timeline.design.json"
            args = [
                "kosika",
                "run",
                str(root / "main.kos"),
                "--input",
                str(root / "input.json"),
                "--journal",
                str(root / "journal.sqlite"),
                "--binding-id",
                "explicit-host",
                "--event-trace",
                str(event_trace),
                "--design",
                str(design),
            ]
            output = io.StringIO()
            with patch("sys.argv", args), contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(), 3)
            normalized = read_event_jsonl(event_trace)
            self.assertEqual(
                [item["type"] for item in normalized],
                ["kosika_approval_pending"],
            )
            manifest = load_design_manifest(design)
            stages = timeline_stage_summary(manifest)["stages"]
            self.assertEqual(stages, ["approval"])


class CliEventPublishingTests(unittest.TestCase):
    def test_publish_run_events_rejects_shared_trace_path(self) -> None:
        from kosika.cli import _publish_run_events

        with tempfile.TemporaryDirectory() as directory:
            args = type(
                "Args",
                (),
                {
                    "event_trace": Path(directory) / "events.jsonl",
                    "design": None,
                    "design_id": "event-timeline",
                    "design_name": "Kosika run timeline",
                },
            )()
            raw = Path(directory) / "run_trace.jsonl"
            raw.write_text(
                json.dumps({"event": "run_start"}) + "\n", encoding="utf-8"
            )
            first = _publish_run_events(args, raw)
            assert first is not None
            self.assertEqual(len(first), 1)
            args.event_trace = raw
            with self.assertRaises(ValueError):
                _publish_run_events(args, raw)

    def test_publish_run_events_normalizes_a_completed_trace(self) -> None:
        from kosika.cli import _publish_run_events

        with tempfile.TemporaryDirectory() as directory:
            args = type(
                "Args",
                (),
                {
                    "event_trace": Path(directory) / "events.jsonl",
                    "design": None,
                    "design_id": "event-timeline",
                    "design_name": "Kosika run timeline",
                },
            )()
            raw = Path(directory) / "run_trace.jsonl"
            raw.write_text(
                "\n".join(
                    json.dumps(item)
                    for item in [
                        {"event": "run_start", "workflow": "main"},
                        {"event": "run_complete", "workflow": "main", "result": 3},
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            normalized = _publish_run_events(args, raw)
            assert normalized is not None
            self.assertEqual(
                [item["type"] for item in normalized],
                ["kosika_workflow_start", "kosika_workflow_complete"],
            )
            persisted = json.loads(
                (Path(directory) / "events.jsonl").read_text(encoding="utf-8")
                .strip()
                .splitlines()[0]
            )
            self.assertEqual(
                persisted["schema"], "synapse.kosika.event/v1"
            )


class WorkbenchTimelineRoundTripTests(unittest.TestCase):
    def test_run_dispatch_events_feed_timeline_dispatch(self) -> None:
        from kosika.workbench import dispatch
        from kosika.workbench_samples import SAMPLES

        sample = SAMPLES[0]
        run = dispatch(
            "run",
            {
                key: copy.deepcopy(sample[key])
                for key in ("files", "entry", "workflow", "inputs", "fixtures")
            },
        )
        self.assertTrue(run["events"])
        self.assertLessEqual(len(run["events"]), 512)
        timeline = dispatch("timeline", {"events": run["events"]})
        self.assertEqual(timeline["design"]["kind"], "event_timeline")
        self.assertTrue(timeline["design"]["stages"])
        self.assertTrue(timeline["design"]["digest"])
        self.assertIn("<svg", timeline["svg"])
        self.assertIn("flowchart", timeline["mermaid"])
        again = dispatch("timeline", {"events": run["events"]})
        self.assertEqual(timeline["design"], again["design"])

    def test_timeline_dispatch_rejects_bad_event_payloads(self) -> None:
        from kosika.workbench import dispatch

        with self.assertRaises(ValueError):
            dispatch("timeline", {"events": "not-an-array"})
        with self.assertRaises(ValueError):
            dispatch("timeline", {"events": [42]})
        with self.assertRaises(ValueError):
            dispatch("timeline", {"events": [{} for _ in range(513)]})


if __name__ == "__main__":
    unittest.main()
