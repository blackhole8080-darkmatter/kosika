"""One agent handing a request to another, which keeps its own rules."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from kosika.app import App
from kosika.checker import CheckError
from kosika.modules import load_program
from kosika.runtime import run
from kosika.system_tools import SYSTEM_KINDS, bind_agent_tools, bind_system_tools

HAND = """
record Handover { agent: Text text: Text }
record Handed { ok: Bool agent: Text text: Text }

tool hand_over {
  source system "agents"
  operation: "ask"
  access: observe
  input: Handover
  output: Handed
}

agent desk {
  can: [hand_over]
  max_steps: 1
}
"""

# A front desk: "@name words" goes to that agent, "anyone: words" lets the
# host choose, and anything else it answers itself.
FRONT = (
    "// The front desk. It passes requests on.\n"
    "record Msg { text: Text }\nrecord Said { text: Text }\n"
    + HAND
    + """
fn who(msg: Msg) -> Text {
  return if msg.text.starts_with("@") { msg.text.words().first().or("").drop(1) } else { "" }
}

fn what(msg: Msg) -> Text {
  return if msg.text.starts_with("@") { msg.text.words().drop(1).join(" ") } else { msg.text.replace("anyone: ", "") }
}

fn told(got: Handed) -> Said {
  return {text: if got.ok { got.agent + " says: " + got.text } else { "no: " + got.text }}
}

workflow passed(msg: Msg) -> Said {
  got  = execute desk.hand_over(who(msg), what(msg)) otherwise {ok: false, agent: "", text: "nobody could be asked"}
  said = told(got)
}

fn own(msg: Msg) -> Said {
  return {text: "desk: " + msg.text}
}

workflow main(msg: Msg) -> Said {
  said = match msg.text.starts_with("@") or msg.text.starts_with("anyone: ") {
    case true { call passed(msg) }
    case _ { own(msg) }
  }
}
"""
)

ECHO = """// The echo. It repeats what it is told, loudly.
record Msg { text: Text }
record Said { text: Text }

fn loud(msg: Msg) -> Said {
  return {text: msg.text.upper()}
}

workflow main(msg: Msg) -> Said {
  said = loud(msg)
}
"""

# An agent that changes a file, so it must ask first.
SCRIBE = """// The scribe. It writes notes into files and saves them.
record Msg { text: Text }
record Said { text: Text }
record Note { path: Text text: Text }
record Done { bytes: Number }

tool save {
  source system "files"
  operation: "write"
  access: execute
  input: Note
  output: Done
}

agent pen {
  can: [save]
  max_steps: 1
}

fn wrote(done: Done) -> Said {
  return {text: "saved " + done.bytes + " bytes"}
}

workflow main(msg: Msg) -> Said {
  done = execute pen.save("note.txt", msg.text)
  require approval
  said = wrote(done)
}
"""

# An agent that tries to hand on what it was handed.
RELAY = (
    "// The relay. It only ever passes things to the echo.\n"
    "record Msg { text: Text }\nrecord Said { text: Text }\n"
    + HAND
    + """
fn told(got: Handed) -> Said {
  return {text: if got.ok { "relayed: " + got.text } else { "relay could not: " + got.text }}
}

