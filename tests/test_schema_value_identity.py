import unittest

from kosika.schema import SchemaValidationError, schemas_equal, validate


class SchemaValueIdentityTests(unittest.TestCase):
    def test_boolean_is_not_a_numeric_constant_or_enum_member(self):
        for actual, expected in [
            (True, 1),
            (False, 0),
            (1, True),
            (0, False),
            ({"x": [True]}, {"x": [1]}),
        ]:
            for schema in ({"const": expected}, {"enum": [expected]}):
                with self.subTest(actual=actual, schema=schema):
                    with self.assertRaises(SchemaValidationError):
                        validate(actual, schema)

    def test_numeric_equivalence_and_structural_equality_are_preserved(self):
        for value in (1, 1.0):
            validate(value, {"const": 1})
            validate(value, {"enum": [1.0]})
        validate(
            {"b": [None, False], "a": 1.0}, {"const": {"a": 1, "b": [None, False]}}
        )
        self.assertTrue(schemas_equal({"const": 1}, {"const": 1.0}))

    def test_schema_pinning_distinguishes_booleans_from_numbers(self):
        for left, right in [
            ({"const": True}, {"const": 1}),
            ({"enum": [False]}, {"enum": [0]}),
            (
                {"properties": {"x": {"const": True}}},
                {"properties": {"x": {"const": 1}}},
            ),
        ]:
            with self.subTest(left=left, right=right):
                self.assertFalse(schemas_equal(left, right))

    def test_nonfinite_numbers_are_rejected(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            for schema in ({"type": "number"}, {"type": ["null", "number"]}):
                with self.subTest(value=value, schema=schema):
                    with self.assertRaises(SchemaValidationError):
                        validate(value, schema)
            with self.assertRaises(SchemaValidationError):
                validate(
                    {"x": [value]},
                    {
                        "type": "object",
                        "properties": {
                            "x": {"type": "array", "items": {"type": "number"}}
                        },
                    },
                )
        validate(10**1000, {"type": "number"})


if __name__ == "__main__":
    unittest.main()
