from __future__ import annotations

import contextlib
import dataclasses
import enum
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import typing
import unittest
from pathlib import Path
from unittest.mock import patch

from kosika import cli, component_sdk
from kosika.checker import check
from kosika.components import (
    COMPONENT_CALL_SCHEMA,
    COMPONENT_PROTOCOL,
    COMPONENT_RESULT_SCHEMA,
    COMPONENT_SCHEMA,
    MAX_MESSAGE_BYTES,
    MAX_TIMEOUT_MS,
    ComponentReportedError,
    bind_component,
    component_contract_to_dict,
    load_component_contract,
    parse_component_contract,
    verify_component_artifact,
)
from kosika.parser import parse
from kosika.runtime import run
from kosika.schema import schema_for_tool_input, schema_for_type

ROOT = Path(__file__).parents[1]
EXAMPLE_DIR = ROOT / "examples" / "components"
EXAMPLE_ARTIFACT = EXAMPLE_DIR / "uppercase_python.py"
EXAMPLE_CONTRACT = EXAMPLE_DIR / "uppercase_python.component.json"
EXAMPLE_SOURCE = ROOT / "examples" / "component_uppercase_python.kos"
EXAMPLE_INPUT = ROOT / "examples" / "component_uppercase_input.json"

COMPLEX_PROGRAM = """enum Color { red, green }
record Inner { n: Number }
record ComplexInput {
  text: Text
  maybe: Text?
  items: Number[]
  bag: Text{}
  inner: Inner
  color: Color
}
record ComplexOutput { text: Text }
"""

NESTED_SCRIPT = """from __future__ import annotations

import enum
from dataclasses import dataclass

from kosika.component_sdk import ErrorSpec, ToolFailure, component, serve


class Color(enum.Enum):
    RED = "red"
    GREEN = "green"


@dataclass(frozen=True)
class Inner:
    n: float


@dataclass(frozen=True)
class Output:
    text: str
    n: float
    color: str


@component(
    id="test.nested",
    version="1.0.0",
    name="nested_tool",
    access="analyze",
    errors=(ErrorSpec("input:bad"),),
    timeout_ms=5000,
)
def nested_tool(
    text: str, inner: Inner, color: Color, maybe: str | None
) -> Output:
    if text == "fail":
        raise ToolFailure("input:bad", "asked to fail")
    if text == "undeclared":
        raise ToolFailure("input:other", "not declared")
    if text == "boom":
        raise RuntimeError("boom")
    return Output(text=text.upper() + (maybe or ""), n=inner.n, color=color.value)


@component(
    id="test.nested-two",
    version="2.0.0",
    name="second_tool",
    access="observe",
)
def second_tool(value: str) -> dict[str, str]:
    return {"value": value}


if __name__ == "__main__":
    serve()
"""


class Color(enum.Enum):
    RED = "red"
    GREEN = "green"


@dataclasses.dataclass(frozen=True)
class Inner:
    n: float


@dataclasses.dataclass(frozen=True)
class ProbeOutput:
    text: str


@dataclasses.dataclass
class Recursive:
    child: Recursive | None


class LooseDict(typing.TypedDict, total=False):
    text: str


class ShapeOutput(typing.TypedDict):
    text: str


@component_sdk.component(
    id="test.sdk_probe",
    version="1.0.0",
    name="sdk_probe",
    access="analyze",
    effects=("cpu:compute",),
    capabilities=("text:transform",),
    errors=(component_sdk.ErrorSpec("input:invalid"),),
    timeout_ms=5_000,
)
def sdk_probe(
    text: str,
    maybe: str | None,
    items: list[float],
    bag: dict[str, str],
    inner: Inner,
    color: Color,
) -> ProbeOutput:
    return ProbeOutput(text=text)


@component_sdk.component(
    id="test.sdk_empty",
    version="1.0.0",
    name="sdk_empty",
    access="observe",
)
def sdk_empty() -> None:
    return None


@component_sdk.component(
    id="test.sdk_shapes",
    version="1.0.0",
    name="sdk_shapes",
    access="observe",
)
def sdk_shapes(
    size: typing.Literal["small", "large"],
    values: typing.Sequence[str],
    weights: typing.Mapping[str, float],
) -> ShapeOutput:
    return {"text": size}


def _decorate_bad(**arguments: typing.Any) -> None:
    @component_sdk.component(**arguments)
    def generated_probe(value: str) -> dict[str, str]:
        return {"value": value}


