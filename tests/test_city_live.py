"""Tests for the live, event-driven city view and its motorway network."""

from __future__ import annotations

import json
import re
import threading
import time
import unittest
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from kosika.agent import AgentLoopError
from kosika.city import build_city
from kosika.city_live import _drive, _handler, capture_trace, render_city_live
from kosika.durable import ExecutionSession
from kosika.execution import ExecutionLimits
from kosika.metropolis import render_city_isometric
from kosika.model import ModelResponse, ModelToolCall
from kosika.parser import parse
from kosika.runtime import run
from kosika.synapse_bridge import normalize_event, normalize_events

ROOT = Path(__file__).parents[1]
SWARM = ROOT / "examples" / "swarm_review.kos"

PROGRAM_OBSERVE = """
record Note { text: Text }
tool read_notes {
  source mcp "local_mock"
  operation: "read"
  access: observe
  output: Note
}
agent archivist {
  can: [read_notes]
  cannot: [write_files]
  max_steps: 3
}
workflow main() -> Note {
  step gather {
    let found: Note = execute archivist.read_notes()
    return found
  }
}
"""

PROGRAM_EXECUTE = """
record Draft { text: Text }
tool publish {
  source mcp "local_mock"
  operation: "write"
  access: execute
  output: Draft
}
agent writer {
  can: [publish]
  max_steps: 2
}
workflow main() -> Draft {
  step write {
    let made: Draft = execute writer.publish()
    return made
  }
}
"""


PROGRAM_PARAM = """
record Note { text: Text }
record Report { text: Text }
tool read_notes {
  source mcp "local_mock"
  operation: "read"
  access: observe
  output: Note
}
tool write_notes {
  source mcp "local_mock"
  operation: "write"
  access: observe
  output: Report
}
agent archivist {
  can: [read_notes, write_notes]
  max_steps: 3
}
workflow main(note: Note) -> Report {
  step first {
    let found: Note = execute archivist.read_notes()
    return found
  }
  step second {
    let made: Report = execute archivist.write_notes()
    return made
  }
}
"""

PROGRAM_ASK_EXECUTE = """
record Draft { text: Text }
model judge { input: Draft output: Draft }
tool publish {
  source mcp "local_mock"
  operation: "write"
  access: execute
  output: Draft
}
agent writer {
  model: judge
  can: [publish]
  max_steps: 2
}
workflow main() -> Draft {
  step consult {
    let advice: Draft = ask writer(text: "review")
    return advice
  }
}
"""


PROGRAM_MODEL = """
record Draft { text: Text }
model writer_model { input: Draft output: Draft }
agent editor {
  model: writer_model
  max_steps: 3
}
workflow main() -> Draft {
  step rewrite {
    let revised: Draft = ask editor(text: "tighten")
    return revised
  }
}
"""


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class LivePageTests(unittest.TestCase):
    def city(self):
        return build_city(ROOT)

    def test_page_is_deterministic(self) -> None:
        city = self.city()
        self.assertEqual(render_city_live(city), render_city_live(city))

    def test_page_binds_to_the_event_stream(self) -> None:
        page = render_city_live(self.city())
        self.assertIn("/api/events", page)
        self.assertIn("/api/session", page)
        self.assertIn("X-Kosika-Token", page)

    def test_page_embeds_a_valid_city_drawing(self) -> None:
        page = render_city_live(self.city())
        drawing = re.search(r'<div id="world">(.*?)</div>', page, re.S)
        assert drawing is not None
        ET.fromstring(drawing.group(1))

    def test_lowpoly_page_embeds_three_scene_and_svg_fallback(self) -> None:
        page = render_city_live(self.city(), render_style="isometric_lowpoly")
        self.assertIn('id="three-world"', page)
        self.assertIn("three.module.js", page)
        self.assertIn('id="svg-fallback"', page)
        block = re.search(r'id="scene-data">(.*?)</script>', page, re.S)
        assert block is not None
        scene = json.loads(block.group(1))
        self.assertGreaterEqual(len(scene["buildings"]), 1)
        self.assertEqual(len(scene["agents"]), len(self.city().residents))

    def test_page_roster_addresses_every_agent(self) -> None:
        city = self.city()
        page = render_city_live(city)
        block = re.search(r'id="roster">(.*?)</script>', page, re.S)
        assert block is not None
        roster = json.loads(block.group(1))
        self.assertEqual({person.name for person in city.residents}, set(roster))
        for district in roster.values():
            self.assertTrue(district)

    def test_roster_cannot_break_out_of_the_script_tag(self) -> None:
        page = render_city_live(self.city())
        block = re.search(r'id="roster">(.*?)</script>', page, re.S)
        assert block is not None
        self.assertNotIn("<", block.group(1))

    def test_page_reports_live_counts(self) -> None:
        city = self.city()
        page = render_city_live(city)
        self.assertIn(f"{len(city.residents)} agents", page)
        self.assertIn(f"{len(city.districts)} districts", page)

    def test_page_exposes_selected_environment(self) -> None:
        page = render_city_live(self.city(), environment="forest")
        self.assertIn('data-environment="forest"', page)
        self.assertIn("live forest world", page)


