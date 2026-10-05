"""One-line steps, short arguments, and "did you mean" hints."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kosika.builtin_methods import did_you_mean
from kosika.checker import CheckError, check
from kosika.development import check_project
from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.modules import load_program
from kosika.parser import ParseError, parse
from kosika.runtime import replay, run

DECLARATIONS = """
record Question { text: Text }
record Answer { text: Text }
record Report { text: Text length: Number }

model assistant { input: Question output: Answer }

fn describe(answer: Answer) -> Report {
  return {text: answer.text, length: answer.text.length()}
}
"""

SHORT = (
    DECLARATIONS
    + """
workflow main(question: Question) -> Report {
  answer = use_model assistant(question.text)
  report = describe(answer)
}
"""
)

LONG = (
    DECLARATIONS
    + """
workflow main(question: Question) -> Report {
  step answer {
    let answer = use_model assistant(text: question.text)
    return answer
  }
  step report {
    let report = describe(answer: answer)
    return report
  }
}
"""
)


def error_of(source: str) -> str:
    try:
        check(parse(source))
    except (CheckError, ParseError) as exc:
        return str(exc)
    raise AssertionError("expected the program to be rejected")


class ShortFormTests(unittest.TestCase):
    def test_short_and_long_forms_are_the_same_program(self) -> None:
        self.assertEqual(parse(SHORT), parse(LONG))

    def test_the_short_form_runs_traces_and_replays(self) -> None:
        program = parse(SHORT)
        check(program)
        provider = ScriptedModelProvider(
            [ModelResponse(content=json.dumps({"text": "hello"}))]
        )
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            result = run(
                program,
                "main",
                trace,
                inputs={"question": {"text": "hi"}},
                model_bindings={"assistant": provider},
            )
            self.assertEqual(result, {"text": "hello", "length": 5})
            self.assertEqual(replay(trace), result)

    def test_both_step_forms_mix_and_approval_still_parses(self) -> None:
        mixed = SHORT.replace(
            "  report = describe(answer)",
            "  step report {\n    let report: Report = describe(answer)\n"
            "    return report\n  }\n  require approval",
        )
        workflow = parse(mixed).workflows[0]
        self.assertEqual([step.name for step in workflow.steps], ["answer", "report"])
        self.assertTrue(workflow.approval_required)

    def test_every_call_kind_has_a_one_line_form(self) -> None:
        source = """
record Draft { text: Text }
model writer { input: Draft output: Draft }
tool read_docs { source mcp "local_mock" operation: "search" access: observe output: Draft }
agent reader { can: [read_docs] }
agent editor { model: writer }
workflow gather() -> Draft { found = execute reader.read_docs() }
workflow main() -> Draft {
  draft = call gather()
  edited = ask editor(draft.text)
}
"""
        program = parse(source)
        check(program)
        kinds = [type(step.call).__name__ for step in program.workflows[1].steps]
        self.assertEqual(kinds, ["WorkflowCall", "AgentCall"])
        self.assertEqual(type(program.workflows[0].steps[0].call).__name__, "ToolCall")

    def test_short_arguments_work_in_functions_and_methods(self) -> None:
        source = (
            DECLARATIONS
            + """
fn wrap(text: Text) -> Answer { return {text: text} }
fn twice(answer: Answer, part: Text) -> Report {
  let inner: Report = describe(answer)
  return {text: inner.text, length: if inner.text.contains(part) { 2 } else { 1 }}
}
workflow main(question: Question) -> Report {
  answer = wrap(question.text)
  report = twice(answer, part: "h")
}
"""
        )
        program = parse(source)
        check(program)
        self.assertEqual(
            run(program, "main", None, inputs={"question": {"text": "hi"}}),
            {"text": "hi", "length": 2},
        )

    def test_a_short_argument_must_match_a_parameter(self) -> None:
        # A call's only argument binds to the callee's only parameter, so a
        # wrong value is a type error rather than an unknown name.
        wrong = SHORT.replace("describe(answer)", "describe(question)")
        self.assertIn(
            "argument 'answer' expects 'Answer', got 'Question'", error_of(wrong)
        )

    def test_several_unnamed_expressions_need_names(self) -> None:
        source = DECLARATIONS + (
            "fn pair(a: Number, b: Number) -> Report {\n"
            '  return {text: "x", length: a + b}\n}\n'
            "workflow main() -> Report { report = pair(1 + 2, 3) }\n"
        )
        self.assertIn("needs a name; write name: value", error_of(source))

    def test_a_one_line_step_cannot_reuse_a_name(self) -> None:
        duplicate = SHORT.replace(
            "report = describe(answer)", "answer = describe(answer)"
        )
        with self.assertRaises((CheckError, ParseError)):
            check(parse(duplicate))

    def test_an_error_in_a_one_line_step_has_a_line(self) -> None:
        wrong = SHORT.replace("describe(answer)", "describe(question)")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "main.kos"
            path.write_text(wrong, encoding="utf-8")
            diagnostic = check_project(path)["diagnostics"]["diagnostics"][0]
        self.assertEqual(diagnostic["owner"], "report")
        self.assertEqual(diagnostic["range"]["start"]["line"], 13)

    def test_the_starter_project_uses_the_short_form(self) -> None:
        from kosika.starter import STARTER_SOURCE, create_starter

        self.assertIn("answer = use_model assistant(question.text)", STARTER_SOURCE)
        with tempfile.TemporaryDirectory() as directory:
            create_starter(Path(directory) / "hello")
            check(load_program(Path(directory) / "hello" / "main.kos"))


class HintTests(unittest.TestCase):
    BASE = "record Order { customer: Text items: Text }\nrecord Out { n: Number }\n"
    MAIN = "workflow main(order: Order) -> Out { out = f(order) }\n"

    def message(self, body: str, main: str | None = None) -> str:
        function = f"fn f(order: Order) -> Out {{ {body} }}\n"
        return error_of(self.BASE + function + (main or self.MAIN))

    def test_typos_get_the_closest_known_spelling(self) -> None:
        cases = {
            'let p: Text[] = order.items.split(seperator: ",") return {n: 1}': "'separator'",
            "return {n: order.itms.length()}": "'items'",
            "return {n: order.items.lenght()}": "'length'",
            "let count: Number = 1 return {n: cuont}": "'count'",
        }
        for body, suggestion in cases.items():
            with self.subTest(body=body):
                self.assertIn(f"Did you mean {suggestion}?", self.message(body))

    def test_a_misspelled_argument_name_is_suggested(self) -> None:
        message = self.message(
            "return {n: 1}",
            "workflow main(order: Order) -> Out { out = f(ordr: order) }\n",
        )
        self.assertIn("Did you mean 'order'?", message)

    def test_a_misspelled_type_is_suggested(self) -> None:
        source = (
            "record Out { n: Numbr }\nfn f() -> Out { return {n: 1} }\n"
            "workflow main() -> Out { out = f() }\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "main.kos"
            path.write_text(source, encoding="utf-8")
            with self.assertRaises(CheckError) as raised:
                load_program(path)
        self.assertIn("Did you mean 'Number'?", str(raised.exception))

    def test_no_hint_when_nothing_is_close(self) -> None:
        self.assertNotIn("Did you mean", self.message("return {n: zzzzzz}"))
        self.assertEqual(did_you_mean("same", ["same"]), "")
        self.assertEqual(did_you_mean("x", []), "")


if __name__ == "__main__":
    unittest.main()