def _pythonpath() -> str:
    existing = os.environ.get("PYTHONPATH")
    return str(ROOT) + (os.pathsep + existing if existing else "")


def _run_artifact(
    script: Path, *arguments: str, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = _pythonpath()
    return subprocess.run(
        [sys.executable, str(script), *arguments],
        cwd=script.parent,
        env=environment,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


def _request(
    *,
    component: str = "test.nested",
    version: str = "1.0.0",
    operation: str = "nested_tool",
    value: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "schema": component_sdk.COMPONENT_CALL_SCHEMA,
        "component": component,
        "version": version,
        "operation": operation,
        "input": value or {},
    }


def _serve(
    script: Path, request: dict[str, object]
) -> subprocess.CompletedProcess[str]:
    return _run_artifact(script, stdin=json.dumps(request))


def _write_nested_script(directory: Path) -> Path:
    script = directory / "nested.py"
    script.write_text(NESTED_SCRIPT, encoding="utf-8")
    return script


class ComponentSchemaTests(unittest.TestCase):
    def test_generated_schemas_match_kosika_exactly(self) -> None:
        program = parse(COMPLEX_PROGRAM)
        check(program)

        manifest = component_sdk.component_manifest(Path(__file__), tool="sdk_probe")

        self.assertEqual(
            manifest["inputSchema"], schema_for_type(program, "ComplexInput")
        )
        self.assertEqual(
            manifest["outputSchema"], schema_for_type(program, "ComplexOutput")
        )

    def test_literal_sequence_mapping_and_typed_dict_match_kosika(self) -> None:
        program = parse(
            "enum Size { small, large }\n"
            "record ShapeInput {\n"
            "  size: Size\n"
            "  values: Text[]\n"
            "  weights: Number{}\n"
            "}\n"
            "record ShapeOutput { text: Text }\n"
        )
        check(program)

        manifest = component_sdk.component_manifest(Path(__file__), tool="sdk_shapes")

        self.assertEqual(
            manifest["inputSchema"], schema_for_type(program, "ShapeInput")
        )
        self.assertEqual(
            manifest["outputSchema"], schema_for_type(program, "ShapeOutput")
        )

    def test_empty_input_and_output_match_kosika(self) -> None:
        program = parse("record Empty {}\n")
        check(program)

        manifest = component_sdk.component_manifest(Path(__file__), tool="sdk_empty")

        self.assertEqual(manifest["inputSchema"], schema_for_tool_input(program, None))
        self.assertEqual(manifest["outputSchema"], schema_for_type(program, "Empty"))

    def test_manifest_and_constants_match_the_host_contract(self) -> None:
        manifest = component_sdk.component_manifest(Path(__file__), tool="sdk_probe")
        contract = parse_component_contract(manifest)

        self.assertEqual(component_sdk.COMPONENT_SCHEMA, COMPONENT_SCHEMA)
        self.assertEqual(component_sdk.COMPONENT_CALL_SCHEMA, COMPONENT_CALL_SCHEMA)
        self.assertEqual(component_sdk.COMPONENT_RESULT_SCHEMA, COMPONENT_RESULT_SCHEMA)
        self.assertEqual(component_sdk.COMPONENT_PROTOCOL, COMPONENT_PROTOCOL)
        self.assertEqual(component_sdk.MAX_MESSAGE_BYTES, MAX_MESSAGE_BYTES)
        self.assertEqual(component_sdk.MAX_TIMEOUT_MS, MAX_TIMEOUT_MS)
        self.assertEqual(contract.component_id, "test.sdk_probe")
        self.assertEqual(contract.tool_name, "sdk_probe")
        self.assertEqual(contract.operation, "sdk_probe")
        self.assertEqual(contract.implementation.artifact, Path(__file__).name)
        self.assertEqual(
            contract.implementation.sha256,
            hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        )
        self.assertEqual(
            parse_component_contract(component_contract_to_dict(contract)),
            contract,
        )

    def test_several_tools_need_a_choice(self) -> None:
        with self.assertRaisesRegex(component_sdk.ComponentSchemaError, "several"):
            component_sdk.component_manifest(Path(__file__))
        with self.assertRaisesRegex(
            component_sdk.ComponentSchemaError, "No component tool"
        ):
            component_sdk.component_manifest(Path(__file__), tool="missing")

    def test_decorator_rejects_unbounded_declarations(self) -> None:
        cases: dict[str, dict[str, typing.Any]] = {
            "id": {"id": "not valid", "version": "1.0.0", "access": "analyze"},
            "version": {"id": "test.bad_version", "version": "1", "access": "analyze"},
            "access": {"id": "test.bad_access", "version": "1.0.0", "access": "admin"},
            "effect": {
                "id": "test.bad_effect",
                "version": "1.0.0",
                "access": "analyze",
                "effects": ("compute",),
            },
            "capability": {
                "id": "test.bad_capability",
                "version": "1.0.0",
                "access": "analyze",
                "capabilities": ("text:transform", "text:transform"),
            },
            "error": {
                "id": "test.bad_error",
                "version": "1.0.0",
                "access": "analyze",
                "errors": (component_sdk.ErrorSpec("invalid"),),
            },
            "error_duplicate": {
                "id": "test.bad_error_duplicate",
                "version": "1.0.0",
                "access": "analyze",
                "errors": (
                    component_sdk.ErrorSpec("input:invalid"),
                    component_sdk.ErrorSpec("input:invalid"),
                ),
            },
            "timeout": {
                "id": "test.bad_timeout",
                "version": "1.0.0",
                "access": "analyze",
                "timeout_ms": 0,
            },
            "nullable_timeout": {
                "id": "test.bad_nullable_timeout",
                "version": "1.0.0",
                "access": "analyze",
                "timeout_ms": True,
            },
        }
        for label, arguments in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(component_sdk.ComponentSchemaError):
                    _decorate_bad(**arguments)

    def test_unsupported_annotations_fail_at_decoration(self) -> None:
        with self.assertRaises(component_sdk.ComponentSchemaError):

            @component_sdk.component(
                id="test.bad_any", version="1.0.0", access="analyze"
            )
            def any_probe(value: typing.Any) -> dict[str, str]:
                return {"value": "x"}

        with self.assertRaisesRegex(component_sdk.ComponentSchemaError, "Recursive"):

            @component_sdk.component(
                id="test.bad_recursive", version="1.0.0", access="analyze"
            )
            def recursive_probe(value: Recursive) -> dict[str, str]:
                return {"value": "x"}

        with self.assertRaisesRegex(component_sdk.ComponentSchemaError, "optional"):

            @component_sdk.component(
                id="test.bad_optional", version="1.0.0", access="analyze"
            )
            def optional_probe(value: LooseDict) -> dict[str, str]:
                return {"value": "x"}

        with self.assertRaisesRegex(
            component_sdk.ComponentSchemaError, "object record"
        ):

            @component_sdk.component(
                id="test.bad_return", version="1.0.0", access="analyze"
            )
            def bad_return(value: str) -> str:
                return value

        with self.assertRaisesRegex(
            component_sdk.ComponentSchemaError, "return annotation"
        ):

            @component_sdk.component(
                id="test.bad_missing_return", version="1.0.0", access="analyze"
            )
            def missing_return(value: str):
                return {"value": value}

        with self.assertRaisesRegex(component_sdk.ComponentSchemaError, "keyword"):

            @component_sdk.component(
                id="test.bad_variadic", version="1.0.0", access="analyze"
            )
            def variadic(*values: str) -> dict[str, str]:
                return {"value": ",".join(values)}


class ComponentServeTests(unittest.TestCase):
    def test_serves_nested_records_and_declared_errors(self) -> None:
        value: dict[str, object] = {
            "text": "hello",
            "inner": {"n": 2.5},
            "color": "red",
            "maybe": None,
        }
        with tempfile.TemporaryDirectory() as directory:
            script = _write_nested_script(Path(directory))

            success = _serve(script, _request(value=value))
            self.assertEqual(success.returncode, 0, success.stderr)
            self.assertEqual(
                json.loads(success.stdout),
                {
                    "schema": COMPONENT_RESULT_SCHEMA,
                    "ok": True,
                    "output": {"text": "HELLO", "n": 2.5, "color": "red"},
                },
            )

            failure = _serve(script, _request(value={**value, "text": "fail"}))
            self.assertEqual(failure.returncode, 0, failure.stderr)
            self.assertEqual(
                json.loads(failure.stdout),
                {
                    "schema": COMPONENT_RESULT_SCHEMA,
                    "ok": False,
                    "error": {"code": "input:bad", "message": "asked to fail"},
                },
            )

    def test_fails_closed_and_writes_no_result(self) -> None:
        value: dict[str, object] = {
            "text": "hello",
            "inner": {"n": 1},
            "color": "red",
            "maybe": None,
        }
        cases = (
            (
                "unknown operation",
                _request(operation="missing"),
                "unknown operation",
            ),
            (
                "wrong identity",
                _request(version="9.9.9"),
                "identity",
            ),
            (
                "missing field",
                _request(
                    value={key: item for key, item in value.items() if key != "maybe"}
                ),
                "missing required property",
            ),
            (
                "undeclared error",
                _request(value={**value, "text": "undeclared"}),
                "undeclared error code",
            ),
            (
                "unexpected exception",
                _request(value={**value, "text": "boom"}),
                "RuntimeError",
            ),
            (
                "extra request field",
                {**_request(value=value), "extra": True},
                "invalid shape",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            script = _write_nested_script(Path(directory))
            for label, request, expected in cases:
                with self.subTest(label=label):
                    result = _serve(script, request)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(result.stdout, "")
                    self.assertIn(expected, result.stderr)

    def test_describe_lists_tools_and_selects_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = _write_nested_script(root)

            empty_script = root / "empty.py"
            empty_script.write_text(
                "from kosika.component_sdk import serve\n"
                'if __name__ == "__main__":\n'
                "    serve()\n",
                encoding="utf-8",
            )
            empty = _run_artifact(empty_script, "--describe")
            self.assertEqual(empty.returncode, 2)
            self.assertIn("defines no component tools", empty.stderr)

            ambiguous = _run_artifact(script, "--describe")
            self.assertEqual(ambiguous.returncode, 2)
            self.assertIn("several tools", ambiguous.stderr)

            output = root / "second.component.json"
            selected = _run_artifact(
                script,
                "--describe",
                "--tool",
                "second_tool",
                "--output",
                str(output),
            )
            self.assertEqual(selected.returncode, 0, selected.stderr)
            self.assertIn("Wrote component contract", selected.stdout)

            contract = load_component_contract(output)
            self.assertEqual(contract.component_id, "test.nested-two")
            self.assertEqual(contract.tool_name, "second_tool")
            verify_component_artifact(contract, root)


class ComponentIntegrationTests(unittest.TestCase):
    def test_committed_example_binds_and_runs(self) -> None:
        contract = load_component_contract(EXAMPLE_CONTRACT)
        verify_component_artifact(contract, EXAMPLE_CONTRACT.parent)
        program = parse(EXAMPLE_SOURCE.read_text(encoding="utf-8"))
        check(program)
        binding = bind_component(
            program,
            contract,
            EXAMPLE_CONTRACT.parent,
            [sys.executable],
            allowed_effects=contract.effects,
            allowed_capabilities=contract.capabilities,
        )

        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"PYTHONPATH": _pythonpath()}):
                result = run(
                    program,
                    "main",
                    Path(directory) / "trace.jsonl",
                    inputs=json.loads(EXAMPLE_INPUT.read_text(encoding="utf-8")),
                    tool_bindings={contract.tool_name: binding},
                )
                with self.assertRaises(ComponentReportedError) as caught:
                    binding(binding.declaration, {"text": "   "})

        self.assertEqual(result, {"text": "HELLO FROM KOSIKA"})
        self.assertEqual(caught.exception.code, "input:invalid")
        self.assertFalse(caught.exception.retryable)

    def test_cli_describe_matches_the_committed_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "generated.component.json"
            stdout = io.StringIO()
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "kosika",
                        "component",
                        "describe",
                        str(EXAMPLE_ARTIFACT),
                        "--output",
                        str(output),
                    ],
                ),
                contextlib.redirect_stdout(stdout),
            ):
                result = cli.main()

            self.assertEqual(result, 0)
            self.assertIn("Wrote component contract", stdout.getvalue())
            generated = load_component_contract(output)
            committed = load_component_contract(EXAMPLE_CONTRACT)
            self.assertEqual(generated, committed)
            verify_component_artifact(committed, EXAMPLE_CONTRACT.parent)

    def test_cli_describe_reports_a_broken_artifact(self) -> None:
        cases = (
            ("exit", "raise SystemExit('broken artifact')\n", "broken artifact"),
            ("shape", "print('{}')\n", "missing fields"),
        )
        for label, source, expected in cases:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as directory:
                    script = Path(directory) / "broken.py"
                    script.write_text(source, encoding="utf-8")
                    stderr = io.StringIO()
                    with (
                        patch.object(
                            sys,
                            "argv",
                            ["kosika", "component", "describe", str(script)],
                        ),
                        contextlib.redirect_stderr(stderr),
                    ):
                        result = cli.main()
                self.assertEqual(result, 2)
                self.assertIn(expected, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
