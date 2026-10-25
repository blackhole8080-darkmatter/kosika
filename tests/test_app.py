"""The Kosika app: what it shows, what it may do, and who may ask."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from kosika.app import (
    PAGE,
    App,
    AppError,
    address,
    describe,
    find_agents,
    make_server,
    plain_refusal,
)
from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.parser import parse

DESK = """// A desk that remembers people.
//
//   kosika test main.kos

record Msg { text: Text }
record Said { text: Text }
record Seen { count: Number }

model cheap
default model careful
memory people of Seen

fn kind_of(msg: Msg) -> Said {
  return {text: if msg.text.contains("?") { "ask" } else { "count" }}
}

fn say(text: Text) -> Said {
  return {text: text}
}

fn bump(known: PeopleRecalled) -> Seen {
  return {count: if known.found { known.value.value().count + 1 } else { 1 }}
}

fn told(seen: Seen) -> Said {
  return {text: "visit " + seen.count}
}

workflow counted(msg: Msg) -> Said {
  known = recall people under "me"
  seen  = bump(known)
  saved = remember seen in people under "me"
  said  = told(seen)
}

workflow answered(msg: Msg) -> Said {
  label = classify msg.text into ["a", "b"] using cheap, careful vote 3 retry 1 otherwise fallback(msg)
  said  = say(label.label)
}

fn fallback(msg: Msg) -> Choice {
  return {label: "a", confidence: 0}
}

workflow main(msg: Msg) -> Said {
  budget 9 model calls
  kind = kind_of(msg)
  said = match kind.text {
    case "ask" { call answered(msg) }
    case _ { call counted(msg) }
  }
}

test "it counts" {
  a = main({text: "hi"})
  b = main({text: "hi"})
  expect b.text == "visit 2"
}

test "this one fails" {
  expect 1 == 2
}
"""

WRITER = """
record Note { path: Text text: Text }
record Done { bytes: Number }

tool save {
  source system "files"
  operation: "write"
  access: execute
  input: Note
  output: Done
}

agent scribe {
  can: [save]
  max_steps: 1
}

workflow main(note: Note) -> Done {
  done = execute scribe.save(note.path, note.text)
  require approval
}
"""


SKILL_NOTE = """# How a list is written
When: todo, shopping list