class MotorwayTests(unittest.TestCase):
    def roads(self) -> dict[str, int]:
        drawing = render_city_isometric(build_city(ROOT))
        return {
            kind: len(re.findall('data-road="%s"' % kind, drawing))
            for kind in ("ring", "corridor", "spur")
        }

    def test_motorway_network_is_drawn(self) -> None:
        roads = self.roads()
        self.assertEqual(roads["ring"], 4)
        self.assertGreaterEqual(roads["corridor"], 1)
        self.assertGreaterEqual(roads["spur"], 1)

    def test_every_district_is_linked_to_the_network(self) -> None:
        city = build_city(ROOT)
        drawing = render_city_isometric(city)
        spurs = len(re.findall('data-road="spur"', drawing))
        self.assertGreaterEqual(spurs, len(city.districts))

    def test_roads_render_under_the_districts(self) -> None:
        drawing = render_city_isometric(build_city(ROOT))
        self.assertLess(drawing.index('data-road="ring"'), drawing.index("data-grand="))


class EventIdentityTests(unittest.TestCase):
    def test_agent_identity_survives_normalization(self) -> None:
        event = normalize_event(
            {"event": "tool_call", "agent": "researcher", "tool": "read_docs"}, 1
        )
        self.assertEqual(event["payload"]["agent"], "researcher")
        self.assertEqual(event["payload"]["tool"], "read_docs")

    def test_identity_is_optional(self) -> None:
        event = normalize_event({"event": "step_start", "step": "gather"}, 1)
        self.assertNotIn("agent", event.get("payload", {}))
        self.assertNotIn("tool", event.get("payload", {}))

    def test_identity_is_sanitised(self) -> None:
        event = normalize_event(
            {"event": "tool_call", "agent": "a b<script>", "tool": "read"}, 1
        )
        self.assertEqual(event["payload"]["agent"], "a_b_script_")

    def test_non_string_identity_is_ignored(self) -> None:
        event = normalize_event({"event": "tool_call", "agent": 7, "tool": None}, 1)
        self.assertNotIn("agent", event.get("payload", {}))
        self.assertNotIn("tool", event.get("payload", {}))

    def test_sequences_stay_contiguous_across_batches(self) -> None:
        first = normalize_events(
            [{"event": "step_start", "step": "a"}], sequence_start=1
        )
        second = normalize_events(
            [{"event": "step_start", "step": "b"}], sequence_start=len(first) + 1
        )
        self.assertEqual([item["sequence"] for item in (*first, *second)], [1, 2])


