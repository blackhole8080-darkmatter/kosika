from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import shutil
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from kosika import cli
from kosika.checker import check
from kosika.components import (
    COMPONENT_SCHEMA,
    ComponentContractError,
    ComponentExecutionError,
    ComponentReportedError,
    bind_component,
    component_contract_digest,
    component_contract_to_dict,
    execute_component,
    load_component_contract,
    parse_component_contract,
    verify_component_artifact,
)
from kosika.parser import parse
from kosika.runtime import run

ROOT = Path(__file__).parents[1]
EXAMPLE_CONTRACT = ROOT / "examples" / "components" / "uppercase.component.json"
EXAMPLE_SOURCE = ROOT / "examples" / "component_uppercase.kos"
EXAMPLE_INPUT = ROOT / "examples" / "component_uppercase_input.json"

PROGRAM_SOURCE = """record TransformInput {
  text: Text
}
record TransformOutput {
  text: Text
}
tool uppercase {
  source mcp "component:test.uppercase"
  operation: "uppercase"
  access: analyze
  input: TransformInput
  output: TransformOutput
}
agent transformer {
  can: [uppercase]
  cannot: [network]
}
workflow main(input: TransformInput) -> TransformOutput {
  step transform {
    let result = execute transformer.uppercase(text: input.text)
    return result
  }
}
"""

SUCCESS_SCRIPT = """import json, sys
request = json.loads(sys.stdin.read())
print(json.dumps({
    "schema": "kosika.component-result/v1",
    "ok": True,
    "output": {"text": request["input"]["text"].upper()},
}))
"""


def _schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }


def _contract_data(
    artifact_sha256: str,
    *,
    artifact: str = "component.py",
    timeout_ms: int = 5_000,
    errors: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "schema": COMPONENT_SCHEMA,
        "id": "test.uppercase",
        "version": "1.0.0",
        "tool": {"name": "uppercase", "operation": "uppercase"},
        "implementation": {
            "artifact": artifact,
            "sha256": artifact_sha256,
            "protocol": "json-stdio/v1",
        },
        "inputSchema": _schema(),
        "outputSchema": _schema(),
        "access": "analyze",
        "effects": ["cpu:compute"],
        "capabilities": ["text:transform"],
        "errors": errors or [{"code": "input:invalid", "retryable": False}],
        "cancellation": {"mode": "terminate", "timeoutMs": timeout_ms},
    }


