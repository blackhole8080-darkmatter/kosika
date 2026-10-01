from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kosika.development import (
    DEVELOPMENT_SCHEMA,
    check_project,
    execute_development_json,
    execute_development_request,
    inspect_project,
    inspect_trace,
    map_project,
    run_project,
)
from kosika.model import ModelResponse, ScriptedModelProvider

ROOT = Path(__file__).resolve().parent.parent
HELLO = ROOT / "examples" / "hello_world.kos"
COMPOSED = ROOT / "examples" / "composed" / "main.kos"


class DevelopmentInterfaceTests(unittest.TestCase):
    def test_inspection_reports_checked_module_graph_and_workflow_contracts(
        self,
    ) -> None:
        response = inspect_project(COMPOSED, root=COMPOSED.parent)

        self.assertTrue(response["ok"], response)
        self.assertEqual(response["schema"], DEVELOPMENT_SCHEMA)
        self.assertEqual(response["operation"], "inspect")
        inventory = response["result"]
        self.assertEqual(inventory["language_contract"], "kosika-language/0.1.0")
        self.assertEqual(
            [module["path"] for module in inventory["modules"]],
            ["main.kos", "research.kos", "types.kos"],
        )
        self.assertEqual(inventory["declarations"]["record"], ["Answer", "Question"])
        workflows = {item["name"]: item for item in inventory["workflows"]}
        self.assertEqual(set(workflows), {"main", "research"})
        self.assertEqual(workflows["main"]["input_schema"]["required"], ["question"])
        self.assertFalse(workflows["main"]["input_schema"]["additionalProperties"])

    def test_check_failure_embeds_k04_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "broken.kos"
            source.write_text("record Broken { value Text }\n", encoding="utf-8")
            response = check_project(source)

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "project/diagnostics")
        diagnostics = response["diagnostics"]
        self.assertEqual(diagnostics["schema"], "kosika.diagnostics/v1")
        self.assertEqual(diagnostics["codes"], ["parse/expected-token"])
        self.assertEqual(diagnostics["diagnostics"][0]["data"]["expected"], ":")
        self.assertEqual(diagnostics["diagnostics"][0]["data"]["actual"], "Text")

    def test_bounded_run_and_trace_inspection_share_observed_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "run.jsonl"
            run_response = run_project(HELLO, trace_path=trace)
            trace_response = inspect_trace(trace)

        self.assertTrue(run_response["ok"], run_response)
        observed = run_response["result"]
        self.assertEqual(observed["value"], {"text": "Hello, world!"})
        self.assertEqual(observed["events"][0]["event"], "run_start")
        self.assertEqual(observed["events"][-1]["event"], "run_complete")
        self.assertEqual(observed["execution"]["used"]["steps"], 1)
        self.assertTrue(trace_response["ok"], trace_response)
        self.assertEqual(trace_response["result"]["result"], observed["value"])
        self.assertEqual(trace_response["result"]["trace_version"], 5)
        self.assertEqual(
            trace_response["result"]["event_count"], len(observed["events"])
        )
        self.assertEqual(trace_response["result"]["mode"], "exact_replay")

    def test_run_uses_only_explicit_host_model_bindings(self) -> None:
        inputs = json.loads(
            (ROOT / "examples" / "composed" / "inputs.json").read_text("utf-8")
        )
        provider = ScriptedModelProvider(
            [ModelResponse(content=json.dumps({"text": "Bound response"}))]
        )

        response = run_project(
            COMPOSED,
            root=COMPOSED.parent,
            inputs=inputs,
            model_bindings={"reader": provider},
        )

        self.assertTrue(response["ok"], response)
        self.assertEqual(response["result"]["value"], {"text": "Bound response"})
        self.assertEqual(len(provider.requests), 1)

    def test_run_reports_missing_host_authority_without_losing_events(self) -> None:
        inputs = json.loads(
            (ROOT / "examples" / "composed" / "inputs.json").read_text("utf-8")
        )

        response = run_project(
            COMPOSED,
            root=COMPOSED.parent,
            inputs=inputs,
        )

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "runtime/error")
        self.assertIn("authorized host binding", response["error"]["message"])
        self.assertEqual(response["result"]["events"][-1]["event"], "run_failed")

    def test_map_is_checked_deterministic_structure(self) -> None:
        first = map_project(COMPOSED, root=COMPOSED.parent)
        second = map_project(COMPOSED, root=COMPOSED.parent)

        self.assertTrue(first["ok"], first)
        self.assertEqual(first["result"]["digest"], second["result"]["digest"])
        self.assertEqual(first["result"]["version"], 2)
        self.assertEqual(first["result"]["summary"]["workflow_count"], 2)
        self.assertEqual(
            first["result"]["files"],
            ["main.kos", "research.kos", "types.kos"],
        )

    def test_request_contract_rejects_unknown_fields_and_executes_run(self) -> None:
        invalid = execute_development_request(
            {
                "schema": DEVELOPMENT_SCHEMA,
                "operation": "inspect",
                "source": str(HELLO),
                "surprise": True,
            }
        )
        executed = execute_development_request(
            {
                "schema": DEVELOPMENT_SCHEMA,
                "operation": "run",
                "source": str(HELLO),
                "workflow": "main",
                "inputs": {},
                "limits": {"max_steps": 2},
            }
        )

        self.assertFalse(invalid["ok"])
        self.assertEqual(invalid["error"]["code"], "request/invalid")
        self.assertTrue(executed["ok"], executed)
        self.assertEqual(executed["result"]["value"], {"text": "Hello, world!"})
        self.assertEqual(executed["result"]["limits"]["max_steps"], 2)

    def test_request_dispatches_read_operations_through_authoritative_results(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "run.jsonl"
            self.assertTrue(run_project(HELLO, trace_path=trace)["ok"])
            requests = (
                {
                    "schema": DEVELOPMENT_SCHEMA,
                    "operation": "inspect",
                    "source": str(COMPOSED),
                    "root": str(COMPOSED.parent),
                },
                {
                    "schema": DEVELOPMENT_SCHEMA,
                    "operation": "check",
                    "source": str(COMPOSED),
                    "root": str(COMPOSED.parent),
                },
                {
                    "schema": DEVELOPMENT_SCHEMA,
                    "operation": "map",
                    "source": str(COMPOSED),
                    "root": str(COMPOSED.parent),
                },
                {
                    "schema": DEVELOPMENT_SCHEMA,
                    "operation": "trace",
                    "trace": str(trace),
                },
            )
            responses = [execute_development_request(item) for item in requests]

        self.assertEqual(
            [response["operation"] for response in responses],
            ["inspect", "check", "map", "trace"],
        )
        self.assertTrue(all(response["ok"] for response in responses), responses)
        self.assertEqual(responses[1]["diagnostics"]["error_count"], 0)
        self.assertEqual(responses[2]["result"]["version"], 2)
        self.assertEqual(
            responses[3]["result"]["result"],
            {"text": "Hello, world!"},
        )

    def test_invalid_projects_and_traces_return_structured_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broken = Path(directory) / "broken.kos"
            broken.write_text("record Broken { value Text }\n", encoding="utf-8")
            invalid_trace = Path(directory) / "trace.jsonl"
            invalid_trace.write_text("{}\n", encoding="utf-8")
            project_responses = (
                inspect_project(broken),
                map_project(broken),
                run_project(broken),
            )
            trace_response = inspect_trace(invalid_trace)

        for response in project_responses:
            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "project/diagnostics")
            self.assertEqual(
                response["diagnostics"]["codes"],
                ["parse/expected-token"],
            )
        self.assertFalse(trace_response["ok"])
        self.assertEqual(trace_response["error"]["code"], "trace/invalid")

    def test_request_validation_returns_stable_machine_errors(self) -> None:
        invalid_requests = (
            None,
            {"schema": "old", "operation": "inspect", "source": str(HELLO)},
            {"schema": DEVELOPMENT_SCHEMA, "operation": "unknown"},
            {"schema": DEVELOPMENT_SCHEMA, "operation": "inspect"},
            {
                "schema": DEVELOPMENT_SCHEMA,
                "operation": "inspect",
                "source": str(HELLO),
                "root": "",
            },
            {
                "schema": DEVELOPMENT_SCHEMA,
                "operation": "run",
                "source": str(HELLO),
                "limits": {"unknown": 1},
            },
            {
                "schema": DEVELOPMENT_SCHEMA,
                "operation": "run",
                "source": str(HELLO),
                "workflow": "",
            },
            {
                "schema": DEVELOPMENT_SCHEMA,
                "operation": "run",
                "source": str(HELLO),
                "inputs": [],
            },
        )

        responses = [
            execute_development_request(request) for request in invalid_requests
        ]
        responses.append(execute_development_json("{"))

        self.assertTrue(all(not response["ok"] for response in responses))
        self.assertEqual(
            {response["error"]["code"] for response in responses},
            {"request/invalid"},
        )

    def test_cli_executes_json_request_and_returns_json_response(self) -> None:
        request = {
            "schema": DEVELOPMENT_SCHEMA,
            "operation": "inspect",
            "source": str(COMPOSED),
            "root": str(COMPOSED.parent),
        }
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "kosika.cli",
                    "development",
                    str(request_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )

        self.assertEqual(process.returncode, 0, process.stderr)
        response = json.loads(process.stdout)
        self.assertEqual(response["schema"], DEVELOPMENT_SCHEMA)
        self.assertEqual(response["operation"], "inspect")
        self.assertTrue(response["ok"])
        self.assertEqual(len(response["result"]["modules"]), 3)


if __name__ == "__main__":
    unittest.main()
