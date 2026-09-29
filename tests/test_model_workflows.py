from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from kosika import cli
from kosika.checker import CheckError, check
from kosika.controls import ControlledProvider, ExecutionError, ModelLimits
from kosika.model import (
    ModelProviderError,
    ModelResponse,
    ModelToolCall,
    ModelUsage,
    OpenAICompatibleProvider,
    ScriptedModelProvider,
)
from kosika.parser import ParseError, parse
from kosika.runtime import replay, run

ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "examples" / "model_answer.syn"
ANSWER = {"text": "A typed AI language.", "confidence": 0.9}
INPUTS = {"question": {"text": "What is Kosika?"}}


class ModelWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = EXAMPLE.read_text(encoding="utf-8")
        self.program = parse(self.source)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.trace = Path(self.directory.name) / "trace.jsonl"

    def provider(self, content: str | None = None) -> ScriptedModelProvider:
        return ScriptedModelProvider(
            [
                ModelResponse(
                    content=content if content is not None else json.dumps(ANSWER),
                    usage=ModelUsage(total_tokens=12),
                )
            ]
        )

    def test_typed_model_run_records_and_replays_without_provider(self) -> None:
        provider = self.provider()
        result = run(
            self.program,
            "answer",
            self.trace,
            inputs=INPUTS,
            model_bindings={"writer": provider},
        )
        self.assertEqual(result, ANSWER)
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(provider.requests[0].tools, ())
        self.assertEqual(
            json.loads(provider.requests[0].messages[-1].content), INPUTS["question"]
        )
        self.assertIn('"confidence"', provider.requests[0].messages[0].content)
        events = [json.loads(line) for line in self.trace.read_text().splitlines()]
        self.assertEqual(events[0]["inputs"], INPUTS)
        self.assertEqual(events[3]["usage"]["total_tokens"], 12)
        with patch.object(
            provider,
            "complete",
            side_effect=AssertionError("offline replay called provider"),
        ):
            self.assertEqual(replay(self.trace), ANSWER)

    def test_missing_binding_and_invalid_inputs_prevent_model_request(self) -> None:
        provider = self.provider()
        with self.assertRaisesRegex(PermissionError, "host binding"):
            run(self.program, "answer", self.trace, inputs=INPUTS)
        for bad_input in ({}, {"question": {"text": 7}}, {**INPUTS, "extra": {}}):
            with self.subTest(inputs=bad_input), self.assertRaises(ValueError):
                run(
                    self.program,
                    "answer",
                    self.trace,
                    inputs=bad_input,
                    model_bindings={"writer": provider},
                )
        with self.assertRaisesRegex(ValueError, "undeclared models"):
            run(
                self.program,
                "answer",
                self.trace,
                inputs=INPUTS,
                model_bindings={"other": provider},
            )
        self.assertEqual(provider.requests, [])
        self.assertEqual(json.loads(self.trace.read_text().splitlines()[-1])["event"], "run_failed")
        with self.assertRaises(ValueError):
            replay(self.trace)

    def test_model_output_fails_closed(self) -> None:
        contents = [
            "not json",
            "[]",
            '{"text":"x"}',
            '{"text":"x","confidence":true}',
            '{"text":"x","confidence":NaN}',
            '{"text":"x","confidence":1e999}',
            '{"text":"a","text":"b","confidence":1}',
        ]
        for content in contents:
            with self.subTest(content=content), self.assertRaises(ModelProviderError):
                run(
                    self.program,
                    "answer",
                    self.trace,
                    inputs=INPUTS,
                    model_bindings={"writer": self.provider(content)},
                )
        for response in (
            ModelResponse(content=None),
            ModelResponse(content=json.dumps(ANSWER), finish_reason="length"),
            ModelResponse(
                content=json.dumps(ANSWER),
                tool_calls=(ModelToolCall("1", "shell", {}),),
            ),
        ):
            with self.subTest(response=response), self.assertRaises(ModelProviderError):
                run(
                    self.program,
                    "answer",
                    self.trace,
                    inputs=INPUTS,
                    model_bindings={"writer": ScriptedModelProvider([response])},
                )
        self.assertEqual(json.loads(self.trace.read_text().splitlines()[-1])["event"], "run_failed")
        with self.assertRaises(ValueError):
            replay(self.trace)

    def test_model_declarations_and_argument_types_are_checked(self) -> None:
        invalid = [
            self.source.replace("input: Question", "input: Unknown"),
            self.source.replace("use_model writer", "use_model unknown"),
            self.source.replace("text: question.text", "text: 7"),
            self.source.replace("question: Question", "question: Unknown"),
            self.source.replace(
                "question: Question", "question: Question, question: Question"
            ),
            self.source + "\nmodel writer { input: Question output: Answer }",
            self.source.replace("let result: Answer", "let result: Question"),
            self.source.replace("let result", "let question").replace(
                "return result", "return question"
            ),
        ]
        for source in invalid:
            with self.subTest(source=source), self.assertRaises(CheckError):
                check(parse(source))
        for fields in (
            "input: Question",
            "input: Question input: Question output: Answer",
            "input: Question output: Answer provider: remote",
        ):
            with self.subTest(fields=fields), self.assertRaises(ParseError):
                parse("model writer { " + fields + " }")

    def test_replay_rejects_incomplete_reordered_or_inconsistent_trace(self) -> None:
        run(
            self.program,
            "answer",
            self.trace,
            inputs=INPUTS,
            model_bindings={"writer": self.provider()},
        )
        original = [json.loads(line) for line in self.trace.read_text().splitlines()]
        variants = [
            original[:-1],
            original + [original[-1]],
            [*original[:2], *original[3:]],
        ]
        for index, field, value in (
            (2, "model", "different"),
            (3, "result", {"text": "changed"}),
            (3, "content", "{}"),
            (5, "result", {"text": "changed"}),
            (0, "trace_version", 99),
        ):
            events = json.loads(json.dumps(original))
            events[index][field] = value
            variants.append(events)
        for events in variants:
            self.trace.write_text(
                "\n".join(json.dumps(event) for event in events), encoding="utf-8"
            )
            with self.subTest(events=events), self.assertRaises(ValueError):
                replay(self.trace)

    def test_model_result_flows_into_another_model_step(self) -> None:
        source = self.source + "\nmodel editor { input: Answer output: Answer }"
        source = source.replace(
            "    return result\n  }",
            "    return result\n  }\n"
            "  step refine {\n let final = use_model editor(text: result.text, confidence: result.confidence)\n return final\n }",
        )
        editor = self.provider()
        run(
            parse(source),
            "answer",
            self.trace,
            inputs=INPUTS,
            model_bindings={"writer": self.provider(), "editor": editor},
        )
        self.assertEqual(json.loads(editor.requests[0].messages[-1].content), ANSWER)
        self.assertEqual(replay(self.trace), ANSWER)

    def test_openai_compatible_adapter_can_supply_structured_step(self) -> None:
        captured = []

        def transport(request, timeout):
            captured.append(json.loads(request.data))
            return json.dumps(
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": json.dumps(ANSWER)},
                        }
                    ]
                }
            ).encode()

        provider = OpenAICompatibleProvider(
            "http://localhost:11434/v1", "local-model", transport=transport
        )
        self.assertEqual(
            run(
                self.program,
                "answer",
                self.trace,
                inputs=INPUTS,
                model_bindings={"writer": provider},
            ),
            ANSWER,
        )
        self.assertEqual(captured[0]["model"], "local-model")
        self.assertNotIn("tools", captured[0])

    def test_runtime_transport_failure_records_provider_telemetry(self) -> None:
        def transport(_request, _timeout):
            raise urllib.error.URLError("offline transport")

        controlled = ControlledProvider(
            OpenAICompatibleProvider(
                "http://localhost:11434/v1", "local-model", transport=transport
            ),
            ModelLimits(),
        )
        with self.assertRaisesRegex(ExecutionError, "request failed"):
            run(
                self.program,
                "answer",
                self.trace,
                inputs=INPUTS,
                model_bindings={"writer": controlled},
            )
        self.assertEqual(controlled.telemetry[0]["error"], "provider")
        self.assertEqual(json.loads(self.trace.read_text().splitlines()[-1])["event"], "run_failed")
        with self.assertRaises(ValueError):
            replay(self.trace)

    def test_missing_model_binding_blocks_earlier_tool_step(self) -> None:
        tool_source = (ROOT / "examples" / "test_host.syn").read_text(encoding="utf-8")
        declarations = tool_source.split("workflow main")[0]
        source = declarations + self.source.replace(
            "  step respond {",
            "  step inspect { let status = execute monitor.check_disk() return status }\n"
            "  step respond {",
        )
        with patch("kosika.runtime._call_declared_tool") as tool:
            with self.assertRaises(PermissionError):
                run(parse(source), "answer", self.trace, inputs=INPUTS)
            tool.assert_not_called()

    def test_replay_does_not_confuse_booleans_with_numbers(self) -> None:
        run(
            self.program,
            "answer",
            self.trace,
            inputs=INPUTS,
            model_bindings={"writer": self.provider('{"text":"x","confidence":1}')},
        )
        events = [json.loads(line) for line in self.trace.read_text().splitlines()]
        events[-1]["result"]["confidence"] = True
        self.trace.write_text(
            "\n".join(json.dumps(event) for event in events), encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "final outcome mismatch"):
            replay(self.trace)

    def test_cli_rejects_partial_live_configuration_without_network(self) -> None:
        args = [
            "kosika",
            "run",
            str(EXAMPLE),
            "--workflow",
            "answer",
            "--input",
            str(ROOT / "examples" / "question.json"),
            "--model-binding",
            "writer",
        ]
        with (
            patch.object(sys, "argv", args),
            contextlib.redirect_stderr(io.StringIO()),
            patch.object(cli, "OpenAICompatibleProvider") as provider,
        ):
            self.assertEqual(cli.main(), 2)
            provider.assert_not_called()

    def test_cli_offline_example_and_replay(self) -> None:
        args = [
            "kosika",
            "run",
            str(EXAMPLE),
            "--workflow",
            "answer",
            "--input",
            str(ROOT / "examples" / "question.json"),
            "--model-fixture",
            str(ROOT / "examples" / "model_answers.json"),
            "--trace",
            str(self.trace),
        ]
        with patch.object(sys, "argv", args), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(), 0)
        with (
            patch.object(sys, "argv", ["kosika", "replay", str(self.trace)]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(cli.main(), 0)
        self.trace.write_text("{}\n", encoding="utf-8")
        with (
            patch.object(sys, "argv", ["kosika", "replay", str(self.trace)]),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(cli.main(), 2)


if __name__ == "__main__":
    unittest.main()
