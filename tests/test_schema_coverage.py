import unittest

from kosika.ast import Field, Program, RecordDecl
from kosika.schema import (
    SchemaValidationError,
    schema_for_record,
    schemas_equal,
    validate,
)


class SchemaCoverageTests(unittest.TestCase):
    def test_all_supported_primitive_types_and_numeric_bool_boundary(self) -> None:
        valid_values = [
            ("object", {}),
            ("array", []),
            ("string", "text"),
            ("number", 1.5),
            ("integer", 3),
            ("boolean", False),
            ("null", None),
        ]
        for expected, value in valid_values:
            with self.subTest(expected=expected):
                validate(value, {"type": expected})

        with self.assertRaises(SchemaValidationError):
            validate(True, {"type": "number"})
        with self.assertRaises(SchemaValidationError):
            validate(1.5, {"type": "integer"})
        with self.assertRaises(SchemaValidationError):
            validate("text", {"type": "boolean"})
        with self.assertRaises(SchemaValidationError):
            validate("text", {"type": ["integer", "boolean"]})
        with self.assertRaises(SchemaValidationError):
            validate("text", {"type": "unsupported"})

    def test_const_enum_and_local_reference(self) -> None:
        schema = {
            "$defs": {"status": {"type": "string", "enum": ["ready", "done"]}},
            "type": "object",
            "properties": {
                "state": {"$ref": "#/$defs/status"},
                "kind": {"const": "job"},
            },
            "required": ["state", "kind"],
            "additionalProperties": False,
        }
        validate({"state": "ready", "kind": "job"}, schema)
        for value in (
            {"state": "unknown", "kind": "job"},
            {"state": "ready", "kind": "wrong"},
        ):
            with self.assertRaises(SchemaValidationError):
                validate(value, schema)
        with self.assertRaisesRegex(
            SchemaValidationError, "unsupported schema reference"
        ):
            validate("x", {"$ref": "other.json#/status"})
        with self.assertRaisesRegex(SchemaValidationError, "unknown schema reference"):
            validate("x", {"$ref": "#/$defs/missing", "$defs": {}})

    def test_object_and_array_validation_paths(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["items"],
            "additionalProperties": False,
        }
        validate({"items": [1, 2, 3]}, schema)
        with self.assertRaisesRegex(SchemaValidationError, "missing required"):
            validate({}, schema)
        with self.assertRaisesRegex(SchemaValidationError, "unknown properties"):
            validate({"items": [], "extra": True}, schema)
        with self.assertRaisesRegex(SchemaValidationError, r"\$\.items\[1\]"):
            validate({"items": [1, "bad"]}, schema)
        with self.assertRaises(SchemaValidationError):
            validate([], {"type": "object"})
        # These schemas exercise the permissive/unsupported-keyword branches.
        validate({"value": 1}, {"type": "object", "properties": []})
        validate([1], {"type": "array", "items": "not-a-schema"})

    def test_nested_record_and_array_schemas(self) -> None:
        program = Program(
            records=[
                RecordDecl("Metadata", (Field("name", "Text"),)),
                RecordDecl(
                    "Payload",
                    (
                        Field("tags", "Text[]"),
                        Field("metadata", "Metadata"),
                        Field("optional", "Null"),
                    ),
                ),
            ]
        )
        self.assertEqual(
            schema_for_record(program, "Payload"),
            {
                "type": "object",
                "properties": {
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "metadata": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                        "additionalProperties": False,
                    },
                    "optional": {"type": "null"},
                },
                "required": ["tags", "metadata", "optional"],
                "additionalProperties": False,
            },
        )
        with self.assertRaisesRegex(ValueError, "Recursive record schema"):
            schema_for_record(
                Program(records=[RecordDecl("Loop", (Field("child", "Loop"),))]),
                "Loop",
            )

    def test_schema_helpers_and_record_conversion_errors(self) -> None:
        left = {"type": "object", "properties": {"x": {"type": "string"}}}
        self.assertTrue(schemas_equal(left, dict(left)))
        self.assertFalse(schemas_equal(left, {"type": "array"}))

        program = Program(
            records=[
                RecordDecl(
                    "Status",
                    (
                        Field("label", "Text"),
                        Field("count", "Number"),
                        Field("ok", "Bool"),
                    ),
                )
            ]
        )
        self.assertEqual(
            schema_for_record(program, "Status"),
            {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "count": {"type": "number"},
                    "ok": {"type": "boolean"},
                },
                "required": ["label", "count", "ok"],
                "additionalProperties": False,
            },
        )
        with self.assertRaisesRegex(ValueError, "Unknown record"):
            schema_for_record(program, "Missing")
        with self.assertRaisesRegex(ValueError, "Unsupported record field type"):
            schema_for_record(
                Program(records=[RecordDecl("Bad", (Field("value", "Bytes"),))]),
                "Bad",
            )


if __name__ == "__main__":
    unittest.main()
