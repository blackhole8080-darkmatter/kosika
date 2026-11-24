"""A workflow `for` step: one step made once for each item of a list."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kosika.checker import CheckError
from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.modules import load_program
from kosika.parser import parse
from kosika.runtime import run
from kosika.story import read_trace
from kosika.trace_replay import replay_trace

SOURCE = """
record Plan { steps: Text[] }
record Ask { text: Text }
record Answer { text: Text }
record Out { text: Text }

model helper {
  input: Ask
  output: Answer
}

fn brief(item: Text, before: Answer[]) -> Text {
  return f"{item} after {before.length()}"
}

fn joined(all: Answer[]) -> Out {
  return {text: (for a in all { a.text }).join(" | ")}
}

workflow main(plan: Plan) -> Out {
  done = for item in plan.steps limit 3 { use_model helper(brief(item, done)) }
  out = joined(done)
}
"""


class WorkflowForTest(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.folder = Path(self._dir.name)
        (self.folder / "main.kos").write_text(SOURCE, encoding="utf-8")

    def go(self, steps: list[str]) -> tuple[dict, ScriptedModelProvider]:
        helper = ScriptedModelProvider(
            [ModelResponse(content=json.dumps({"text": f"#{n}"})) for n in range(3)]
        )
        result = run(
            load_program(self.folder / "main.kos"),
            "main",
            self.folder / "run.jsonl",
            inputs={"plan": {"steps": steps}},
            model_bindings={"helper": helper},
        )
        return result, helper

    def test_each_pass_sees_the_passes_before(self) -> None:
        result, helper = self.go(["a", "b"])
        self.assertEqual(result, {"text": "#0 | #1"})
        asked = [json.loads(r.messages[-1].content) for r in helper.requests]
        self.assertEqual([a["text"] for a in asked], ["a after 0", "b after 1"])

    def test_each_pass_is_a_step_of_the_trace_and_it_replays(self) -> None:
        self.go(["a", "b"])
        events = read_trace(self.folder / "run.jsonl")
        steps = [e["step"] for e in events if e["event"] == "step_start"]
        self.assertEqual(steps, ["done[0]", "done[1]", "out"])
        self.assertEqual(replay_trace(self.folder / "run.jsonl"), {"text": "#0 | #1"})

    def test_no_item_makes_no_pass(self) -> None:
        result, helper = self.go([])
        self.assertEqual(result, {"text": ""})
        self.assertEqual(helper.requests, [])

    def test_more_items_than_the_limit_stop_before_any_pass(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "more than its limit 3"):
            self.go(["a", "b", "c", "d"])

    def test_a_body_with_no_call_is_the_ordinary_for_of_a_value(self) -> None:
        program = parse(
            "record In { words: Text[] }\n"
            "record Out { sizes: Number[] }\n"
            "fn out(sizes: Number[]) -> Out { return {sizes: sizes} }\n"
            "workflow main(input: In) -> Out {\n"
            "  sizes = for w in input.words { w.length() }\n"
            "  done = out(sizes)\n"
            "}\n"
        )
        self.assertIsNone(program.workflows[0].steps[0].each_item)

    def test_the_bound_is_required(self) -> None:
        with self.assertRaisesRegex(CheckError, "needs a bound"):
            (self.folder / "main.kos").write_text(
                SOURCE.replace(" limit 3", ""), encoding="utf-8"
            )
            load_program(self.folder / "main.kos")


if __name__ == "__main__":
    unittest.main()
