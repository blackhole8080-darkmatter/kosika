"""A command-line program as a model provider, and `kosika run --model-command`."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kosika import cli
from kosika.command_model import CommandModelProvider, unwrap_json
from kosika.model import ChatMessage, ModelProviderError, ModelRequest, ModelTool
from kosika.runtime import replay

FENCE = "`" * 3

PROGRAM = """
record Ticket { text: Text }
record Routing { queue: Text sure: Bool }

model assistant

fn route(label: Choice) -> Routing {
  return {queue: label.label, sure: label.confidence > 0.5}
}

workflow main(ticket: Ticket) -> Routing {
  label = classify ticket.text into ["billing", "bug"]
  routing = route(label)
}
"""

# Stand-in for a model program: reads the request, prints a fenced answer.
ANSWERING = f"""
import sys
request = sys.stdin.read()
assert "charged twice" in request, request
print("Here is the answer:")
print("{FENCE}json")
print('{{"label": "billing", "confidence": 0.9}}')
print("{FENCE}")
"""


def invoke(*arguments: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with patch.object(sys, "argv", ["kosika", *arguments]):
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main()
    return code, out.getvalue(), err.getvalue()


def request(text: str = "hello") -> ModelRequest:
    return ModelRequest(messages=(ChatMessage(role="user", content=text),))


class UnwrapJsonTests(unittest.TestCase):
    def test_plain_json_is_kept(self) -> None:
        self.assertEqual(unwrap_json(' {"a": 1}\n'), '{"a": 1}')

    def test_one_fenced_block_is_taken_out(self) -> None:
        reply = f'Sure.\n{FENCE}json\n{{"a": 1}}\n{FENCE}\nDone.'
        self.assertEqual(unwrap_json(reply), '{"a": 1}')

    def test_two_valid_blocks_are_not_guessed_between(self) -> None:
        reply = f'{FENCE}\n{{"a": 1}}\n{FENCE}\n{FENCE}\n{{"a": 2}}\n{FENCE}'
        self.assertEqual(unwrap_json(reply), reply)

    def test_text_that_is_not_json_is_passed_on(self) -> None:
        self.assertEqual(unwrap_json("I cannot say."), "I cannot say.")


class CommandModelProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)

    def script(self, body: str) -> list[str]:
        path = self.root / "model.py"
        path.write_text(body, encoding="utf-8")
        return [sys.executable, str(path)]

    def test_the_request_goes_in_and_the_reply_comes_out(self) -> None:
        provider = CommandModelProvider(
            self.script("import sys\nprint(sys.stdin.read().upper())")
        )
        response = provider.complete(request("hello"))
        self.assertEqual(response.content, "[USER]\nHELLO")

    def test_the_command_runs_outside_the_project(self) -> None:
        provider = CommandModelProvider(
            self.script("import os, json\nprint(json.dumps(os.listdir('.')))")
        )
        self.assertEqual(provider.complete(request()).content, "[]")

    def test_tools_are_refused(self) -> None:
        provider = CommandModelProvider(self.script("print('{}')"))
        tool = ModelTool(name="t", description="", input_schema={})
        with self.assertRaisesRegex(ModelProviderError, "cannot be given tools"):
            provider.complete(
                ModelRequest(
                    messages=(ChatMessage(role="user", content="x"),), tools=(tool,)
                )
            )

    def test_an_empty_reply_reports_the_error_output(self) -> None:
        provider = CommandModelProvider(
            self.script("import sys\nsys.stderr.write('quota used up')\nsys.exit(3)")
        )
        with self.assertRaisesRegex(ModelProviderError, "(?s)exited 3.*quota used up"):
            provider.complete(request())

    def test_a_slow_command_is_stopped(self) -> None:
        provider = CommandModelProvider(
            self.script("import time\ntime.sleep(30)"), timeout=0.5
        )
        with self.assertRaisesRegex(ModelProviderError, "longer than 0.5 seconds"):
            provider.complete(request())

    def test_a_missing_program_is_rejected_up_front(self) -> None:
        with self.assertRaisesRegex(ValueError, "was not found"):
            CommandModelProvider(["kosika-no-such-program"])
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            CommandModelProvider([])


class RunWithModelCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.source = self.root / "main.kos"
        self.source.write_text(PROGRAM, encoding="utf-8")
        self.input = self.root / "input.json"
        self.input.write_text(
            json.dumps({"ticket": {"text": "I was charged twice."}}), encoding="utf-8"
        )
        self.model = self.root / "model.py"

    def run_with(self, body: str, *extra: str) -> tuple[int, str, str]:
        self.model.write_text(body, encoding="utf-8")
        return invoke(
            "run",
            str(self.source),
            "--input",
            str(self.input),
            "--trace",
            str(self.root / "trace.jsonl"),
            *extra,
            "--model-command",
            sys.executable,
            str(self.model),
        )

    def test_a_thinking_step_runs_and_replays(self) -> None:
        code, _, err = self.run_with(ANSWERING)
        self.assertEqual(code, 0, err)
        self.assertIn("answers for: assistant", err)
        self.assertEqual(
            replay(self.root / "trace.jsonl"), {"queue": "billing", "sure": True}
        )

    def test_an_answer_outside_the_choices_fails_the_run(self) -> None:
        code, _, err = self.run_with(
            'print(\'{"label": "refund", "confidence": 0.9}\')'
        )
        self.assertNotEqual(code, 0)
        self.assertIn("refund", err)

    def test_it_cannot_be_mixed_with_another_model_option(self) -> None:
        code, _, err = self.run_with(ANSWERING, "--model", "local")
        self.assertNotEqual(code, 0)
        self.assertIn("--model-command on its own", err)


if __name__ == "__main__":
    unittest.main()
