from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.modules import load_program
from kosika.runtime import replay, run


class SupportedLanguageContractTests(unittest.TestCase):
    root = Path(__file__).parents[1]
    examples = root / "examples"

    def test_supported_pure_examples_execute_and_replay(self) -> None:
        cases = (
            ("hello_world.kos", None, {"text": "Hello, world!"}),
            (
                "typed_input_output.kos",
                {"request": {"quality": 0.9, "reliability": 0.8}},
                {"score": 0.87, "accepted": True},
            ),
            ("closures.kos", None, {"value": 115}),
            (
                "recursion_match.kos",
                None,
                {
                    "factorial": 120,
                    "parity": "odd",
                    "classification": "zero",
                },
            ),
        )

        with tempfile.TemporaryDirectory() as directory:
            trace_root = Path(directory)
            for source_name, inputs, expected in cases:
                with self.subTest(source=source_name):
                    program = load_program(self.examples / source_name)
                    trace = trace_root / f"{Path(source_name).stem}.jsonl"
                    result = run(program, "main", trace, inputs=inputs)

                    self.assertEqual(result, expected)
                    self.assertEqual(replay(trace), expected)
                    first_event = json.loads(
                        trace.read_text(encoding="utf-8").splitlines()[0]
                    )
                    self.assertEqual(first_event["trace_version"], 5)

    def test_supported_module_and_model_example_executes_and_replays_offline(
        self,
    ) -> None:
        example = self.examples / "composed"
        program = load_program(example / "main.kos")
        inputs = json.loads((example / "inputs.json").read_text(encoding="utf-8"))
        fixture = json.loads((example / "responses.json").read_text(encoding="utf-8"))
        expected = fixture["reader"][0]
        provider = ScriptedModelProvider(
            [ModelResponse(content=json.dumps(expected, sort_keys=True))]
        )

        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "composed.jsonl"
            result = run(
                program,
                "main",
                trace,
                inputs=inputs,
                model_bindings={"reader": provider},
            )
            self.assertEqual(result, expected)
            self.assertEqual(len(provider.requests), 1)

            with patch.object(
                provider, "complete", side_effect=AssertionError("live model call")
            ):
                self.assertEqual(replay(trace), expected)


if __name__ == "__main__":
    unittest.main()
