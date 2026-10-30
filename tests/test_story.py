"""`kosika story`: a run told in plain words from its trace."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from kosika import cli
from kosika.checker import check
from kosika.memory import bind_memory_tools
from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.parser import parse
from kosika.runtime import run
from kosika.story import StoryError, build_story, read_trace, to_text

SOURCE = """
record Ticket { customer: Text text: Text }
record Seen { count: Number }
record Order { id: Text? }
record Out { queue: Text refund: Bool id: Text count: Number }

model cheap
default model careful
memory people of Seen

fn keyword_label(ticket: Ticket) -> Choice {
  return {label: "other", confidence: 0}
}

fn bump(known: PeopleRecalled) -> Seen {
  let before = if known.found { known.value.value().count } else { 0 }
  return {count: before + 1}
}

fn build(label: Choice, refund: Decision, order: Order, seen: Seen) -> Out {
  return {queue: label.label, refund: refund.answer, id: order.id.or("none"), count: seen.count}
}

workflow main(ticket: Ticket) -> Out {
  budget 20 model calls
  known  = recall people under ticket.customer
  label  = classify ticket.text into ["billing", "other"] retry 1 otherwise keyword_label(ticket)
  refund = decide "Refund asked?" given ticket.text using cheap, careful vote 3
  order  = extract Order from ticket.text with evidence
  seen   = bump(known)
  saved  = remember seen in people under ticket.customer
  out    = build(label, refund, order, seen)
}
"""

INPUTS = {
    "ticket": {
        "customer": "Ada",
        "text": "Order KX-1 was charged twice. Refund please.",
    }
}


def decision(answer: bool) -> dict[str, Any]:
    return {"answer": answer, "confidence": 0.8, "reason": "They ask for it."}


def scripted(*answers: Any) -> ScriptedModelProvider:
    return ScriptedModelProvider(
        [ModelResponse(content=json.dumps(answer)) for answer in answers]
    )


class StoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.trace = self.root / "run.jsonl"

    def record(self, careful: list[Any], cheap: list[Any]) -> None:
        program = parse(SOURCE)
        check(program)
        try:
            run(
                program,
                "main",
                self.trace,
                inputs=INPUTS,
                model_bindings={
                    "careful": scripted(*careful),
                    "cheap": scripted(*cheap),
                },
                tool_bindings=bind_memory_tools(program, self.root / "memory"),
            )
        except Exception:
            pass

    def good(self) -> str:
        self.record(
            careful=[
                {"label": "refunds", "confidence": 1},
                {"label": "billing", "confidence": 0.9},
                decision(True),
                {"id": "KX-1", "evidence": {"id": "Order KX-1"}},
            ],
            cheap=[decision(False), decision(True)],
        )
        return to_text(build_story(read_trace(self.trace)))

    def test_every_part_of_the_run_is_told(self) -> None:
        text = self.good()
        self.assertIn("Run of main", text)
        self.assertIn("1. known: looked up 'Ada' in memory people", text)
        self.assertIn("-> nothing stored yet", text)
        self.assertIn('asked careful to classify into ["billing", "other"]', text)
        self.assertIn("rejected, then asked again: classify answered 'refunds'", text)
        self.assertIn("-> billing (confidence 0.9)", text)
        self.assertIn("asked cheap to decide: Refund asked?", text)
        self.assertIn(
            "vote: 2 of 3 agreed (1 for false, 2 for true); asked cheap, careful, cheap",
            text,
        )
        self.assertIn("asked careful to extract Order", text)
        self.assertIn("backed by id: 'Order KX-1'", text)
        self.assertIn("5. seen: computed bump in code", text)
        self.assertIn("remembered {\"count\": 1} under 'Ada' in memory people", text)
        self.assertIn("memory people now holds 1 entry", text)
        self.assertIn('Result: {"count": 1,', text)
        self.assertIn("6 model call(s) of 20 allowed", text)
        self.assertIn("This trace replays", text)

    def test_a_fallback_is_told_with_its_reason(self) -> None:
        bad = {"label": "refunds", "confidence": 1}
        self.record(
            careful=[bad, bad, decision(True), {"id": None, "evidence": {}}],
            cheap=[decision(True), decision(True)],
        )
        text = to_text(build_story(read_trace(self.trace)))
        self.assertIn("2. label: computed keyword_label in code", text)
        self.assertIn("the first attempt failed: classify answered 'refunds'", text)
        self.assertIn("so the fallback ran instead", text)

    def test_a_run_that_stopped_is_told_as_far_as_it_went(self) -> None:
        self.record(careful=[{"label": "billing", "confidence": 1}], cheap=[])
        story = build_story(read_trace(self.trace))
        self.assertFalse(story["finished"])
        text = to_text(story)
        self.assertIn("The run stopped before finishing", text)
        self.assertIn("1. known", text)

    def test_a_tampered_trace_is_flagged(self) -> None:
        self.good()
        events = read_trace(self.trace)
        for event in events:
            if event.get("event") == "step_complete" and event.get("step") == "label":
                event["result"] = {"label": "other", "confidence": 1}
        story = build_story(events)
        self.assertFalse(story["replays"])
        self.assertIn("Careful: this trace does not replay", to_text(story))

    def test_the_story_is_also_data(self) -> None:
        self.good()
        story = build_story(read_trace(self.trace))
        self.assertEqual(
            [step["kind"] for step in story["steps"]],
            [
                "memory",
                "model",
                "model",
                "model",
                "code",
                "memory",
                "code",
            ],
        )
        json.dumps(story)

    def test_other_files_are_refused(self) -> None:
        other = self.root / "x.txt"
        other.write_text("hello", encoding="utf-8")
        with self.assertRaises(StoryError):
            read_trace(other)
        other.write_text('{"event": "step_start"}\n', encoding="utf-8")
        with self.assertRaisesRegex(StoryError, "must begin with the start of a run"):
            build_story(read_trace(other))

    def test_the_command(self) -> None:
        self.good()

        def invoke(*arguments: str) -> tuple[int, str]:
            out, err = io.StringIO(), io.StringIO()
            with patch.object(sys, "argv", ["kosika", *arguments]):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = cli.main()
            return code, out.getvalue() + err.getvalue()

        code, output = invoke("story", str(self.trace))
        self.assertEqual(code, 0)
        self.assertIn("Run of main", output)
        code, output = invoke("story", str(self.trace), "--json")
        self.assertEqual(json.loads(output)["workflow"], "main")
        code, output = invoke("story", str(self.root / "missing.jsonl"))
        self.assertEqual(code, 2)
        self.assertIn("Story unavailable", output)


if __name__ == "__main__":
    unittest.main()


class MindmapAndStarterTests(unittest.TestCase):
    def test_the_mind_map_shows_retries_votes_and_voters(self) -> None:
        from kosika.mindmap import build_mindmap

        program = parse(SOURCE)
        check(program)
        steps = {
            node["label"]: node["attributes"]
            for node in build_mindmap(program)["nodes"]
            if node["kind"] == "step"
        }
        self.assertEqual(steps["2. label"]["retries"], 1)
        self.assertEqual(steps["3. refund"]["votes"], 3)
        self.assertEqual(steps["3. refund"]["voters"], ["cheap", "careful"])
        self.assertNotIn("votes", steps["1. known"])

    def test_each_agent_kind_is_a_working_tested_project(self) -> None:
        from kosika.modules import load_program
        from kosika.starter import KINDS, StarterError, create_starter
        from kosika.testing import run_tests

        self.assertEqual(KINDS, ("hello", "support", "expense", "notes", "researcher"))
        with tempfile.TemporaryDirectory() as directory:
            for kind in KINDS[1:]:
                with self.subTest(kind=kind):
                    project = Path(directory) / kind
                    names = sorted(p.name for p in create_starter(project, kind))
                    expected = ["README.md", "input.json", "main.kos"]
                    if (project / "responses.json").exists():
                        json.loads((project / "responses.json").read_text("utf-8"))
                        expected.append("responses.json")
                    self.assertEqual(names, expected)
                    results = run_tests(load_program(project / "main.kos"))
                    self.assertTrue(results)
                    self.assertTrue(all(r.passed for r in results), results)
                    json.loads((project / "input.json").read_text("utf-8"))
                    self.assertIn(
                        "kosika story", (project / "README.md").read_text("utf-8")
                    )
            with self.assertRaisesRegex(StarterError, "Unknown kind"):
                create_starter(Path(directory) / "x", "wrong")
            self.assertFalse((Path(directory) / "x").exists())
