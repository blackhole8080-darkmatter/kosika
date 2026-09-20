from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from examples.document_assistant import DocumentFixture, run_assistant
from kosika.agent import AgentLoopError
from kosika.checker import CheckError, check
from kosika.local_documents import LocalDocuments
from kosika.model import ModelResponse, ModelToolCall, ScriptedModelProvider
from kosika.parser import ParseError, parse
from kosika.runtime import replay, run

ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "examples" / "document_assistant.syn"
DOCS = ROOT / "examples" / "documents"


class DocumentAssistantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.trace = Path(self.directory.name) / "run.jsonl"
        self.source = SOURCE.read_text(encoding="utf-8")

    def test_document_search_agent_and_offline_replay(self) -> None:
        answer = run_assistant("approval", DOCS, self.trace)
        self.assertIn("human approval", answer["text"])
        self.assertEqual(answer["sources"], ["safety.md"])
        with (
            patch.object(
                DocumentFixture, "complete", side_effect=AssertionError("offline")
            ),
            patch.object(
                LocalDocuments, "search", side_effect=AssertionError("offline")
            ),
        ):
            self.assertEqual(replay(self.trace), answer)
        events = [json.loads(line) for line in self.trace.read_text().splitlines()]
        self.assertEqual(events[3]["model_steps"], 2)
        self.assertEqual(events[3]["messages"][3]["role"], "tool")

    def test_no_matches_are_explicit(self) -> None:
        self.assertEqual(
            run_assistant("xyzunmatched", DOCS, self.trace),
            {
                "text": "No matching documents found.",
                "sources": [],
            },
        )

    def test_unknown_model_tool_and_unretrieved_citation_fail_closed(self) -> None:
        with self.assertRaisesRegex(AgentLoopError, "undeclared tool"):
            run_assistant(
                "approval",
                DOCS,
                self.trace,
                ScriptedModelProvider(
                    [ModelResponse(tool_calls=(ModelToolCall("1", "shell", {}),))]
                ),
            )
        with self.assertRaisesRegex(ValueError, "not retrieved"):
            run_assistant(
                "approval",
                DOCS,
                self.trace,
                ScriptedModelProvider(
                    [
                        ModelResponse(
                            content=json.dumps(
                                {"text": "fake", "sources": ["outside.txt"]}
                            )
                        )
                    ]
                ),
            )
        self.assertFalse(self.trace.exists())

    def test_ask_checks_types_models_and_limits(self) -> None:
        for source in (
            self.source.replace("model: reader", "model: missing"),
            self.source.replace("model: reader", ""),
            self.source.replace("ask researcher", "ask unknown"),
            self.source.replace("max_steps: 4", "max_steps: 0"),
            self.source.replace("max_steps: 4", "max_steps: 65"),
            self.source.replace("text: question.text", "text: 7"),
        ):
            with self.subTest(source=source), self.assertRaises(CheckError):
                check(parse(source))
        for value in ("1.5", "unlimited"):
            with self.assertRaises(ParseError):
                parse(self.source.replace("max_steps: 4", f"max_steps: {value}"))

    def test_missing_bindings_and_mutations_prevent_model_calls(self) -> None:
        provider = ScriptedModelProvider([])
        program = parse(self.source)
        for bindings in ({}, {"reader": provider}):
            with self.assertRaises(PermissionError):
                run(
                    program,
                    "answer",
                    self.trace,
                    inputs={"question": {"text": "approval"}},
                    model_bindings=bindings,
                )
        with self.assertRaisesRegex(PermissionError, "observe"):
            run(
                parse(self.source.replace("access: observe", "access: execute")),
                "answer",
                self.trace,
                inputs={"question": {"text": "approval"}},
                model_bindings={"reader": provider},
                tool_bindings={"search_docs": lambda tool, args: {}},
            )
        self.assertEqual(provider.requests, [])

    def test_step_limit_and_invalid_answer(self) -> None:
        responses = [
            ModelResponse(
                tool_calls=(
                    ModelToolCall(str(i), "search_docs", {"query": "approval"}),
                )
            )
            for i in range(4)
        ]
        with self.assertRaisesRegex(AgentLoopError, "maximum model steps"):
            run_assistant(
                "approval", DOCS, self.trace, ScriptedModelProvider(responses)
            )
        with self.assertRaises(ValueError):
            run_assistant(
                "approval",
                DOCS,
                self.trace,
                ScriptedModelProvider(
                    [ModelResponse(content='{"text":7,"sources":[]}')]
                ),
            )
        with self.assertRaisesRegex(AgentLoopError, "complete final"):
            run_assistant(
                "approval",
                DOCS,
                self.trace,
                ScriptedModelProvider(
                    [
                        ModelResponse(
                            content='{"text":"x","sources":[]}', finish_reason="length"
                        )
                    ]
                ),
            )
        self.assertFalse(self.trace.exists())

    def test_transcript_tampering_is_rejected(self) -> None:
        run_assistant("approval", DOCS, self.trace)
        events = [json.loads(line) for line in self.trace.read_text().splitlines()]
        events[3]["messages"][3]["tool_call_id"] = "different"
        self.trace.write_text(
            "\n".join(json.dumps(event) for event in events), encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "tool result mismatch"):
            replay(self.trace)

    def test_document_root_bounds_and_snapshot(self) -> None:
        root = Path(self.directory.name) / "docs"
        root.mkdir()
        file = root / "note.md"
        file.write_text("approval uses a ledger", encoding="utf-8")
        documents = LocalDocuments(root)
        file.write_text("changed", encoding="utf-8")
        self.assertEqual(
            documents.search({"query": "approval"})["hits"][0]["source"], "note.md"
        )
        self.assertEqual(documents.search({"query": "../../private.txt"}), {"hits": []})
        for query in ("", "!", "x" * 257):
            with self.assertRaises(ValueError):
                documents.search({"query": query})
        with (
            patch.object(Path, "is_symlink", return_value=True),
            self.assertRaises(PermissionError),
        ):
            LocalDocuments(root)
        file.write_bytes(b"x" * 65537)
        with self.assertRaisesRegex(ValueError, "64 KiB"):
            LocalDocuments(root)


if __name__ == "__main__":
    unittest.main()