def _write_contract(
    directory: Path,
    script: str = SUCCESS_SCRIPT,
    *,
    timeout_ms: int = 5_000,
    errors: list[dict[str, object]] | None = None,
):
    artifact = directory / "component.py"
    artifact.write_text(script, encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    contract_path = directory / "component.json"
    contract_path.write_text(
        json.dumps(_contract_data(digest, timeout_ms=timeout_ms, errors=errors)),
        encoding="utf-8",
    )
    return load_component_contract(contract_path)


def _program():
    program = parse(PROGRAM_SOURCE)
    check(program)
    return program


class ComponentContractTests(unittest.TestCase):
    def test_example_contract_round_trips_with_stable_identity(self) -> None:
        contract = load_component_contract(EXAMPLE_CONTRACT)

        self.assertEqual(contract.component_id, "example.uppercase")
        self.assertEqual(
            parse_component_contract(component_contract_to_dict(contract)), contract
        )
        self.assertRegex(component_contract_digest(contract), r"^[0-9a-f]{64}$")
        artifact = verify_component_artifact(contract, EXAMPLE_CONTRACT.parent)
        self.assertEqual(artifact.name, "uppercase.js")

    def test_contract_rejects_unverifiable_or_unbounded_fields(self) -> None:
        base = json.loads(EXAMPLE_CONTRACT.read_text(encoding="utf-8"))
        cases = {
            "unknown": ("unknown", True),
            "traversal": ("implementation.artifact", "../uppercase.js"),
            "digest": ("implementation.sha256", "not-a-digest"),
            "timeout": ("cancellation.timeoutMs", 0),
            "mode": ("cancellation.mode", "cooperative"),
            "schema": ("schema", "kosika.component/v2"),
        }
        for name, (path, value) in cases.items():
            with self.subTest(name=name):
                data = copy.deepcopy(base)
                target = data
                parts = path.split(".")
                for part in parts[:-1]:
                    target = target[part]
                target[parts[-1]] = value
                with self.assertRaises(ComponentContractError):
                    parse_component_contract(data)

    def test_artifact_digest_is_rechecked_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract = _write_contract(root)
            (root / "component.py").write_text("print('tampered')", encoding="utf-8")

            with self.assertRaisesRegex(ComponentContractError, "digest"):
                execute_component(contract, root, [sys.executable], {"text": "hello"})

    def test_binding_requires_exact_schema_authority_and_host_grants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract = _write_contract(root)
            program = _program()

            with self.assertRaisesRegex(ComponentContractError, "effects"):
                bind_component(program, contract, root, [sys.executable])
            with self.assertRaisesRegex(ComponentContractError, "capabilities"):
                bind_component(
                    program,
                    contract,
                    root,
                    [sys.executable],
                    allowed_effects=contract.effects,
                )
            mismatched = replace(contract, access="execute")
            with self.assertRaisesRegex(ComponentContractError, "access"):
                bind_component(
                    program,
                    mismatched,
                    root,
                    [sys.executable],
                    allowed_effects=contract.effects,
                    allowed_capabilities=contract.capabilities,
                )

    def test_component_executes_through_the_real_workflow_tool_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract = _write_contract(root)
            program = _program()
            binding = bind_component(
                program,
                contract,
                root,
                [sys.executable],
                allowed_effects=contract.effects,
                allowed_capabilities=contract.capabilities,
            )

            result = run(
                program,
                "main",
                root / "trace.jsonl",
                inputs={"input": {"text": "hello"}},
                tool_bindings={"uppercase": binding},
            )

        self.assertEqual(result, {"text": "HELLO"})
        self.assertEqual(
            binding.identity["artifactSha256"], contract.implementation.sha256
        )

    def test_declared_component_errors_preserve_retryability(self) -> None:
        script = """import json
print(json.dumps({
    "schema": "kosika.component-result/v1",
    "ok": False,
    "error": {"code": "input:invalid", "message": "bad input"},
}))
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract = _write_contract(root, script)
            with self.assertRaises(ComponentReportedError) as caught:
                execute_component(contract, root, [sys.executable], {"text": "hello"})

        self.assertEqual(caught.exception.code, "input:invalid")
        self.assertFalse(caught.exception.retryable)

    def test_undeclared_errors_and_invalid_outputs_fail_closed(self) -> None:
        responses = (
            {
                "schema": "kosika.component-result/v1",
                "ok": False,
                "error": {"code": "network:surprise", "message": "no"},
            },
            {
                "schema": "kosika.component-result/v1",
                "ok": True,
                "output": {"wrong": "field"},
            },
        )
        for response in responses:
            with self.subTest(response=response):
                script = f"import json\nprint(json.dumps({response!r}))\n"
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    contract = _write_contract(root, script)
                    with self.assertRaises(ComponentExecutionError):
                        execute_component(
                            contract, root, [sys.executable], {"text": "hello"}
                        )

    def test_termination_deadline_kills_a_slow_component(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract = _write_contract(
                root,
                "import time\ntime.sleep(5)\n",
                timeout_ms=100,
            )
            started = time.perf_counter()
            with self.assertRaises(ComponentExecutionError) as caught:
                execute_component(contract, root, [sys.executable], {"text": "hello"})

        self.assertEqual(caught.exception.code, "component/timeout")
        self.assertLess(time.perf_counter() - started, 2)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for the JS smoke")
    def test_javascript_example_uses_the_language_neutral_protocol(self) -> None:
        contract = load_component_contract(EXAMPLE_CONTRACT)

        result = execute_component(
            contract,
            EXAMPLE_CONTRACT.parent,
            [shutil.which("node") or "node"],
            {"text": "hello"},
        )

        self.assertEqual(result, {"text": "HELLO"})


class ComponentCommandTests(unittest.TestCase):
    def test_component_check_reports_pinned_identity(self) -> None:
        output = io.StringIO()
        with (
            patch.object(
                sys,
                "argv",
                ["kosika", "component", "check", str(EXAMPLE_CONTRACT), "--json"],
            ),
            contextlib.redirect_stdout(output),
        ):
            result = cli.main()

        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["valid"])
        self.assertEqual(payload["id"], "example.uppercase")
        self.assertRegex(payload["identity"]["contractDigest"], r"^[0-9a-f]{64}$")

    def test_run_command_binds_a_component_only_with_explicit_grants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            output = io.StringIO()
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "kosika",
                        "run",
                        str(EXAMPLE_SOURCE),
                        "--input",
                        str(EXAMPLE_INPUT),
                        "--trace",
                        str(trace),
                        "--component-manifest",
                        str(EXAMPLE_CONTRACT),
                        "--component-runtime",
                        shutil.which("node") or "node",
                        "--allow-component-effect",
                        "cpu:compute",
                        "--allow-component-capability",
                        "text:transform",
                    ],
                ),
                contextlib.redirect_stdout(output),
            ):
                if shutil.which("node") is None:
                    self.skipTest("Node.js is required for the JS CLI smoke")
                result = cli.main()

            self.assertEqual(result, 0)
            self.assertIn("HELLO FROM KOSIKA", output.getvalue())
            self.assertTrue(trace.exists())


if __name__ == "__main__":
    unittest.main()