workflow main(msg: Msg) -> Said {
  got  = execute desk.hand_over("echo", msg.text) otherwise {ok: false, agent: "", text: "no way to hand on"}
  said = told(got)
}
"""
)


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name) / "agents"
        for name, source in (
            ("front", FRONT),
            ("echo", ECHO),
            ("scribe", SCRIBE),
            ("relay", RELAY),
        ):
            (self.root / name).mkdir(parents=True)
            (self.root / name / "main.kos").write_text(source, encoding="utf-8")
        self.app = App(
            self.root, bind_models=lambda program: {}, allow_folder=self.root
        )
        self.addCleanup(self.app.close)

    def until(self, job_id: str, *states: str) -> dict[str, Any]:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            view = self.app.poll(job_id, 0)
            if view["state"] in states:
                return view
            time.sleep(0.02)
        self.fail(f"the run never reached {states}: {self.app.poll(job_id, 0)}")

    def say(self, text: str, agent: str = "front") -> dict[str, Any]:
        job = self.app.start(f"{agent}/main.kos", {"text": text})
        return self.until(job.id, "done", "failed", "refused", "waiting") | {
            "id": job.id
        }


class HandOverTests(Fixture):
    def test_a_request_is_passed_on_and_the_answer_comes_back(self) -> None:
        view = self.say("@echo hello there")
        self.assertEqual(
            (view["state"], view["text"]), ("done", "echo says: HELLO THERE")
        )
        events = [(e["event"], e.get("agent")) for e in view["events"]]
        self.assertIn(("handed", "echo"), events)
        self.assertIn(("handed_back", "echo"), events)
        # The other agent's run is kept with that agent, as if opened there.
        self.assertEqual(len(list((self.root / "echo" / "runs").glob("*.jsonl"))), 1)

    def test_what_is_not_handed_over_is_answered_at_the_desk(self) -> None:
        self.assertEqual(self.say("hello")["text"], "desk: hello")

    def test_an_agent_that_is_not_there_is_named_as_missing(self) -> None:
        text = self.say("@nobody hi")["text"]
        self.assertTrue(text.startswith("no: There is no agent called nobody."), text)
        self.assertIn("echo, relay, scribe", text)

    def test_an_agent_cannot_hand_a_request_to_itself(self) -> None:
        self.assertIn("cannot hand a request to itself", self.say("@front hi")["text"])

    def test_with_no_name_the_host_chooses_by_what_each_agent_says_it_does(
        self,
    ) -> None:
        view = self.say("anyone: repeat this loudly")
        self.assertEqual(view["text"], "echo says: REPEAT THIS LOUDLY")

    def test_the_other_agent_still_asks_before_it_changes_anything(self) -> None:
        view = self.say("@scribe remember the milk")
        self.assertEqual(view["state"], "waiting")
        self.assertEqual(view["request"]["tool"], "save")
        self.assertEqual(view["request"]["handed"], "scribe")
        self.assertFalse((self.root / "note.txt").exists())
        self.app.decide(view["id"], True)
        done = self.until(view["id"], "done", "failed", "refused")
        self.assertEqual(done["text"], "scribe says: saved 17 bytes")
        self.assertEqual(
            (self.root / "note.txt").read_text(encoding="utf-8"), "remember the milk"
        )

    def test_a_no_to_the_other_agent_changes_nothing_and_the_desk_says_so(self) -> None:
        view = self.say("@scribe remember the milk")
        self.app.decide(view["id"], False)
        done = self.until(view["id"], "done", "failed", "refused")
        self.assertEqual(done["state"], "done")
        self.assertEqual(done["text"], "no: That was refused, so nothing was changed.")
        self.assertFalse((self.root / "note.txt").exists())

    def test_a_handed_request_is_not_handed_on(self) -> None:
        # Asked directly, the relay can pass to the echo.
        self.assertEqual(self.say("hi", agent="relay")["text"], "relayed: HI")
        # Handed the same request, it has no such tool to use.
        text = self.say("@relay hi")["text"]
        self.assertEqual(text, "relay says: relay could not: no way to hand on")


class DeclarationTests(unittest.TestCase):
    def load(self, source: str) -> Any:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "main.kos"
            path.write_text(source, encoding="utf-8")
            return load_program(path)

    def test_agents_is_a_kind_of_system_tool_with_one_operation(self) -> None:
        self.assertIn("agents", SYSTEM_KINDS)
        self.load(FRONT)
        with self.assertRaises(CheckError) as caught:
            self.load(FRONT.replace('operation: "ask"', 'operation: "order"'))
        self.assertIn("the only one is ask", str(caught.exception))

    def test_its_records_must_have_exactly_the_agreed_fields(self) -> None:
        with self.assertRaises(CheckError) as caught:
            self.load(
                FRONT.replace(
                    "record Handover { agent: Text text: Text }",
                    "record Handover { text: Text }",
                )
            )
        self.assertIn("agent: Text, text: Text", str(caught.exception))

    def test_a_host_without_agents_binds_nothing(self) -> None:
        program = self.load(FRONT)
        self.assertNotIn("hand_over", bind_system_tools(program, folder=None))
        bound = bind_agent_tools(
            program, lambda tool, arguments: {"ok": True, "agent": "x", "text": "y"}
        )
        self.assertEqual(list(bound), ["hand_over"])
        # Run with no host for it, the program takes its own way out.
        result = run(program, "main", None, inputs={"msg": {"text": "@echo hi"}})
        self.assertEqual(result["text"], "no: nobody could be asked")


if __name__ == "__main__":
    unittest.main()
