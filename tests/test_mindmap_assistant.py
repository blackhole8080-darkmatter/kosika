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
from kosika.assistant import (
    MAX_CONTEXT_BYTES,
    AssistantError,
    KosikaAssistant,
    assistant_context,
    static_summary,
)
from kosika.explain import (
    ExplainError,
    build_explanation,
    to_explanation_outsider,
    to_explanation_text,
)
from kosika.mindmap import build_mindmap, to_json, to_mermaid
from kosika.model import (
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    ModelToolCall,
    ScriptedModelProvider,
)
from kosika.modules import load_program
from kosika.parser import parse
from kosika.runtime import run

ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "examples" / "test_host.kos"
COMPOSED = ROOT / "examples" / "composed" / "main.kos"


class ExplodingProvider:
    def complete(self, _request: ModelRequest) -> ModelResponse:
        raise RuntimeError("synthetic provider failure")


class MindmapAssistantTests(unittest.TestCase):
    def test_mindmap_is_deterministic_and_typed(self) -> None:
        program = parse(SOURCE.read_text(encoding="utf-8"))
        first = build_mindmap(program)
        second = build_mindmap(program)
        self.assertEqual(first, second)
        self.assertEqual(to_json(first), to_json(second))
        mermaid = to_mermaid(first)
        self.assertTrue(mermaid.startswith("flowchart TD\n"))
        self.assertIn("record: DiskStatus", mermaid)
        self.assertIn("workflow: main", mermaid)
        self.assertTrue(any(node["kind"] == "field" for node in first["nodes"]))
        self.assertTrue(
            any(edge["relation"] == "tool_call" for edge in first["edges"])
        )

    def test_imported_modules_and_declarations_keep_boundaries(self) -> None:
        program = load_program(COMPOSED)
        mindmap = build_mindmap(program)
        self.assertEqual(
            [module["path"] for module in mindmap["modules"]],
            ["main.kos", "research.kos", "types.kos"],
        )
        self.assertEqual(
            set(mindmap["modules"][0]["imports"]),
            {"research.kos", "types.kos"},
        )
        owners = {
            node["label"]: node["module"]
            for node in mindmap["nodes"]
            if node["kind"] in {"record", "model", "workflow"}
        }
        self.assertEqual(owners["Question"], "types.kos")
        self.assertEqual(owners["reader"], "research.kos")
        self.assertEqual(owners["main"], "main.kos")
        self.assertTrue(
            any(edge["relation"] == "imports" for edge in mindmap["edges"])
        )

    def test_assistant_fixture_receives_static_context_without_tools(self) -> None:
        provider = ScriptedModelProvider(
            [ModelResponse(content="The workflow calls the research module safely.")]
        )
        result = KosikaAssistant(parse(SOURCE.read_text()), provider).ask(
            "Explain the workflow", diagnostics=("example diagnostic",)
        )
        self.assertEqual(result.mode, "model")
        self.assertEqual(result.answer, "The workflow calls the research module safely.")
        self.assertEqual(len(provider.requests), 1)
        request = provider.requests[0]
        self.assertEqual(request.tools, ())
        self.assertIn("example diagnostic", request.messages[-1].content)
        self.assertIn('"nodes"', request.messages[-1].content)

    def test_assistant_context_accepts_grounded_source_analysis(self) -> None:
        analysis = {
            "schema": "kosika.source-analysis/v1",
            "language": "python",
            "source": {"path": "fixture.py", "sha256": "abc"},
            "symbols": [{"qualified_name": "run"}],
        }
        provider = ScriptedModelProvider([ModelResponse(content="Grounded answer.")])
        result = KosikaAssistant(parse(SOURCE.read_text()), provider).ask(
            "Compare this helper",
            source_analyses=(analysis,),
        )
        self.assertEqual(result.source_analyses, (analysis,))
        context = provider.requests[0].messages[-1].content
        self.assertIn("fixture.py", context)
        self.assertIn("not complete semantics", context)
        self.assertIn("untrusted data", context)

        with self.assertRaisesRegex(AssistantError, "string-keyed objects"):
            KosikaAssistant(parse(SOURCE.read_text())).ask(
                "Explain", source_analyses=(None,)  # type: ignore[arg-type]
            )

    def test_assistant_without_provider_is_offline_and_read_only(self) -> None:
        result = KosikaAssistant(parse(SOURCE.read_text())).ask(
            "Summarize", diagnostics=("Review the model boundary",)
        )
        self.assertEqual(result.mode, "static")
        self.assertEqual(result.diagnostics, ("Review the model boundary",))
        self.assertIn("Static Kosika assistant", result.answer)

    def test_assistant_validates_tasks_diagnostics_and_context_bounds(self) -> None:
        program = parse(SOURCE.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(ValueError, "max_output_tokens"):
            KosikaAssistant(program, max_output_tokens=0)
        assistant = KosikaAssistant(program)
        with self.assertRaisesRegex(AssistantError, "cannot be empty"):
            assistant.ask("   ")
        with self.assertRaisesRegex(AssistantError, "exceeds the 4096"):
            assistant.ask("x" * 4097)
        with self.assertRaisesRegex(AssistantError, "diagnostics must be text"):
            assistant.ask("Explain", diagnostics=(None,))

        context = assistant_context(
            build_mindmap(program), "Explain", diagnostics=("one warning",)
        )
        self.assertIn("one warning", context)
        self.assertIn("Explain", context)
        with self.assertRaisesRegex(AssistantError, "256 KiB safety bound"):
            assistant_context({"padding": "x" * MAX_CONTEXT_BYTES}, "Explain")
        self.assertIn("Kosika code mindmap", static_summary(build_mindmap(program)))

    def test_assistant_static_mode_handles_unstructured_summary(self) -> None:
        program = parse(SOURCE.read_text(encoding="utf-8"))
        with patch(
            "kosika.assistant.build_mindmap",
            return_value={"summary": "not-a-mapping", "modules": "not-a-list"},
        ):
            result = KosikaAssistant(program).ask("Summarize")
        self.assertEqual(result.mode, "static")
        self.assertIn("0 module(s)", result.answer)
        self.assertIn("0 mindmap node(s)", result.answer)

    def test_assistant_rejects_provider_failures_and_empty_text(self) -> None:
        program = parse(SOURCE.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(
            ModelProviderError, "has no response left"
        ):
            KosikaAssistant(program, ScriptedModelProvider([])).ask("Explain")
        with self.assertRaisesRegex(AssistantError, "failed safely"):
            KosikaAssistant(program, ExplodingProvider()).ask("Explain")
        with self.assertRaisesRegex(AssistantError, "returned no text"):
            KosikaAssistant(
                program, ScriptedModelProvider([ModelResponse(content=None)])
            ).ask("Explain")

    def test_assistant_rejects_tools_and_truncation(self) -> None:
        with self.assertRaisesRegex(AssistantError, "cannot request tools"):
            KosikaAssistant(
                parse(SOURCE.read_text()),
                ScriptedModelProvider(
                    [
                        ModelResponse(
                            tool_calls=(ModelToolCall("1", "shell", {}),)
                        )
                    ]
                ),
            ).ask("Explain")
        with self.assertRaisesRegex(AssistantError, "did not complete"):
            KosikaAssistant(
                parse(SOURCE.read_text()),
                ScriptedModelProvider(
                    [ModelResponse(content="partial", finish_reason="length")]
                ),
            ).ask("Explain")

    def test_explain_selects_code_and_validates_trace_offline(self) -> None:
        program = parse(SOURCE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "run.jsonl"
            run(program, "main", trace)
            explanation = build_explanation(
                program,
                workflows=("main",),
                records=("DiskStatus",),
                diagnostics=("Check passed",),
                trace_path=trace,
            )
        self.assertEqual(explanation["selection"]["workflows"], ["main"])
        self.assertEqual(explanation["selection"]["records"], ["DiskStatus"])
        self.assertEqual(explanation["diagnostics"], ["Check passed"])
        self.assertIsNotNone(explanation["trace"])
        self.assertTrue(explanation["trace"]["program_hash_match"])
        self.assertEqual(explanation["trace"]["mode"], "validated_offline")
        self.assertIn("Trace", to_explanation_text(explanation))

    def test_explain_rejects_unknown_selection_and_tampered_trace(self) -> None:
        program = parse(SOURCE.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(ExplainError, "Unknown workflow"):
            build_explanation(program, workflows=("missing",))
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "run.jsonl"
            run(program, "main", trace)
            events = [json.loads(line) for line in trace.read_text().splitlines()]
            events[-1]["result"]["used_percent"] = 99
            trace.write_text(
                "\n".join(json.dumps(event) for event in events), encoding="utf-8"
            )
            with self.assertRaisesRegex(ExplainError, "Trace rejected"):
                build_explanation(program, trace_path=trace)

    def test_cli_explain_json_includes_selection_diagnostics_and_trace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "run.jsonl"
            run(load_program(SOURCE), "main", trace)
            output = io.StringIO()
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "kosika",
                        "explain",
                        str(SOURCE),
                        "--workflow",
                        "main",
                        "--record",
                        "DiskStatus",
                        "--diagnostic",
                        "No mutation requested",
                        "--trace",
                        str(trace),
                        "--format",
                        "json",
                    ],
                ),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(cli.main(), 0)
            response = json.loads(output.getvalue())
            self.assertEqual(response["selection"]["workflows"], ["main"])
            self.assertEqual(response["selection"]["records"], ["DiskStatus"])
            self.assertEqual(response["diagnostics"], ["No mutation requested"])
            self.assertTrue(response["trace"]["program_hash_match"])

    def test_cli_mindmap_and_fixture_assistant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "assistant.json"
            python_source = Path(directory) / "helper.py"
            python_source.write_text("def helper():\n    return 'ok'\n", encoding="utf-8")
            fixture.write_text(
                json.dumps({"responses": ["Fixture explanation."]}),
                encoding="utf-8",
            )
            output = io.StringIO()
            with (
                patch.object(
                    sys, "argv", ["kosika", "mindmap", str(SOURCE), "--format", "mermaid"]
                ),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(cli.main(), 0)
            self.assertIn("flowchart TD", output.getvalue())

            output = io.StringIO()
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "kosika",
                        "assistant",
                        str(SOURCE),
                        "--task",
                        "Explain",
                        "--source-file",
                        str(python_source),
                        "--fixture",
                        str(fixture),
                        "--json",
                    ],
                ),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(cli.main(), 0)
            response = json.loads(output.getvalue())
            self.assertEqual(response["answer"], "Fixture explanation.")
            self.assertEqual(response["mode"], "model")
            self.assertIn("mindmap", response)
            self.assertEqual(len(response["source_analyses"]), 1)
            self.assertEqual(response["source_analyses"][0]["language"], "python")


class OutsiderExplanationTests(unittest.TestCase):
    def test_outsider_view_uses_same_graph_without_type_names(self) -> None:
        program = load_program(COMPOSED)
        explanation = build_explanation(program, workflows=("main",))
        outsider = to_explanation_outsider(explanation)
        technical = to_explanation_text(explanation)
        self.assertIn("What this program does", outsider)
        self.assertIn("checked program structure, not a model guess", outsider)
        self.assertNotIn("Question", outsider)
        self.assertNotIn("Text", outsider)
        # The developer view keeps the same structure with full type detail.
        self.assertIn("Question", technical)
        self.assertIn("Text", technical)

    def test_outsider_view_reports_permissions_and_human_notes(self) -> None:
        program = parse(
            (ROOT / "examples" / "document_assistant.kos").read_text(encoding="utf-8")
        )
        explanation = build_explanation(
            program, diagnostics=("Helps a team find answers.",)
        )
        outsider = to_explanation_outsider(explanation)
        self.assertIn("may use search_docs", outsider)
        self.assertIn("must never use", outsider)
        self.assertIn("Human notes about purpose", outsider)
        self.assertIn("Helps a team find answers.", outsider)

    def test_outsider_view_is_deterministic(self) -> None:
        program = load_program(COMPOSED)
        first = to_explanation_outsider(build_explanation(program))
        second = to_explanation_outsider(build_explanation(program))
        self.assertEqual(first, second)

    def test_cli_outsider_format(self) -> None:
        output = io.StringIO()
        with (
            patch.object(
                sys,
                "argv",
                [
                    "kosika",
                    "explain",
                    str(COMPOSED),
                    "--workflow",
                    "main",
                    "--format",
                    "outsider",
                ],
            ),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(cli.main(), 0)
        self.assertIn("What this program does", output.getvalue())


if __name__ == "__main__":
    unittest.main()
