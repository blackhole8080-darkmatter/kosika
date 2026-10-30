"""Kosika declarations generated from pinned component contracts."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from kosika import cli
from kosika.checker import check
from kosika.component_declaration import (
    ComponentDeclarationError,
    declaration_for_contract,
)
from kosika.components import (
    ComponentContract,
    bind_component,
    load_component_contract,
    parse_component_contract,
)
from kosika.modules import load_program
from kosika.parser import parse
from kosika.schema import schema_for_tool_input, schema_for_type, schemas_equal

ROOT = Path(__file__).parents[1]
EXAMPLE_DIR = ROOT / "examples" / "components"
EXAMPLE_CONTRACT = EXAMPLE_DIR / "uppercase_python.component.json"


def contract_for(
    input_schema: dict[str, Any],
    output_schema: dict[str, Any],
    *,
    tool: str = "shape_tool",
    component_id: str = "test.shapes",
    access: str = "analyze",
) -> ComponentContract:
    return parse_component_contract(
        {
            "schema": "kosika.component/v1",
            "id": component_id,
            "version": "1.0.0",
            "tool": {"name": tool, "operation": tool},
            "implementation": {
                "artifact": "tool.py",
                "sha256": "0" * 64,
                "protocol": "json-stdio/v1",
            },
            "inputSchema": input_schema,
            "outputSchema": output_schema,
            "access": access,
            "effects": [],
            "capabilities": [],
            "errors": [],
            "cancellation": {"mode": "terminate", "timeoutMs": 1000},
        }
    )


def closed(
    properties: dict[str, Any], *, required: list[str] | None = None
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required is not None:
        schema["required"] = required
    return schema


def record_schema(**properties: Any) -> dict[str, Any]:
    return closed(properties, required=list(properties))


RICH_INPUT = record_schema(
    text={"type": "string"},
    maybe={"anyOf": [{"type": "string"}, {"type": "null"}]},
    color={"type": "string", "enum": ["red", "green"]},
    colors={"type": "array", "items": {"type": "string", "enum": ["red", "green"]}},
    items={"type": "array", "items": {"type": "number"}},
    bag={"type": "object", "additionalProperties": {"type": "number"}},
    extras={
        "type": "object",
        "additionalProperties": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    maybe_items={
        "anyOf": [
            {"type": "array", "items": {"type": "string"}},
            {"type": "null"},
        ]
    },
    inner=record_schema(n={"type": "number"}),
    nothing=closed({}, required=[]),
    myField=record_schema(left={"type": "string"}),
    MyField=record_schema(right={"type": "number"}),
)
RICH_OUTPUT = record_schema(
    text={"type": "string"},
    color={"type": "string", "enum": ["red", "green"]},
)


class DeclarationTests(unittest.TestCase):
    def test_the_committed_example_declares_exactly(self) -> None:
        contract = load_component_contract(EXAMPLE_CONTRACT)
        self.assertEqual(
            declaration_for_contract(contract),
            f"""// Generated from example.uppercase-python 1.0.0.
// Pinned artifact: uppercase_python.py {contract.implementation.sha256}
// This declares shapes only. The host still grants the manifest, its effects and capabilities.

export [UppercasePythonInput, UppercasePythonOutput, uppercase_python]

record UppercasePythonInput {{
  text: Text
}}

record UppercasePythonOutput {{
  text: Text
}}

tool uppercase_python {{
  source mcp "component:example.uppercase-python"
  operation: "uppercase_python"
  access: analyze
  input: UppercasePythonInput
  output: UppercasePythonOutput
}}
""",
        )

    def test_the_generated_example_binds_through_the_host_gate(self) -> None:
        contract = load_component_contract(EXAMPLE_CONTRACT)
        program = parse(declaration_for_contract(contract))
        check(program)
        binding = bind_component(
            program,
            contract,
            EXAMPLE_DIR,
            [sys.executable],
            allowed_effects=contract.effects,
            allowed_capabilities=contract.capabilities,
        )
        self.assertEqual(binding.declaration.name, contract.tool_name)

    def test_a_rich_contract_round_trips(self) -> None:
        contract = contract_for(RICH_INPUT, RICH_OUTPUT)
        text = declaration_for_contract(contract)
        program = parse(text)
        check(program)
        tool = next(item for item in program.tools if item.name == "shape_tool")
        self.assertTrue(
            schemas_equal(
                contract.input_schema, schema_for_tool_input(program, tool.input)
            )
        )
        self.assertTrue(
            schemas_equal(contract.output_schema, schema_for_type(program, tool.output))
        )

        self.assertIn("enum ShapeToolInputColor {", text)
        self.assertIn("enum ShapeToolInputColors {", text)
        self.assertIn("  red,\n  green\n", text)
        self.assertIn("record ShapeToolInputInner {", text)
        self.assertIn("record ShapeToolInputNothing {}", text)
        self.assertIn("  maybe: Text?", text)
        self.assertIn("  colors: ShapeToolInputColors[]", text)
        self.assertIn("  items: Number[]", text)
        self.assertIn("  bag: Number{}", text)
        self.assertIn("  extras: Text?{}", text)
        self.assertIn("  maybe_items: Text[]?", text)
        self.assertIn("  myField: ShapeToolInputMyField", text)
        self.assertIn("  MyField: ShapeToolInputMyField2", text)
        self.assertIn('  source mcp "component:test.shapes"', text)
        self.assertIn("  access: analyze", text)

        again = declaration_for_contract(contract)
        self.assertEqual(text, again)

    def test_generated_declarations_import_into_a_program(self) -> None:
        contract = load_component_contract(EXAMPLE_CONTRACT)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = root / "tools"
            tools.mkdir()
            (tools / "uppercase.kos").write_text(
                declaration_for_contract(contract), encoding="utf-8"
            )
            (root / "main.kos").write_text(
                """import "tools/uppercase.kos"

