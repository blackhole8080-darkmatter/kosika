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

from kosika.city import build_city
from kosika.city_live import _drive, _handler, render_city_live
from kosika.metropolis import render_city_isometric
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
        self.assertLess(
            drawing.index('data-road="ring"'), drawing.index("data-grand=")
        )


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
        first = normalize_events([{"event": "step_start", "step": "a"}], sequence_start=1)
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
        pending = [
            item for item in seen if item["type"] == "kosika_approval_pending"
        ]
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


if __name__ == "__main__":
    unittest.main()
