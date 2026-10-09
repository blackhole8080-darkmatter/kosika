"""`parallel { }`: steps whose slow calls are made at the same time."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any

from kosika.ast import program_payload
from kosika.checker import CheckError
from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.modules import load_program
from kosika.parser import parse
from kosika.runtime import run
from kosika.story import read_trace
from kosika.trace_replay import replay_trace

SOURCE = """
record In { a: Text b: Text c: Text }
record Out { text: Text }
record Where { path: Text }
record FileText { text: Text }
record Ask { text: Text }
record Answer { text: Text }

model fast {
  input: Ask
  output: Answer
}

model slow {
  input: Ask
  output: Answer
}

tool look {
  source system "files"
  operation: "read"
  access: observe
  input: Where
  output: FileText
}

agent hands {
  can: [look]
  max_steps: 1
}

fn empty(input: In) -> FileText {
  return {text: "(missing)"}
}

fn join(one: FileText, two: FileText, three: FileText) -> Out {
  return {text: f"{one.text}+{two.text}+{three.text}"}
}

workflow main(input: In) -> Out {
  parallel {
    one   = execute hands.look(input.a)
    two   = execute hands.look(input.b) otherwise empty(input)
    three = execute hands.look(input.c)
  }
  out = join(one, two, three)
}

fn both(x: Answer, y: Answer) -> Out {
  return {text: f"{x.text} / {y.text}"}
}

workflow ask_two(input: In) -> Out {
  parallel {
    x = use_model fast(input.a)
    y = use_model slow(input.b)
  }
  out = both(x, y)
}
"""

INPUTS = {"input": {"a": "a.txt", "b": "b.txt", "c": "c.txt"}}


class SlowReader:
    """A read-only tool that takes a moment and notes who was reading with it."""

    def __init__(self, seconds: float, missing: tuple[str, ...] = ()) -> None:
        self.seconds, self.missing = seconds, missing
        self.reading = 0
        self.most = 0
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def __call__(self, tool: Any, arguments: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.reading += 1
            self.most = max(self.most, self.reading)
            self.calls.append(arguments["path"])
        time.sleep(self.seconds)
        with self._lock:
            self.reading -= 1
        if arguments["path"] in self.missing:
            raise ValueError(f"{arguments['path']!r} is not a file")
        return {"text": arguments["path"].upper()}


class SlowModel:
    def __init__(self, text: str, seconds: float) -> None:
        self.text, self.seconds = text, seconds

    def complete(self, request: Any) -> ModelResponse:
        time.sleep(self.seconds)
        return ModelResponse(content=json.dumps({"text": self.text}))


class ParallelTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.folder = Path(self._dir.name)

    def program(self, source: str = SOURCE) -> Any:
        (self.folder / "main.kos").write_text(source, encoding="utf-8")
        return load_program(self.folder / "main.kos")

    def go(self, reader: Any, workflow: str = "main", **extra: Any) -> Any:
        return run(
            self.program(),
            workflow,
            self.folder / "run.jsonl",
            inputs=INPUTS,
            tool_bindings={"look": reader},
            **extra,
        )

    def test_the_slow_calls_of_a_block_overlap(self) -> None:
        reader = SlowReader(0.4)
        started = time.monotonic()
        result = self.go(reader)
        took = time.monotonic() - started
        self.assertEqual(result, {"text": "A.TXT+B.TXT+C.TXT"})
        self.assertEqual(reader.most, 3)
        # One after another would take 1.2 seconds.
        self.assertLess(took, 1.0)

    def test_the_trace_is_what_running_in_order_would_have_written(self) -> None:
        self.go(SlowReader(0.05))
        kinds = [
            (event["event"], event.get("step") or event.get("tool"))
            for event in read_trace(self.folder / "run.jsonl")
        ]
        steps = [name for kind, name in kinds if kind == "step_start"]
        self.assertEqual(steps, ["one", "two", "three", "out"])
        self.assertEqual(
            replay_trace(self.folder / "run.jsonl"), {"text": "A.TXT+B.TXT+C.TXT"}
        )

    def test_a_failure_in_the_block_is_handled_where_it_was_written(self) -> None:
        reader = SlowReader(0.05, missing=("b.txt",))
        self.assertEqual(self.go(reader), {"text": "A.TXT+(missing)+C.TXT"})
        with self.assertRaisesRegex(ValueError, "'c.txt' is not a file"):
            self.go(SlowReader(0.05, missing=("c.txt",)))

    def test_two_different_models_are_asked_at_the_same_time(self) -> None:
        started = time.monotonic()
        result = self.go(
            SlowReader(0),
            "ask_two",
            model_bindings={
                "fast": SlowModel("one", 0.4),
                "slow": SlowModel("two", 0.4),
            },
        )
        self.assertEqual(result, {"text": "one / two"})
        self.assertLess(time.monotonic() - started, 0.75)

    def test_scripted_answers_are_still_given_in_order(self) -> None:
        # A scripted model answers in the order it is asked, so it is never
        # asked from two places at once and a test means the same every time.
        def scripted(text: str) -> ScriptedModelProvider:
            return ScriptedModelProvider(
                [ModelResponse(content=json.dumps({"text": text}))]
            )

        for _ in range(5):
            result = self.go(
                SlowReader(0),
                "ask_two",
                model_bindings={"fast": scripted("one"), "slow": scripted("two")},
            )
            self.assertEqual(result, {"text": "one / two"})

    def test_what_may_not_be_in_a_parallel_block(self) -> None:
        for change, reason in (
            (
                (
                    "    two   = execute hands.look(input.b) otherwise empty(input)\n",
                    "    two   = execute hands.look(one.text)\n",
                ),
                "worked out in the same parallel block",
            ),
            (
                (
                    "    three = execute hands.look(input.c)\n",
                    "    three = call ask_two(input)\n",
                ),
                "it calls a workflow",
            ),
        ):
            with self.subTest(reason=reason), self.assertRaises(CheckError) as caught:
                self.program(SOURCE.replace(*change))
            self.assertIn(reason, str(caught.exception))
        writes = SOURCE.replace(
            'operation: "read"\n  access: observe',
            'operation: "write"\n  access: execute',
        )
        with self.assertRaises(CheckError):
            self.program(writes)

    def test_a_program_without_a_block_hashes_as_before(self) -> None:
        plain = parse(
            SOURCE.replace("  parallel {\n", "")
            .replace("  }\n  out = join", "  out = join")
            .replace("  }\n  out = both", "  out = both")
        )
        self.assertNotIn('"group"', json.dumps(program_payload(plain)))
        self.assertIn('"group"', json.dumps(program_payload(parse(SOURCE))))


if __name__ == "__main__":
    unittest.main()