class DriveTests(unittest.TestCase):
    def collect(self, root: Path) -> tuple[list[dict], set[str], set[str]]:
        from kosika.synapse_bridge import normalize_events

        seen: list[dict] = []
        lit: set[str] = set()
        held: set[str] = set()
        state = {"sequence": 1}

        def publish(raw: list[dict]) -> None:
            batch = normalize_events(raw, sequence_start=state["sequence"])
            state["sequence"] += len(batch)
            seen.extend(batch)
            for item in batch:
                agent = (item.get("payload") or {}).get("agent")
                if not agent:
                    continue
                if "approval_pending" in item["type"]:
                    held.add(agent)
                else:
                    lit.add(agent)

        _drive(root, publish, threading.Event())
        return seen, lit, held

    def test_drive_runs_real_tools_for_declared_agents(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "observe.kos").write_text(PROGRAM_OBSERVE, encoding="utf-8")
            (root / "execute.kos").write_text(PROGRAM_EXECUTE, encoding="utf-8")
            seen, lit, held = self.collect(root)

        self.assertIn("archivist", lit)
        tool_calls = [
            item
            for item in seen
            if item["type"] == "kosika_tool_call"
            and item["payload"].get("agent") == "archivist"
        ]
        self.assertTrue(tool_calls)
        self.assertEqual(tool_calls[0]["payload"]["tool"], "read_notes")

    def test_execute_tools_are_gated_not_forced(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "execute.kos").write_text(PROGRAM_EXECUTE, encoding="utf-8")
            seen, lit, held = self.collect(root)

        self.assertIn("writer", held)
        self.assertNotIn("writer", lit)
        pending = [item for item in seen if item["type"] == "kosika_approval_pending"]
        self.assertTrue(pending)
        self.assertEqual(pending[0]["payload"]["agent"], "writer")

    def test_model_only_agents_are_driven_through_their_model(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "model.kos").write_text(PROGRAM_MODEL, encoding="utf-8")
            seen, lit, _ = self.collect(root)

        self.assertIn("editor", lit)
        agent_calls = [
            item
            for item in seen
            if item["type"] == "kosika_agent_call"
            and item["payload"].get("agent") == "editor"
        ]
        self.assertTrue(agent_calls)

    def test_drive_stops_promptly(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "observe.kos").write_text(PROGRAM_OBSERVE, encoding="utf-8")
            stop = threading.Event()
            stop.set()
            started = time.perf_counter()
            _drive(root, lambda raw_events: None, stop)
            self.assertLess(time.perf_counter() - started, 5.0)

    def test_every_agent_in_the_city_reaches_the_stream(self) -> None:
        city = build_city(ROOT)
        seen, lit, held = self.collect(ROOT)
        del seen
        self.assertEqual({person.name for person in city.residents}, lit | held)


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        from kosika.sse import EventStream

        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        (root / "observe.kos").write_text(PROGRAM_OBSERVE, encoding="utf-8")
        self.root = root
        self.stream = EventStream(buffer_size=4096, max_replay=4096)
        self.token = "unit-test-token"
        page = render_city_live(build_city(root))
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", _free_port()), _handler(page, self.stream, self.token)
        )
        self.server.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)

    def _stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def get(self, path: str, token: str | None = None) -> tuple[int, bytes]:
        request = urllib.request.Request(self.base + path)
        if token is not None:
            request.add_header("X-Kosika-Token", token)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_serves_the_city_page(self) -> None:
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"data-agent=", body)
        self.assertIn(b"/api/events", body)

    def test_session_issues_a_token(self) -> None:
        status, body = self.get("/api/session")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["token"], self.token)

    def test_stream_requires_the_token(self) -> None:
        status, _ = self.get("/api/events")
        self.assertEqual(status, 403)

    def test_unknown_route_is_not_found(self) -> None:
        status, _ = self.get("/nope")
        self.assertEqual(status, 404)

    def test_stream_delivers_published_events(self) -> None:
        response = urllib.request.Request(self.base + "/api/events")
        response.add_header("X-Kosika-Token", self.token)
        events = self.stream
        events.acquire_subscriber()
        try:
            published = normalize_events(
                [{"event": "tool_call", "agent": "archivist", "tool": "read_notes"}],
                sequence_start=1,
            )
        finally:
            events.release_subscriber()
        with urllib.request.urlopen(response, timeout=10) as stream:
            assert events.publish(list(published))
            line = stream.readline()
        payload = json.loads(line[5:].decode("utf-8").strip())
        self.assertEqual(payload["payload"]["agent"], "archivist")


class SwarmExampleTests(unittest.TestCase):
    def test_shipped_example_drives_its_own_agents(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "swarm_review.kos").write_text(
                SWARM.read_text(encoding="utf-8"), encoding="utf-8"
            )
            events, lit, _ = DriveTests("collect").collect(root)
        self.assertIn("researcher", lit)


