"""Untrusted transcript shape errors must stay at the validation boundary."""

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from examples import document_assistant
from examples.document_assistant import DocumentFixture, run_assistant
from kosika.cli import main
from kosika.local_documents import LocalDocuments
from kosika.runtime import replay


class AgentReplayValidationTests(unittest.TestCase):
    def test_malformed_transcripts_rejected_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            docs = Path(document_assistant.__file__).parent / "documents"
            run_assistant("approval", docs, trace)
            original = [json.loads(line) for line in trace.read_text().splitlines()]
            cases = [
                ((2, "event"), []),
                ((2, "event"), {}),
                ((2, "max_steps"), True),
                ((2, "max_steps"), 0),
                ((2, "max_steps"), 65),
                ((2, "tools", "search_docs"), None),
                ((3, "model_steps"), True),
                ((3, "model_steps"), 3),
                ((3, "messages"), []),
                ((3, "messages", 0), None),
                ((3, "messages", 0, "role"), "tool"),
                ((3, "messages", 1, "content"), {"text": "approval"}),
                ((3, "messages", 1, "content"), "null"),
                ((3, "messages", 2, "tool_calls"), None),
                ((3, "messages", 2, "tool_calls", 0, "name"), "shell"),
                ((3, "messages", 2, "tool_calls", 0, "id"), ""),
                ((3, "messages", 2, "tool_calls", 0, "arguments"), {"query": 1}),
                ((3, "messages", 3, "content"), None),
                ((3, "messages", 3, "content"), '{"hits":false}'),
                ((3, "messages", 3, "tool_call_id"), "mismatch"),
                ((3, "messages", 4, "content"), "{}"),
            ]
            with (
                patch.object(
                    DocumentFixture,
                    "complete",
                    side_effect=AssertionError("model called"),
                ),
                patch.object(
                    LocalDocuments,
                    "__init__",
                    side_effect=AssertionError("documents opened"),
                ),
            ):
                for path, value in cases:
                    with self.subTest(path=path, value=value):
                        events = copy.deepcopy(original)
                        target = events
                        for key in path[:-1]:
                            target = target[key]
                        target[path[-1]] = value
                        trace.write_text(
                            "\n".join(json.dumps(event) for event in events),
                            encoding="utf-8",
                        )
                        with self.assertRaises(ValueError):
                            replay(trace)
                        error = io.StringIO()
                        with (
                            redirect_stderr(error),
                            patch("sys.argv", ["kosika", "replay", str(trace)]),
                        ):
                            self.assertEqual(main(), 2)
                        self.assertIn("Replay rejected:", error.getvalue())


if __name__ == "__main__":
    unittest.main()