One thing on a line.
"""


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name) / "agents"
        (self.root / "desk").mkdir(parents=True)
        (self.root / "desk" / "main.kos").write_text(DESK, encoding="utf-8")
        (self.root / "writer").mkdir()
        (self.root / "writer" / "main.kos").write_text(WRITER, encoding="utf-8")
        (self.root / "broken.kos").write_text(
            "workflow main( -> Nope {", encoding="utf-8"
        )
        (self.root / "library.kos").write_text(
            "record OnlyTypes { a: Text }\n", encoding="utf-8"
        )
        for hidden in (".venv", "_trash", "node_modules"):
            (self.root / hidden).mkdir()
            (self.root / hidden / "main.kos").write_text(DESK, encoding="utf-8")
        self.providers = {
            "cheap": ScriptedModelProvider([]),
            "careful": ScriptedModelProvider([]),
        }

    def app(self, can_run: bool = True) -> App:
        return App(
            self.root,
            bind_models=(lambda program: self.providers) if can_run else None,
            model_label="test-model" if can_run else "",
        )


class DescribeTests(Fixture):
    def test_only_runnable_programs_outside_hidden_folders_are_found(self) -> None:
        names = [p.relative_to(self.root).as_posix() for p in find_agents(self.root)]
        self.assertEqual(names, ["broken.kos", "desk/main.kos", "writer/main.kos"])

    def test_a_card_says_what_an_agent_is_and_may_do(self) -> None:
        cards = {a["id"]: a for a in self.app().overview()["agents"]}
        desk = cards["desk/main.kos"]
        self.assertEqual(desk["title"], "desk")
        self.assertEqual(desk["about"], "A desk that remembers people.")
        self.assertEqual(desk["models"], ["cheap", "careful"])
        self.assertEqual(desk["default_model"], "careful")
        self.assertEqual(desk["memories"], [{"name": "people", "record": "Seen"}])
        self.assertEqual(desk["tools"], [])
        self.assertEqual((desk["tests"], desk["steps"], desk["chat"]), (2, 2, True))
        self.assertEqual(desk["budget"], [["model_calls", 9]])
        self.assertFalse(desk["changes_world"])
        writer = cards["writer/main.kos"]
        self.assertTrue(writer["changes_world"])
        self.assertFalse(writer["chat"])
        self.assertEqual(
            writer["agents"], [{"name": "scribe", "can": ["save"], "model": None}]
        )

    def test_a_program_that_does_not_check_is_listed_with_the_reason(self) -> None:
        cards = {a["id"]: a for a in self.app().overview()["agents"]}
        self.assertIn("error", cards["broken.kos"])
        detail = describe(self.app().agent("broken.kos"), detail=True)
        self.assertIn("workflow main(", detail["source"])

    def test_the_detail_draws_every_step(self) -> None:
        detail = describe(self.app().agent("desk/main.kos"), detail=True)
        workflows = {w["name"]: w for w in detail["workflows"]}
        self.assertEqual(detail["workflows"][0]["name"], "main")
        chooser = workflows["main"]["steps"][1]
        self.assertEqual(
            [(o["when"], o["kind"], o["text"]) for o in chooser["options"]],
            [("ask", "workflow", "answered"), (None, "workflow", "counted")],
        )
        think = workflows["answered"]["steps"][0]
        self.assertEqual(think["options"][0]["kind"], "think")
        self.assertEqual(think["options"][0]["text"], "classify")
        self.assertEqual((think["votes"], think["retries"]), (3, 1))
        self.assertEqual(think["voters"], ["cheap", "careful"])
        self.assertEqual(think["fallback"], {"kind": "code", "text": "fallback"})
        memory = workflows["counted"]["steps"][0]["options"][0]
        self.assertEqual(
            memory, {"when": None, "kind": "memory", "text": "recall people"}
        )
        self.assertEqual(detail["test_names"], ["it counts", "this one fails"])
        self.assertEqual(len(detail["program_hash"]), 64)
        json.dumps(detail)


class ActionTests(Fixture):
    def test_tests_run_without_a_model(self) -> None:
        results = self.app(can_run=False).tests("desk/main.kos")["results"]
        self.assertEqual([r["passed"] for r in results], [True, False])

    def test_a_run_keeps_memory_writes_a_trace_and_tells_its_story(self) -> None:
        app = self.app()
        first = app.run("desk/main.kos", {"text": "hello"})
        second = app.run("desk/main.kos", {"text": "hello"})
        self.assertEqual((first["text"], second["text"]), ("visit 1", "visit 2"))
        self.assertTrue((self.root / "desk" / "memory" / "people.json").exists())
        self.assertTrue((self.root / "desk" / second["trace"]).exists())
        self.assertTrue(second["story"]["replays"])
        again = app.story("desk/main.kos", second["trace"])
        self.assertEqual(again["result"], {"text": "visit 2"})
        listed = describe(app.agent("desk/main.kos"), detail=True)["traces"]
        self.assertEqual(len(listed), 2)

    def test_a_run_can_ask_the_granted_models(self) -> None:
        answer = ModelResponse(content=json.dumps({"label": "b", "confidence": 1}))
        self.providers["cheap"] = ScriptedModelProvider([answer, answer])
        self.providers["careful"] = ScriptedModelProvider([answer])
        self.assertEqual(
            self.app().run("desk/main.kos", {"text": "which?"})["text"], "b"
        )

    def test_without_a_model_nothing_can_be_run(self) -> None:
        with self.assertRaises(AppError) as caught:
            self.app(can_run=False).run("desk/main.kos", {"text": "hello"})
        self.assertEqual(caught.exception.status, 403)

    def test_a_change_is_refused_when_nobody_is_there_to_allow_it(self) -> None:
        app = App(self.root, bind_models=lambda program: {}, allow_folder=self.root)
        with self.assertRaises(AppError) as caught:
            app.run(
                "writer/main.kos",
                {"inputs": {"note": {"path": "pwned.txt", "text": "x"}}},
            )
        self.assertEqual(caught.exception.status, 422)
        self.assertFalse((self.root / "pwned.txt").exists())

    def test_only_agents_and_runs_inside_the_folder_can_be_named(self) -> None:
        app = self.app()
        for bad in ("../outside/main.kos", "library.kos", ".venv/main.kos", ""):
            with self.subTest(agent=bad), self.assertRaises(AppError):
                app.agent(bad)
        with self.assertRaises(AppError):
            app.story("desk/main.kos", "../../secret.jsonl")
        with self.assertRaises(AppError):
            app.run("desk/main.kos", {"nothing": 1})


class RefusalTests(unittest.TestCase):
    def test_a_missing_grant_says_what_to_do_in_studio(self) -> None:
        program = parse(
            "record P { path: Text }\nrecord L { text: Text }\n"
            'tool list_folder {\n  source system "files"\n  operation: "list"\n'
            "  access: observe\n  input: P\n  output: L\n}\n"
            'tool run_tests {\n  source system "shell"\n  operation: "run"\n'
            "  access: execute\n  input: P\n  output: L\n}\n"
        )
        folder = plain_refusal(program, "Agent tool 'list_folder' has no authorized host binding")
        self.assertIn("Choose a folder", folder)
        shell = plain_refusal(program, "Agent tool 'run_tests' has no authorized host binding")
        self.assertIn("Let agents start programs", shell)
        other = "Agent 'coder' has no authorized model binding"
        self.assertEqual(plain_refusal(program, other), other)


class LiveTests(Fixture):
    """A run started from Studio can be followed, and asks before it acts."""

    def writer(self) -> App:
        return App(self.root, bind_models=lambda program: {}, allow_folder=self.root)

    def until(self, app: App, run: str, *states: str) -> dict[str, Any]:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            view = app.poll(run, 0)
            if view["state"] in states:
                return view
            time.sleep(0.01)
        self.fail(f"the run never reached {states}")

    def test_a_run_reports_its_steps_as_it_goes_with_their_depth(self) -> None:
        app = self.app()
        job = app.start("desk/main.kos", {"text": "hello"})
        view = self.until(app, job.id, "done")
        self.assertEqual(view["text"], "visit 1")
        told = [(e["event"], e.get("step"), e["depth"]) for e in view["events"]]
        self.assertIn(("step_start", "kind", 0), told)
        self.assertIn(("step_start", "known", 1), told)
        self.assertIn(("step_complete", "said", 0), told)
        calls = [e for e in view["events"] if e["event"] == "workflow_call"]
        self.assertEqual(calls[0]["workflow"], "counted")
        # Only what the page needs travels: never the values themselves.
        self.assertTrue(all("arguments" not in e for e in view["events"]))
        later = app.poll(job.id, view["next"])
        self.assertEqual((later["events"], later["next"]), ([], view["next"]))

    def test_a_change_waits_for_a_yes_and_then_happens_once(self) -> None:
        app = self.writer()
        note = {"note": {"path": "made.txt", "text": "hello"}}
        job = app.start("writer/main.kos", {"inputs": note})
        view = self.until(app, job.id, "waiting")
        self.assertEqual(view["request"]["tool"], "save")
        self.assertEqual(view["request"]["arguments"]["path"], "made.txt")
        self.assertFalse((self.root / "made.txt").exists())
        app.decide(job.id, True)
        done = self.until(app, job.id, "done", "failed", "refused")
        self.assertEqual(done["state"], "done")
        self.assertEqual((self.root / "made.txt").read_text(encoding="utf-8"), "hello")
        with self.assertRaises(AppError) as caught:
            app.decide(job.id, True)
        self.assertEqual(caught.exception.status, 409)

    def test_a_refused_change_never_happens(self) -> None:
        app = self.writer()
        note = {"note": {"path": "never.txt", "text": "x"}}
        job = app.start("writer/main.kos", {"inputs": note})
        self.until(app, job.id, "waiting")
        app.decide(job.id, False)
        view = self.until(app, job.id, "refused", "done", "failed")
        self.assertEqual(view["state"], "refused")
        self.assertFalse((self.root / "never.txt").exists())

    def test_memory_can_be_looked_at(self) -> None:
        app = self.app()
        self.assertEqual(
            app.memory("desk/main.kos")["memories"],
            [{"name": "people", "record": "Seen", "entries": {}}],
        )
        app.run("desk/main.kos", {"text": "hello"})
        entries = app.memory("desk/main.kos")["memories"][0]["entries"]
        self.assertEqual(entries, {"me": {"count": 1}})

    def test_the_app_opens_on_an_agent_that_can_be_talked_to(self) -> None:
        overview = self.app().overview()
        self.assertEqual(overview["assistant"], "desk/main.kos")
        (self.root / "assistant").mkdir()
        (self.root / "assistant" / "main.kos").write_text(DESK, encoding="utf-8")
        self.assertEqual(self.app().assistant(), "assistant/main.kos")

    def test_an_unknown_run_is_not_found(self) -> None:
        with self.assertRaises(AppError) as caught:
            self.app().poll("nope", 0)
        self.assertEqual(caught.exception.status, 404)


class OutsideTests(Fixture):
    def test_the_folder_says_what_else_it_holds(self) -> None:
        (self.root / "notes.py").write_text(
            "def summarise(text):\n    return text\n", encoding="utf-8"
        )
        (self.root / "node_modules" / "hidden.py").write_text(
            "def never():\n    return 0\n", encoding="utf-8"
        )
        data = self.app().outside_sources()
        self.assertEqual(data["file_count"], 1)
        self.assertEqual(data["language_file_counts"], {"python": 1})
        self.assertEqual(
            [(item["path"], item["declarations"]) for item in data["files"]],
            [("notes.py", ["summarise"])],
        )

    def test_a_folder_with_nothing_else_says_zero(self) -> None:
        data = self.app().outside_sources()
        self.assertEqual((data["file_count"], data["files"]), (0, []))
        self.assertEqual(data["graph"]["nodes"], [])

    def test_a_file_the_map_drew_can_be_read_beside_it(self) -> None:
        (self.root / "helper.py").write_text(
            "def twice(n):\n    return n * 2\n", encoding="utf-8", newline="\n"
        )
        data = self.app().other_source("helper.py")
        self.assertEqual((data["path"], data["language"]), ("helper.py", "python"))
        self.assertEqual(data["text"], "def twice(n):\n    return n * 2\n")
        self.assertEqual((data["line_count"], data["truncated"]), (2, False))

    def test_a_file_outside_the_folder_is_not_given(self) -> None:
        (self.root.parent / "secret.py").write_text("x = 1\n", encoding="utf-8")
        for given in (
            "../secret.py",
            str(self.root.parent / "secret.py"),
            "desk/../../secret.py",
            "",
        ):
            with self.assertRaises(AppError) as caught:
                self.app().other_source(given)
            self.assertEqual(caught.exception.status, 422)

    def test_the_folder_is_drawn_with_its_links(self) -> None:
        (self.root / "store.py").write_text(
            "def load():\n    return 1\n", encoding="utf-8"
        )
        (self.root / "use.py").write_text(
            "from store import load\n\ndef start():\n    return load()\n",
            encoding="utf-8",
        )
        graph = self.app().outside_sources()["graph"]
        self.assertEqual(
            [(link["from"], link["to"], link["kind"]) for link in graph["links"]],
            [("use.py", "store.py", "import"), ("use.py", "store.py", "call")],
        )
        self.assertEqual(
            {node["path"]: node["level"] for node in graph["nodes"]},
            {"use.py": 0, "store.py": 1},
        )


class ServerTests(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.studio = self.app()
        self.server = make_server(self.studio)
        self.port = self.server.server_address[1]
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def ask(
        self,
        path: str,
        *,
        token: str | None = "right",
        headers: dict[str, str] | None = None,
        body: Any = None,
    ) -> tuple[int, Any]:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=None if body is None else json.dumps(body).encode("utf-8"),
            method="GET" if body is None else "POST",
        )
        if token is not None:
            request.add_header(
                "X-Kosika-Token", self.studio.token if token == "right" else token
            )
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw, status = response.read(), response.status
        except urllib.error.HTTPError as error:
            raw, status = error.read(), error.code
        try:
            return status, json.loads(raw)
        except ValueError:
            return status, raw.decode("utf-8")

    def test_the_page_and_the_data_are_served(self) -> None:
        status, page = self.ask("/?token=" + self.studio.token, token=None)
        self.assertEqual(status, 200)
        self.assertIn("<title>Kosika Studio</title>", page)
        status, data = self.ask("/api/agents")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["agents"]), 3)
        self.assertTrue(data["can_run"])
        status, detail = self.ask("/api/agent?id=desk%2Fmain.kos")
        self.assertEqual(detail["title"], "desk")
        self.assertIn("?token=", address(self.studio, self.server))

    def test_testing_and_running_work_over_http(self) -> None:
        status, tests = self.ask("/api/test?id=desk%2Fmain.kos", body={})
        self.assertEqual([r["passed"] for r in tests["results"]], [True, False])
        status, ran = self.ask("/api/run?id=desk%2Fmain.kos", body={"text": "hi"})
        self.assertEqual((status, ran["text"]), (200, "visit 1"))
        status, story = self.ask(
            "/api/story?id=desk%2Fmain.kos&trace=" + ran["trace"].replace("/", "%2F")
        )
        self.assertEqual(story["workflow"], "main")

    def test_a_run_can_be_started_and_followed_over_http(self) -> None:
        status, started = self.ask("/api/start?id=desk%2Fmain.kos", body={"text": "hi"})
        self.assertEqual(status, 200)
        for _ in range(2000):
            status, view = self.ask(f"/api/poll?run={started['run']}&since=0")
            if view["state"] != "running":
                break
            time.sleep(0.01)
        self.assertEqual(
            (status, view["state"], view["text"]), (200, "done", "visit 1")
        )
        status, _ = self.ask(f"/api/decide?run={started['run']}", body={"allow": True})
        self.assertEqual(status, 409)
        status, memory = self.ask("/api/memory?id=desk%2Fmain.kos")
        self.assertEqual(memory["memories"][0]["entries"], {"me": {"count": 1}})
        self.assertEqual(self.ask("/api/poll?run=nope")[0], 404)

    def test_requests_without_the_token_or_from_elsewhere_are_refused(self) -> None:
        self.assertEqual(self.ask("/api/agents", token=None)[0], 403)
        self.assertEqual(self.ask("/api/agents", token="wrong")[0], 403)
        self.assertEqual(self.ask("/?token=wrong", token=None)[0], 403)
        self.assertEqual(
            self.ask("/api/agents", headers={"Host": "evil.example"})[0], 403
        )
        self.assertEqual(
            self.ask("/api/agents", headers={"Origin": "http://evil.example"})[0], 403
        )

    def test_bad_requests_get_a_reason_not_a_crash(self) -> None:
        self.assertEqual(self.ask("/api/agent?id=nope")[0], 404)
        self.assertEqual(self.ask("/api/nothing")[0], 404)
        status, data = self.ask(
            "/api/run?id=desk%2Fmain.kos", body=["not", "an", "object"]
        )
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    def test_the_skills_section_is_served(self) -> None:
        status, data = self.ask("/api/skills")
        self.assertEqual(status, 200)
        self.assertIn("desk", [place["title"] for place in data["places"]])
        status, saved = self.ask(
            "/api/skills/save?id=desk/main.kos",
            body={"name": "lists", "text": SKILL_NOTE},
        )
        self.assertEqual((status, saved["saved"]), (200, True))
        status, picked = self.ask(
            "/api/skills/try?id=desk/main.kos", body={"text": "a todo for today"}
        )
        self.assertEqual(picked["picked"][0]["name"], "lists")
        status, checked = self.ask(
            "/api/skills/check", body={"name": "x", "text": "nothing"}
        )
        self.assertEqual((status, checked["ok"]), (200, False))
        status, _ = self.ask(
            "/api/skills/save?id=desk/main.kos",
            body={"name": "bad name", "text": SKILL_NOTE},
        )
        self.assertEqual(status, 422)
        status, _ = self.ask("/api/skills/save?id=desk/main.kos", body={"name": 3})
        self.assertEqual(status, 400)
        self.assertEqual(self.ask("/api/skills", token="wrong")[0], 403)

    def test_the_page_has_a_skills_section(self) -> None:
        page = PAGE.read_text(encoding="utf-8")
        self.assertEqual(page.count('["skills", "Skills"]'), 2)
        self.assertIn('id="icon-skills"', page)
        self.assertIn("async function showSkills(", page)

    def test_the_rest_of_the_folder_is_served(self) -> None:
        (self.root / "helper.py").write_text(
            "def twice(n):\n    return n * 2\n", encoding="utf-8"
        )
        status, data = self.ask("/api/outside")
        self.assertEqual(status, 200)
        self.assertEqual(data["files"][0]["path"], "helper.py")
        self.assertEqual(data["files"][0]["declarations"], ["twice"])

    def test_the_project_page_lists_other_languages(self) -> None:
        page = PAGE.read_text(encoding="utf-8")
        self.assertIn("function outsidePanel(", page)
        self.assertIn('api("/api/outside")', page)
        self.assertIn("kosika sourcemap . --format text", page)

    def test_another_language_file_can_be_read_over_http_but_not_from_outside(
        self,
    ) -> None:
        (self.root / "helper.py").write_text(
            "def twice(n):\n    return n * 2\n", encoding="utf-8"
        )
        (self.root.parent / "secret.py").write_text("x = 1\n", encoding="utf-8")
        status, data = self.ask("/api/other?path=helper.py")
        self.assertEqual((status, data["language"]), (200, "python"))
        self.assertIn("return n * 2", data["text"])
        self.assertEqual(self.ask("/api/other?path=..%2Fsecret.py")[0], 422)
        self.assertEqual(self.ask("/api/other?path=desk%2Fmain.kos")[0], 422)
        self.assertEqual(self.ask("/api/other?path=missing.py")[0], 422)

    def test_the_project_page_opens_a_file_it_drew(self) -> None:
        page = PAGE.read_text(encoding="utf-8")
        self.assertIn("function showOtherFile(", page)
        self.assertIn('api("/api/other?path="', page)
        self.assertIn("Read-only, as the map read it", page)

    def test_the_project_page_draws_what_connects_the_files(self) -> None:
        page = PAGE.read_text(encoding="utf-8")
        self.assertIn("function fileMap(", page)
        self.assertIn('class: "fmap-svg"', page)
        self.assertIn("fileMap(graph)", page)


class PageTests(unittest.TestCase):
    def test_the_page_never_builds_markup_from_data(self) -> None:
        page = PAGE.read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", page)
        self.assertNotIn("document.write", page)
        self.assertNotIn("http://", page.replace("http://127.0.0.1", ""))
        self.assertNotIn("https://", page)


if __name__ == "__main__":
    unittest.main()