agent transformer {
  can: [uppercase_python]
  cannot: [network]
}

workflow main(input: UppercasePythonInput) -> UppercasePythonOutput {
  step transform {
    let result = execute transformer.uppercase_python(text: input.text)
    return result
  }
}
""",
                encoding="utf-8",
            )
            program = load_program(root / "main.kos")
            check(program)
            binding = bind_component(
                program,
                contract,
                EXAMPLE_DIR,
                [sys.executable],
                allowed_effects=contract.effects,
                allowed_capabilities=contract.capabilities,
            )
            self.assertEqual(binding.declaration.name, "uppercase_python")

    def test_required_order_sets_the_record_order(self) -> None:
        contract = contract_for(
            {
                "type": "object",
                "properties": {
                    "a": {"type": "string"},
                    "b": {"type": "number"},
                },
                "required": ["b", "a"],
                "additionalProperties": False,
            },
            record_schema(text={"type": "string"}),
        )
        text = declaration_for_contract(contract)
        self.assertIn("  b: Number\n  a: Text", text)
        program = parse(text)
        check(program)
        tool = next(item for item in program.tools if item.name == "shape_tool")
        self.assertTrue(
            schemas_equal(
                contract.input_schema, schema_for_tool_input(program, tool.input)
            )
        )

    def test_a_tool_without_arguments_omits_input(self) -> None:
        contract = contract_for(
            {"type": "object", "properties": {}, "additionalProperties": False},
            closed({}, required=[]),
            tool="no_args",
        )
        text = declaration_for_contract(contract)
        self.assertNotIn("input:", text)
        self.assertIn("record NoArgsOutput {}", text)
        program = parse(text)
        check(program)
        tool = next(item for item in program.tools if item.name == "no_args")
        self.assertIsNone(tool.input)
        self.assertTrue(
            schemas_equal(
                contract.input_schema, schema_for_tool_input(program, tool.input)
            )
        )
        self.assertTrue(
            schemas_equal(contract.output_schema, schema_for_type(program, tool.output))
        )

    def test_a_no_argument_shape_with_required_stays_a_record(self) -> None:
        contract = contract_for(
            closed({}, required=[]),
            closed({}, required=[]),
            tool="explicit_empty",
        )
        text = declaration_for_contract(contract)
        self.assertIn("input: ExplicitEmptyInput", text)
        self.assertIn("record ExplicitEmptyInput {}", text)
        self.assertIn("record ExplicitEmptyOutput {}", text)

    def test_unsupported_shapes_are_refused(self) -> None:
        cases: tuple[tuple[str, dict[str, Any], dict[str, Any], str], ...] = (
            (
                "integer",
                record_schema(n={"type": "integer"}),
                record_schema(text={"type": "string"}),
                "Kosika's Number is",
            ),
            (
                "enum value",
                record_schema(
                    status={"type": "string", "enum": ["in-progress", "done"]}
                ),
                record_schema(text={"type": "string"}),
                "not a Kosika identifier",
            ),
            (
                "empty enum",
                record_schema(status={"type": "string", "enum": []}),
                record_schema(text={"type": "string"}),
                "at least one value",
            ),
            (
                "open record",
                {
                    "type": "object",
                    "properties": {"n": {"type": "number"}},
                    "required": ["n"],
                    "additionalProperties": True,
                },
                record_schema(text={"type": "string"}),
                "records are closed",
            ),
            (
                "partial required",
                {
                    "type": "object",
                    "properties": {
                        "n": {"type": "number"},
                        "m": {"type": "number"},
                    },
                    "required": ["n"],
                    "additionalProperties": False,
                },
                record_schema(text={"type": "string"}),
                "every field must be required",
            ),
            (
                "missing additionalProperties",
                {
                    "type": "object",
                    "properties": {"n": {"type": "number"}},
                    "required": ["n"],
                },
                record_schema(text={"type": "string"}),
                "records are closed",
            ),
            (
                "map root",
                record_schema(text={"type": "string"}),
                {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
                "closed object record",
            ),
            (
                "double optional",
                record_schema(
                    maybe={
                        "anyOf": [
                            {
                                "anyOf": [
                                    {"type": "string"},
                                    {"type": "null"},
                                ]
                            },
                            {"type": "null"},
                        ]
                    }
                ),
                record_schema(text={"type": "string"}),
                "at most once",
            ),
            (
                "null optional",
                record_schema(maybe={"anyOf": [{"type": "null"}, {"type": "null"}]}),
                record_schema(text={"type": "string"}),
                "value or null",
            ),
            (
                "unknown key",
                record_schema(text={"type": "string", "description": "words"}),
                record_schema(text={"type": "string"}),
                "cannot be declared",
            ),
            (
                "empty output without required",
                record_schema(text={"type": "string"}),
                {"type": "object", "properties": {}, "additionalProperties": False},
                "always includes an empty required",
            ),
            (
                "array without items",
                record_schema(items={"type": "array"}),
                record_schema(text={"type": "string"}),
                "item schema",
            ),
            (
                "field name",
                record_schema(**{"not-a-name": {"type": "string"}}),
                record_schema(text={"type": "string"}),
                "not a Kosika identifier",
            ),
        )
        for label, input_schema, output_schema, expected in cases:
            with self.subTest(label=label):
                contract = contract_for(input_schema, output_schema)
                with self.assertRaises(ComponentDeclarationError) as caught:
                    declaration_for_contract(contract)
                self.assertIn(expected, str(caught.exception))

    def test_a_tool_name_with_punctuation_is_refused(self) -> None:
        contract = contract_for(
            record_schema(text={"type": "string"}),
            record_schema(text={"type": "string"}),
            tool="upper-case",
        )
        with self.assertRaises(ComponentDeclarationError) as caught:
            declaration_for_contract(contract)
        self.assertIn("not a Kosika identifier", str(caught.exception))


class DeclarationCliTests(unittest.TestCase):
    def _invoke(self, *arguments: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with (
            patch.object(sys, "argv", ["kosika", *arguments]),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = cli.main()
        return code, out.getvalue(), err.getvalue()

    def test_the_cli_prints_declarations(self) -> None:
        code, out, err = self._invoke("component", "declaration", str(EXAMPLE_CONTRACT))
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("tool uppercase_python {", out)
        self.assertIn("record UppercasePythonInput {", out)

    def test_the_cli_writes_declarations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "shapes.kos"
            code, out, err = self._invoke(
                "component",
                "declaration",
                str(EXAMPLE_CONTRACT),
                "--output",
                str(output),
            )
            self.assertEqual(code, 0)
            self.assertEqual(err, "")
            self.assertIn("Wrote Kosika declarations", out)
            self.assertIn("Artifact: uppercase_python.py", out)
            program = parse(output.read_text(encoding="utf-8"))
            check(program)
            self.assertEqual(
                [tool.name for tool in program.tools], ["uppercase_python"]
            )

    def test_the_cli_refuses_an_unrepresentable_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "tool.py"
            artifact.write_text("# a tool\n", encoding="utf-8")
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            manifest = root / "tool.component.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema": "kosika.component/v1",
                        "id": "test.shapes",
                        "version": "1.0.0",
                        "tool": {"name": "shape_tool", "operation": "shape_tool"},
                        "implementation": {
                            "artifact": "tool.py",
                            "sha256": digest,
                            "protocol": "json-stdio/v1",
                        },
                        "inputSchema": record_schema(n={"type": "integer"}),
                        "outputSchema": record_schema(text={"type": "string"}),
                        "access": "analyze",
                        "effects": [],
                        "capabilities": [],
                        "errors": [],
                        "cancellation": {"mode": "terminate", "timeoutMs": 1000},
                    }
                ),
                encoding="utf-8",
            )
            code, out, err = self._invoke("component", "declaration", str(manifest))
            self.assertEqual(code, 2)
            self.assertEqual(out, "")
            self.assertIn("Component declaration failed", err)
            self.assertIn("Kosika's Number is", err)

    def test_the_cli_reports_a_missing_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.component.json"
            code, out, err = self._invoke("component", "declaration", str(missing))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("Component declaration failed", err)


if __name__ == "__main__":
    unittest.main()
