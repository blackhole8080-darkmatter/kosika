"""Tests for the bounded AI authoring loop.

The loop is hermetic here: the proposal model and the run's model binding are
both scripted providers, so every assertion is about the loop's own behaviour --
which file it patches, what it refuses to do, how it bounds attempts, and what
it hands back to the host.
"""

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
from kosika.authoring import (
    AUTHORING_SCHEMA,
    AuthoringError,
    AuthoringLimits,
    author,
)
from kosika.model import ModelResponse, ModelUsage, ScriptedModelProvider
from kosika.repair import RepairStrategy

ROOT = Path(__file__).resolve().parent.parent
COMPOSED = ROOT / "examples" / "composed"


def block(search: str, replace: str) -> str:
    return f"<<<<<<< SEARCH\n{search}\n=======\n{replace}\n>>>>>>> REPLACE"


class BrokenProject:
    """A two-file project whose *imported* file carries the diagnostic."""

    def __init__(self, directory: str) -> None:
        self.root = Path(directory) / "project"
        self.root.mkdir(parents=True)
        self.types = self.root / "types.kos"
        self.research = self.root / "research.kos"
        self.main = self.root / "main.kos"
        self.inputs = json.loads((COMPOSED / "inputs.json").read_text("utf-8"))
        self.types.write_text(
            "record Question { text: Text }\nrecord Answer { text: Text }\n"
            "export [Question, Answer]\n",
            encoding="utf-8",
        )
        # The failure lives in the imported module, not the entry document, so
        # a loop that only ever looked at `main.kos` could not repair it.
        # The two steps repeat `    return result`, which gives the ambiguity
        # test a line that appears more than once; a patch that quotes it must
        # be refused instead of applied to an arbitrary occurrence.
        self.research.write_text(
            'import "types.kos"\n'
            "model reader { input: Question output: Answer }\n"
            "workflow research(question: Question) -> Answer {\n"
            "  step answer {\n"
            "    let result: Answer = use_model reader(text: question.prompt)\n"
            "    return result\n"
            "  }\n"
            "  step echo {\n"
            "    let result: Answer = use_model reader(text: question.text)\n"
            "    return result\n"
            "  }\n"
            "}\n"
            "export [research]\n",
            encoding="utf-8",
        )
        self.main.write_text(
            'import "types.kos"\n'
            'import "research.kos"\n'
            "workflow main(question: Question) -> Answer {\n"
            "  step research {\n"
            "    let answer: Answer = call research(question: question)\n"
            "    return answer\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )

    @property
    def prompt(self) -> str:
        return (
            "The program uses an unknown record field, so it does not check.\n"
            "Replace `question.prompt` with `question.text`."
        )

    def repair_answer(self, current: str = "question.prompt") -> str:
        # Search text is matched as whole lines, so a real patch has to quote
        # the complete line it changes rather than the offending fragment.
        return block(
            f"    let result: Answer = use_model reader(text: {current})",
            "    let result: Answer = use_model reader(text: question.text)",
        )

    def ambiguous_answer(self) -> str:
        # `    return result` closes both steps, so the search text is ambiguous
        # and applying it would corrupt the program.
        return block("    return result", "    return result\n    return result")


class AuthoringLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.project = BrokenProject(self._directory.name)

    def run_loop(self, provider, **kwargs):
        # The research workflow makes one model call per step, so the host
        # binding is scripted for both.
        params = {
            "intent": "Make the research workflow check cleanly",
            "provider": provider,
            "root": self.project.root,
            "inputs": self.project.inputs,
            "model_bindings": {
                "reader": ScriptedModelProvider(
                    [
                        ModelResponse(content=json.dumps({"text": "Bound"})),
                        ModelResponse(content=json.dumps({"text": "Bound"})),
                    ]
                )
            },
            "execute": True,
        }
        params.update(kwargs)
        return author(self.project.main, **params)

    def test_clean_project_runs_without_calling_the_model(self) -> None:
        self.project.research.write_text(
            self.project.research.read_text("utf-8").replace(
                "question.prompt", "question.text"
            ),
            encoding="utf-8",
        )
        provider = ScriptedModelProvider([])

        response = self.run_loop(provider, execute=True)

        self.assertTrue(response["ok"], response)
        self.assertEqual(response["schema"], AUTHORING_SCHEMA)
        self.assertEqual(response["status"], "ok")
        self.assertEqual(provider.requests, [])
        self.assertEqual(response["attempts"], [])
        self.assertEqual(response["proposals"], {})
        self.assertEqual(response["execution"]["result"]["value"], {"text": "Bound"})

    def test_loop_repairs_the_failing_imported_file_and_returns_a_proposal(
        self,
    ) -> None:
        provider = ScriptedModelProvider(
            [
                ModelResponse(
                    content=self.project.repair_answer(), usage=ModelUsage(1, 2, 3)
                )
            ]
        )

        response = self.run_loop(provider)

        self.assertTrue(response["ok"], response)
        self.assertEqual(response["status"], "ok")
        self.assertEqual(len(provider.requests), 1)
        attempt = response["attempts"][0]
        self.assertEqual(attempt["path"], "research.kos")
        self.assertEqual(attempt["code"], "check/field-access")
        self.assertTrue(attempt["applied"])
        self.assertTrue(attempt["model_called"])
        self.assertEqual(attempt["prompt_tokens"], 1)
        self.assertEqual(attempt["completion_tokens"], 2)
        self.assertGreaterEqual(attempt["seconds"], 0.0)
        # Only the file the diagnostic came from is proposed, and the entry
        # document is untouched because nothing failed in it.
        self.assertEqual(list(response["proposals"]), ["research.kos"])
        self.assertIn("question.text", response["proposals"]["research.kos"])
        self.assertNotIn("question.prompt", response["proposals"]["research.kos"])
        self.assertTrue(response["diagnostics"]["ok"])
        self.assertEqual(response["execution"]["result"]["value"], {"text": "Bound"})
        self.assertEqual(
            response["usage"],
            {"model_calls": 1, "prompt_tokens": 1, "completion_tokens": 2},
        )

    def test_proposals_are_never_written_to_disk(self) -> None:
        before = self.project.research.read_text("utf-8")
        provider = ScriptedModelProvider(
            [ModelResponse(content=self.project.repair_answer())]
        )

        response = self.run_loop(provider, execute=False)

        self.assertTrue(response["ok"], response)
        self.assertEqual(self.project.research.read_text("utf-8"), before)
        # Without execution there is nothing observed, so no trace is claimed.
        self.assertIsNone(response["execution"])
        self.assertIsNone(response["trace"])

    def test_completed_run_is_inspected_through_the_trace(self) -> None:
        trace = Path(self._directory.name) / "trace.jsonl"
        provider = ScriptedModelProvider(
            [ModelResponse(content=self.project.repair_answer())]
        )

        response = self.run_loop(provider, trace_path=trace)

        self.assertTrue(response["ok"], response)
        self.assertTrue(trace.exists())
        assert response["trace"] is not None
        self.assertTrue(response["trace"]["ok"], response["trace"])
        self.assertEqual(response["trace"]["result"]["result"], {"text": "Bound"})
        self.assertEqual(response["trace"]["result"]["trace_version"], 5)

    def test_missing_host_run_authority_is_reported_not_minted(self) -> None:
        provider = ScriptedModelProvider(
            [ModelResponse(content=self.project.repair_answer())]
        )

        response = self.run_loop(provider, model_bindings=None)

        # The repair itself is valid; the run fails because the loop holds no
        # authority to grant the declared model.
        self.assertEqual(response["status"], "repaired")
        self.assertFalse(response["ok"])
        self.assertEqual(response["execution"]["error"]["code"], "runtime/error")
        self.assertIn(
            "authorized host binding", response["execution"]["error"]["message"]
        )
        self.assertIn("question.text", response["proposals"]["research.kos"])

    def test_attempt_budget_bounds_provider_calls(self) -> None:
        provider = ScriptedModelProvider(
            [
                ModelResponse(content=block("not in the program", "question.text")),
                ModelResponse(content=block("still not present", "question.text")),
            ]
        )

        response = self.run_loop(provider, limits=AuthoringLimits(max_attempts=2))

        self.assertFalse(response["ok"])
        self.assertEqual(response["status"], "exhausted")
        self.assertEqual(response["error"]["code"], "authoring/uncheckable")
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(len(response["attempts"]), 2)
        self.assertEqual(
            response["attempts"][0]["error"],
            "Search text does not appear in the program",
        )
        self.assertEqual(response["proposals"], {})
        self.assertIsNone(response["execution"])

    def test_ambiguous_search_text_is_rejected_and_the_failure_is_fed_back(
        self,
    ) -> None:
        provider = ScriptedModelProvider(
            [
                ModelResponse(content=self.project.ambiguous_answer()),
                ModelResponse(content=self.project.repair_answer()),
            ]
        )

        response = self.run_loop(provider, limits=AuthoringLimits(max_attempts=2))

        self.assertTrue(response["ok"], response)
        self.assertFalse(response["attempts"][0]["applied"])
        self.assertIn("times", response["attempts"][0]["error"])
        # The rejected answer is reported to the model instead of being repeated
        # silently, which is what lets a second attempt converge.
        retry = provider.requests[1].messages[-1].content
        self.assertIn("Attempts that did not work", retry)
        self.assertIn("question.prompt", provider.requests[0].messages[-1].content)

    def test_response_without_a_patch_is_a_recorded_format_failure(self) -> None:
        provider = ScriptedModelProvider(
            [ModelResponse(content="I think you should rename the field.")]
        )

        response = self.run_loop(provider, limits=AuthoringLimits(max_attempts=1))

        self.assertFalse(response["ok"])
        self.assertEqual(
            response["attempts"][0]["error"], "Response contained no replacement block"
        )
        self.assertEqual(response["usage"]["model_calls"], 1)

    def test_provider_error_stops_the_loop_without_retrying(self) -> None:
        class Failing(ScriptedModelProvider):
            def complete(self, request):  # type: ignore[override]
                raise RuntimeError("endpoint unavailable")

        provider = Failing([])

        response = self.run_loop(provider, limits=AuthoringLimits(max_attempts=3))

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "authoring/provider")
        self.assertIn("endpoint unavailable", response["attempts"][0]["error"])
        self.assertEqual(len(response["attempts"]), 1)
        # The failed request is still counted: it was made and it cost spend.
        self.assertEqual(response["usage"]["model_calls"], 1)

    def test_reasoning_only_answer_is_named_as_configuration(self) -> None:
        provider = ScriptedModelProvider(
            [ModelResponse(content=None, reasoning="thinking about the field")]
        )

        response = self.run_loop(provider, limits=AuthoringLimits(max_attempts=3))

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "authoring/no-answer")
        self.assertIn("disable thinking", response["attempts"][0]["error"])
        self.assertEqual(len(response["attempts"]), 1)

    def test_missing_project_file_is_reported_without_calling_the_model(self) -> None:
        self.project.research.unlink()
        provider = ScriptedModelProvider([])

        response = self.run_loop(provider)

        self.assertFalse(response["ok"])
        self.assertEqual(provider.requests, [])
        self.assertTrue(response["diagnostics"]["diagnostics"])

    def test_out_of_project_diagnostic_is_unmapped_not_repaired(self) -> None:
        entry = self.project.main
        diagnostic = {"code": "check/field-access", "path": str(ROOT / "escape.kos")}

        from kosika.authoring import _resolve_diagnostic_path

        self.assertIsNone(
            _resolve_diagnostic_path(entry, diagnostic, self.project.root)
        )

    def test_intent_is_required_and_limits_are_validated_before_any_call(self) -> None:
        provider = ScriptedModelProvider([])
        with self.assertRaises(AuthoringError):
            self.run_loop(provider, intent="   ")
        for limits in (
            AuthoringLimits(max_attempts=0),
            AuthoringLimits(max_output_tokens=0),
            AuthoringLimits(max_context_lines=0),
        ):
            with self.assertRaises(AuthoringError):
                self.run_loop(provider, limits=limits)
        self.assertEqual(provider.requests, [])

    def test_a_broken_project_without_a_proposal_authority_fails_closed(self) -> None:
        response = author(
            self.project.main,
            intent="no proposing model",
            provider=None,
            root=self.project.root,
            execute=False,
        )

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "authoring/no-provider")
        self.assertEqual(response["proposals"], {})
        self.assertEqual(response["attempts"][0]["code"], "check/field-access")

    def test_a_clean_project_needs_no_proposal_authority(self) -> None:
        self.project.research.write_text(
            self.project.research.read_text("utf-8").replace(
                "question.prompt", "question.text"
            ),
            encoding="utf-8",
        )

        response = author(
            self.project.main,
            intent="already checks",
            provider=None,
            root=self.project.root,
            execute=False,
        )

        self.assertTrue(response["ok"], response)
        self.assertEqual(response["attempts"], [])

    def test_full_strategy_shows_the_whole_failing_file(self) -> None:
        provider = ScriptedModelProvider(
            [ModelResponse(content=self.project.repair_answer())]
        )

        response = self.run_loop(
            provider, limits=AuthoringLimits(strategy=RepairStrategy.FULL)
        )

        self.assertTrue(response["ok"], response)
        shown = provider.requests[0].messages[-1].content
        self.assertIn("complete program", shown)
        self.assertIn("workflow research", shown)

    def test_proposal_prompt_names_the_file_and_the_diagnostic(self) -> None:
        provider = ScriptedModelProvider(
            [ModelResponse(content=self.project.repair_answer())]
        )

        self.run_loop(provider)

        prompt = provider.requests[0].messages[-1].content
        self.assertIn("research.kos", prompt)
        self.assertIn("check/field-access", prompt)
        self.assertIn("question.prompt", prompt)

    def test_a_patch_that_applies_but_does_not_converge_is_fed_back(self) -> None:
        wrong = block(
            "    let result: Answer = use_model reader(text: question.prompt)",
            "    let result: Answer = use_model reader(text: question.missing)",
        )
        provider = ScriptedModelProvider(
            [
                ModelResponse(content=wrong),
                ModelResponse(content=self.project.repair_answer("question.missing")),
            ]
        )

        response = self.run_loop(provider, limits=AuthoringLimits(max_attempts=2))

        self.assertTrue(response["ok"], response)
        self.assertTrue(response["attempts"][0]["applied"])
        self.assertEqual(response["attempts"][0]["remaining"], ["check/field-access"])
        retry = provider.requests[1].messages[-1].content
        self.assertIn("still failed with check/field-access", retry)

    def test_host_source_overrides_are_read_and_returned(self) -> None:
        corrected = self.project.research.read_text("utf-8").replace(
            "question.prompt", "question.text"
        )
        provider = ScriptedModelProvider([])

        response = self.run_loop(
            provider,
            execute=False,
            source_overrides={self.project.research: corrected},
        )

        self.assertTrue(response["ok"], response)
        self.assertEqual(provider.requests, [])
        # The host's own in-memory state is part of what was checked, so it is
        # part of what comes back for review.
        self.assertEqual(response["proposals"], {"research.kos": corrected})

    def test_out_of_project_diagnostic_stops_the_loop(self) -> None:
        outside = ROOT / "examples" / "hello_world.kos"
        report = {
            "schema": "kosika.diagnostics/v1",
            "path": str(self.project.main),
            "ok": False,
            "error_count": 1,
            "codes": ["check/field-access"],
            "diagnostics": [
                {
                    "code": "check/field-access",
                    "severity": 1,
                    "message": "outside the project",
                    "range": None,
                    "path": str(outside),
                    "owner": None,
                    "owner_kind": None,
                    "data": {},
                }
            ],
        }
        provider = ScriptedModelProvider([])

        with patch(
            "kosika.authoring.check_project",
            return_value={
                "schema": "kosika.development/v1",
                "operation": "check",
                "ok": False,
                "project": None,
                "result": None,
                "diagnostics": report,
                "error": None,
            },
        ):
            response = self.run_loop(provider)

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "authoring/unmapped")
        self.assertIsNone(response["attempts"][0]["path"])
        self.assertEqual(provider.requests, [])
        self.assertEqual(response["usage"]["model_calls"], 0)
        self.assertEqual(response["proposals"], {})