class TraceCaptureTests(unittest.TestCase):
    """The city must be auditable on disk, not only live in a browser."""

    def capture(self, source: str) -> tuple[dict, dict[str, str]]:
        """Capture, then snapshot the files while the temp tree still exists."""

        with TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "program.kos").write_text(source, encoding="utf-8")
            out = root / "traces"
            report = capture_trace(root, out)
            written = {
                path.name: path.read_text(encoding="utf-8")
                for path in sorted(out.glob("*.jsonl"))
            }
        return report, written

    @staticmethod
    def strip_volatile(events: list[dict]) -> list[dict]:
        """Drop wall-clock fields so determinism means *work*, not byte identity.

        A trace is an execution record, so it legitimately carries a real
        timestamp and a real elapsed time. Those differ every run; the work
        and the results must not.
        """

        def clean(value):
            if isinstance(value, dict):
                return {
                    key: clean(item)
                    for key, item in value.items()
                    if key not in {"timestamp", "elapsed_seconds"}
                }
            if isinstance(value, list):
                return [clean(item) for item in value]
            return value

        return [clean(event) for event in events]

    @staticmethod
    def drive_for(report: dict, tool: str) -> dict:
        matches = [entry for entry in report["entries"] if entry["tool"] == tool]
        assert len(matches) == 1, f"expected one drive for {tool}, got {matches}"
        return matches[0]

    def test_observed_tool_run_is_written_and_replays(self) -> None:
        report, written = self.capture(PROGRAM_OBSERVE)
        entry = self.drive_for(report, "read_notes")
        self.assertTrue(entry["replayable"], entry.get("replay_error"))
        self.assertIn(f"{entry['workflow']}.jsonl", written)
        events = [
            json.loads(line)
            for line in written[f"{entry['workflow']}.jsonl"].splitlines()
        ]
        self.assertEqual(events[0]["event"], "run_start")
        self.assertEqual(events[-1]["event"], "run_complete")
        self.assertEqual(
            [event["event"] for event in events],
            [
                "run_start",
                "step_start",
                "tool_call",
                "tool_return",
                "step_complete",
                "run_complete",
            ],
        )

    def test_replayed_result_identifies_the_tool(self) -> None:
        report, _ = self.capture(PROGRAM_OBSERVE)
        entry = self.drive_for(report, "read_notes")
        self.assertIn("read_notes", json.dumps(entry["replayed"]))

    def test_approval_gated_run_is_recorded_not_faked(self) -> None:
        report, _ = self.capture(PROGRAM_EXECUTE)
        entry = self.drive_for(report, "publish")
        self.assertTrue(entry["approval_pending"])
        self.assertFalse(entry["replayable"])
        self.assertIn("completed run", entry["replay_error"])

    def test_trace_names_the_resident_and_tool(self) -> None:
        report, _ = self.capture(PROGRAM_OBSERVE)
        entry = self.drive_for(report, "read_notes")
        self.assertEqual(entry["agent"], "archivist")
        self.assertEqual(entry["tool"], "read_notes")

    def test_capture_is_deterministic(self) -> None:
        left_report, left = self.capture(PROGRAM_OBSERVE)
        right_report, right = self.capture(PROGRAM_OBSERVE)
        self.assertEqual(
            [entry["replayed"] for entry in left_report["entries"]],
            [entry["replayed"] for entry in right_report["entries"]],
        )
        self.assertEqual(sorted(left), sorted(right))
        for name in sorted(left):
            with self.subTest(trace=name):
                self.assertEqual(
                    self.strip_volatile(
                        [json.loads(line) for line in left[name].splitlines()]
                    ),
                    self.strip_volatile(
                        [json.loads(line) for line in right[name].splitlines()]
                    ),
                )

    def test_shipped_city_runs_have_no_silent_failures(self) -> None:
        with TemporaryDirectory() as raw:
            out = Path(raw) / "traces"
            report = capture_trace(ROOT / "examples" / "city", out)
        self.assertGreater(report["runs"], 0)
        for entry in report["entries"]:
            with self.subTest(workflow=entry["workflow"]):
                self.assertTrue(
                    entry.get("replayable") or entry["approval_pending"],
                    f"{entry['workflow']} neither replayed nor held for approval: "
                    f"{entry.get('replay_error')}",
                )
        self.assertEqual(
            report["replayable"] + report["awaiting_approval"], report["runs"]
        )

    def test_parameterised_workflow_runs_with_host_inputs(self) -> None:
        report, written = self.capture(PROGRAM_PARAM)
        authored = [entry for entry in report["entries"] if entry["authored"]]
        self.assertEqual(len(authored), 1)
        entry = authored[0]
        self.assertEqual(entry["workflow"], "main")
        self.assertTrue(entry["replayable"], entry.get("replay_error"))
        events = [json.loads(line) for line in written["main.jsonl"].splitlines()]
        # The authored multi-step workflow ran end to end, in order.
        self.assertEqual(
            [event["event"] for event in events],
            [
                "run_start",
                "step_start",
                "tool_call",
                "tool_return",
                "step_complete",
                "step_start",
                "tool_call",
                "tool_return",
                "step_complete",
                "run_complete",
            ],
        )
        self.assertEqual(
            [event["step"] for event in events if event["event"] == "step_start"],
            ["first", "second"],
        )

    def test_ask_reaching_execute_tool_pauses_for_approval(self) -> None:
        """The gate this city fixture cannot reach: a model that asks for a tool.

        The city fixture returns canned model text with no tool calls, so the
        ask path never reaches an execute tool there. This drives ``run``
        directly with a provider that does request one, which is the case the
        ``ask`` gate exists for.
        """

        program = parse(PROGRAM_ASK_EXECUTE)
        calls = [ModelResponse(tool_calls=(ModelToolCall("0", "publish", {}),))]

        class _Provider:
            def complete(self, request: object) -> ModelResponse:
                return calls.pop(0) if calls else ModelResponse(content="")

        limits = ExecutionLimits(max_steps=8, max_tool_calls=8, deadline_seconds=10.0)
        with TemporaryDirectory() as raw:
            session = ExecutionSession(Path(raw) / "j.jsonl", binding_id="kosika-test")
            events: list[dict] = []
            # The agent loop raises its own approval error before the tool
            # callback runs; the runtime records that as run_paused.
            with self.assertRaises(AgentLoopError):
                run(
                    program,
                    "main",
                    None,
                    inputs={},
                    session=session,
                    model_bindings={"judge": _Provider()},
                    tool_bindings={"publish": lambda tool, args: {"text": "ok"}},
                    limits=limits,
                    _events_out=events,
                )
        kinds = [event.get("event") for event in events]
        self.assertEqual(kinds[0], "run_start")
        self.assertIn("agent_call", kinds)
        # It got as far as asking, then stopped for a human rather than
        # silently running an execute tool.
        self.assertEqual(kinds[-1], "run_paused")
        self.assertNotIn("tool_call", kinds)
        self.assertNotIn("run_complete", kinds)

    def test_ask_without_session_refuses_before_any_tool_call(self) -> None:
        program = parse(PROGRAM_ASK_EXECUTE)
        limits = ExecutionLimits(max_steps=8, max_tool_calls=8, deadline_seconds=10.0)
        with self.assertRaisesRegex(PermissionError, "verified approval record"):
            run(
                program,
                "main",
                None,
                inputs={},
                model_bindings={"judge": _Provider_stub()},
                tool_bindings={"publish": lambda tool, args: {"text": "ok"}},
                limits=limits,
            )

    def test_shipped_city_workflows_are_actually_executed(self) -> None:
        with TemporaryDirectory() as raw:
            report = capture_trace(ROOT / "examples" / "city", Path(raw) / "traces")
        authored = {
            entry["workflow"] for entry in report["entries"] if entry["authored"]
        }
        # Every department's own workflow now runs, not just generated drives.
        self.assertLessEqual(
            {"evaluate", "review_change", "teach", "narrate", "review", "run"},
            authored,
        )
        for entry in report["entries"]:
            if entry["authored"] and entry["workflow"] in {
                "evaluate",
                "review_change",
                "teach",
                "narrate",
                "review",
                "run",
            }:
                with self.subTest(workflow=entry["workflow"]):
                    self.assertTrue(entry["replayable"], entry.get("replay_error"))
                    self.assertTrue(
                        str(entry["source"]).endswith(".kos"),
                        "an authored run must name the .kos it came from",
                    )


class _Provider_stub:
    def complete(self, request: object) -> ModelResponse:
        return ModelResponse(content="")


if __name__ == "__main__":
    unittest.main()
