"""A step-grammar failure must name the rule, not only the expected token.

A model repairing its own program reads the diagnostic, not the language guide,
so "Expected 'execute' at 4:13" is unsolvable for it: the token it was missing
is a symptom, and the rule it broke is the repair. These tests pin that each
step-grammar failure states the rule, keeps a stable code an agent can branch
on, and still reports the ``expected``/``actual`` pair the repair layer uses.
"""

from __future__ import annotations

import unittest

from kosika.diagnostics import Diagnostic, analyze

TWO_STATEMENTS = """record Out { text: Text }
fn make() -> Out { return {text: "x"} }
workflow main() -> Out {
  step go {
    let out = make()
    var again = out
    return again
  }
}
"""

VALUE_AS_STEP = """record Out { text: Text }
workflow main() -> Out {
  step go {
    let count = 1
    return count
  }
}
"""

RETURN_IN_WORKFLOW = """record Out { text: Text }
fn make() -> Out { return {text: "x"} }
workflow main() -> Out {
  out = make()
  return out
}
"""

WRONG_RETURN_NAME = """record Out { text: Text }
fn make() -> Out { return {text: "x"} }
workflow main() -> Out {
  step go { let out = make() return other }
}
"""

PLAIN_SYNTAX_ERROR = "record Result { value Text }\n"


def only(source: str) -> Diagnostic:
    diagnostics = analyze(source)
    if len(diagnostics) != 1:
        raise AssertionError(f"expected one diagnostic, got {diagnostics}")
    return diagnostics[0]


class StepGrammarDiagnosticTests(unittest.TestCase):
    """The rule is in the message, and the code still says which rule it was."""

    def test_a_second_statement_names_the_step_block_rule(self) -> None:
        diagnostic = only(TWO_STATEMENTS)
        self.assertEqual(diagnostic.code, "parse/step-return")
        self.assertIn("a step block is one call", diagnostic.message)
        self.assertIn("put any other statement in a fn", diagnostic.message)
        self.assertEqual(diagnostic.data["expected"], "return")
        self.assertEqual(diagnostic.data["actual"], "var")
        self.assertEqual(diagnostic.data["phase"], "parse")

    def test_a_plain_value_as_a_step_body_names_the_call_rule(self) -> None:
        diagnostic = only(VALUE_AS_STEP)
        self.assertEqual(diagnostic.code, "parse/step-call")
        self.assertIn("a step's body must be one call", diagnostic.message)
        self.assertIn("work a plain value out in a fn", diagnostic.message)
        self.assertEqual(diagnostic.data["expected"], "execute")
        self.assertEqual(diagnostic.data["actual"], "1")

    def test_a_return_in_a_workflow_body_names_the_one_line_rule(self) -> None:
        diagnostic = only(RETURN_IN_WORKFLOW)
        self.assertEqual(diagnostic.code, "parse/workflow-item")
        self.assertIn("a workflow step is one line", diagnostic.message)
        self.assertIn("the last step's value is the result", diagnostic.message)

    def test_a_wrong_return_name_states_the_rule_too(self) -> None:
        diagnostic = only(WRONG_RETURN_NAME)
        self.assertEqual(diagnostic.code, "check/missing-return")
        self.assertIn("a step block is one call followed by", diagnostic.message)

    def test_an_unrelated_syntax_error_keeps_the_generic_code(self) -> None:
        diagnostic = only(PLAIN_SYNTAX_ERROR)
        self.assertEqual(diagnostic.code, "parse/expected-token")
        self.assertEqual(diagnostic.data["expected"], ":")
        self.assertEqual(diagnostic.data["actual"], "Text")


if __name__ == "__main__":
    unittest.main()
