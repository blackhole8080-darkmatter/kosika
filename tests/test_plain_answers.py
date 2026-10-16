"""`output: R as text`: a model answers in plain text, which fills R's one
Text field. A small model writes a whole file well; the same file escaped
inside a JSON string, it often cannot."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kosika.checker import CheckError, check
from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.model_steps import unfenced
from kosika.parser import parse
from kosika.runtime import replay, run

SOURCE = """
record Ask { text: Text }
record Code { text: Text }

model writer {
  input: Ask
  output: Code as text
}

workflow main(a: Ask) -> Code {
  code = use_model writer(a.text)
}
"""


class PlainAnswerTests(unittest.TestCase):
    def test_a_fenced_file_is_written_as_itself(self) -> None:
        writer = ScriptedModelProvider(
            [ModelResponse(content='Here it is:\n```python\ndef f():\n    return "x"\n```\n')]
        )
        with tempfile.TemporaryDirectory() as folder:
            trace = Path(folder) / "trace.jsonl"
            result = run(
                parse(SOURCE),
                "main",
                trace,
                inputs={"a": {"text": "write f"}},
                model_bindings={"writer": writer},
            )
            self.assertEqual(result["text"], 'def f():\n    return "x"')
            self.assertEqual(replay(trace), result)
        request = writer.requests[0]
        # Asked in plain words, and not held to JSON.
        self.assertEqual(request.messages[1].content, "write f")
        self.assertFalse(request.json_object)

    def test_text_without_a_fence_is_taken_whole(self) -> None:
        self.assertEqual(unfenced("  just words \n"), "just words")
        self.assertEqual(unfenced("```\na\n```"), "a")
        two = "```\na\n```\nand\n```\nb\n```"
        self.assertEqual(unfenced(two), two)

    def test_only_a_record_of_one_text_can_be_answered_as_text(self) -> None:
        bad = SOURCE.replace("record Code { text: Text }", "record Code { text: Text lines: Number }")
        with self.assertRaisesRegex(CheckError, "exactly one field, of type Text"):
            check(parse(bad))


if __name__ == "__main__":
    unittest.main()