class AuthoringCommandTests(unittest.TestCase):
    """The CLI is a host surface: it holds no authority it was not given."""

    def call_cli(self, *arguments: str) -> tuple[int, str]:
        output = io.StringIO()
        with (
            patch.object(sys, "argv", ["kosika", *arguments]),
            contextlib.redirect_stdout(output),
        ):
            result = cli.main()
        return result, output.getvalue()

    def call_cli_error(self, *arguments: str) -> tuple[int, str]:
        output = io.StringIO()
        with (
            patch.object(sys, "argv", ["kosika", *arguments]),
            contextlib.redirect_stderr(output),
        ):
            result = cli.main()
        return result, output.getvalue()

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.project = BrokenProject(self._directory.name)
        self.fixture = Path(self._directory.name) / "proposal.json"
        self.fixture.write_text(
            json.dumps({"responses": [self.project.repair_answer()]}),
            encoding="utf-8",
        )
        self.model_fixture = Path(self._directory.name) / "models.json"
        self.model_fixture.write_text(
            json.dumps(
                {
                    "reader": [
                        {"text": "Bound"},
                        {"text": "Bound"},
                    ]
                }
            ),
            encoding="utf-8",
        )

    def author_arguments(self, *extra: str) -> tuple[str, ...]:
        return (
            "author",
            str(self.project.main),
            "--intent",
            "Make the research workflow check cleanly",
            "--root",
            str(self.project.root),
            "--proposal-fixture",
            str(self.fixture),
            "--model-fixture",
            str(self.model_fixture),
            "--input",
            json.dumps(self.project.inputs),
            *extra,
        )

    def test_command_reports_a_repair_and_never_writes_it(self) -> None:
        before = self.project.research.read_text("utf-8")

        result, output = self.call_cli(*self.author_arguments())

        self.assertEqual(result, 0, output)
        self.assertIn("status      : ok", output)
        self.assertIn("proposed    : research.kos (not written to disk)", output)
        self.assertIn('run         : {"text": "Bound"}', output)
        self.assertEqual(self.project.research.read_text("utf-8"), before)

    def test_command_json_report_is_the_loop_envelope(self) -> None:
        result, output = self.call_cli(*self.author_arguments("--format", "json"))

        self.assertEqual(result, 0, output)
        payload = json.loads(output)
        self.assertEqual(payload["schema"], AUTHORING_SCHEMA)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["usage"]["model_calls"], 1)
        self.assertIn("research.kos", payload["proposals"])

    def test_command_no_run_skips_execution_and_bindings(self) -> None:
        result, output = self.call_cli(
            "author",
            str(self.project.main),
            "--intent",
            "check only",
            "--root",
            str(self.project.root),
            "--proposal-fixture",
            str(self.fixture),
            "--no-run",
            "--format",
            "json",
        )

        self.assertEqual(result, 0, output)
        payload = json.loads(output)
        self.assertIsNone(payload["execution"])

    def test_command_trace_is_written_and_inspected(self) -> None:
        trace = Path(self._directory.name) / "trace.jsonl"

        result, output = self.call_cli(*self.author_arguments("--trace", str(trace)))

        self.assertEqual(result, 0, output)
        self.assertTrue(trace.exists())

    def test_command_without_a_proposal_authority_checks_a_clean_project(self) -> None:
        self.project.research.write_text(
            self.project.research.read_text("utf-8").replace(
                "question.prompt", "question.text"
            ),
            encoding="utf-8",
        )

        result, output = self.call_cli(
            "author",
            str(self.project.main),
            "--intent",
            "already checks",
            "--root",
            str(self.project.root),
            "--no-run",
        )

        self.assertEqual(result, 0, output)
        self.assertIn("status      : ok", output)

    def test_command_without_a_proposal_authority_cannot_claim_a_repair(self) -> None:
        result, output = self.call_cli(
            "author",
            str(self.project.main),
            "--intent",
            "no proposing model",
            "--root",
            str(self.project.root),
            "--no-run",
        )

        self.assertEqual(result, 1, output)
        self.assertIn("status      : exhausted", output)

    def test_command_rejects_conflicting_proposal_authority(self) -> None:
        result, output = self.call_cli_error(
            *self.author_arguments("--proposal-model", "some-model")
        )

        self.assertEqual(result, 2)
        self.assertIn("not both", output)

    def test_command_reports_an_unrepaired_project_with_a_nonzero_exit(self) -> None:
        self.fixture.write_text(
            json.dumps({"responses": [block("absent line", "other line")]}),
            encoding="utf-8",
        )

        result, output = self.call_cli(*self.author_arguments("--no-run"))

        self.assertEqual(result, 1)
        self.assertIn("status      : exhausted", output)
        self.assertNotIn("proposed", output)

    def test_command_rejects_a_non_object_input(self) -> None:
        result, output = self.call_cli_error(
            *self.author_arguments("--input", "[1, 2]")
        )

        self.assertEqual(result, 2)
        self.assertIn("JSON object", output)


if __name__ == "__main__":
    unittest.main()
