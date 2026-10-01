"""Tests for the model repair adapter.

These use a scripted provider, so they are hermetic: no network, no model, and
every assertion is about the adapter's own behaviour -- patch extraction,
uniqueness enforcement, feedback, and accounting.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from kosika.corpus import CorpusCase
from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.repair import (
    DIVIDER_MARKER,
    MAX_INSERTED_LINES,
    MAX_REPLACEMENTS,
    REPLACE_MARKER,
    SEARCH_MARKER,
    ModelRepairSolver,
    PatchError,
    RepairStrategy,
    apply_replacement,
    build_messages,
    describe_diagnostic,
    parse_replacements,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Only used where the behaviour under test is patch mechanics rather than
#: checking. The solver tests use real corpus cases, because a hand-written
#: "failing" program that actually checks clean would make them vacuous.
SOURCE = 'record A {\n  x: Text\n}\n\nfn f() -> A {\n  return {x: "hi"}\n}\n'

_CASES: list | None = None


def corpus_cases() -> list:
    global _CASES
    if _CASES is None:
        from kosika.corpus import build_corpus

        _CASES = [case for case in build_corpus(REPO_ROOT) if case.is_repairable]
    return _CASES


def case_with_code(code: str):
    for case in corpus_cases():
        if case.code == code:
            return case
    raise AssertionError(f"no corpus case with code {code}")


#: A real capability failure, which is the case an editing agent most needs.
CAPABILITY_CASE = case_with_code("check/capability-denied")


def block(search: str, replace: str) -> str:
    return f"{SEARCH_MARKER}\n{search}\n{DIVIDER_MARKER}\n{replace}\n{REPLACE_MARKER}"


def gold_reply(case) -> str:
    """Express a case's own reference patch as a search/replace block.

    Derived from the stored fix so the tests never hand-write a patch that
    could drift away from the corpus.
    """

    from kosika.corpus import source_lines

    lines = source_lines(case.source)
    edit = case.fix_edit
    search = "\n".join(lines[edit["start"] : edit["start"] + edit["removed"]])
    replace = "\n".join(edit["inserted"])
    return block(search, replace)


class PatchParsingTests(unittest.TestCase):
    def test_parses_a_single_block(self) -> None:
        parsed = parse_replacements(block("old", "new"))
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].search, "old")
        self.assertEqual(parsed[0].replace, "new")

    def test_tolerates_surrounding_prose(self) -> None:
        text = f"Sure, here is the fix.\n{block('old', 'new')}\nHope that helps!"
        self.assertEqual(len(parse_replacements(text)), 1)

    def test_tolerates_a_code_fence(self) -> None:
        text = f"```\n{block('old', 'new')}\n```\n"
        self.assertEqual(len(parse_replacements(text)), 1)

    def test_parses_multiple_blocks(self) -> None:
        text = block("a", "A") + "\n" + block("b", "B")
        self.assertEqual(len(parse_replacements(text)), 2)

    def test_multiline_search_is_preserved(self) -> None:
        parsed = parse_replacements(block("line one\nline two", "replacement"))
        self.assertEqual(parsed[0].search, "line one\nline two")

    def test_empty_response_is_an_error(self) -> None:
        with self.assertRaises(PatchError):
            parse_replacements("")
        with self.assertRaises(PatchError):
            parse_replacements("   ")

    def test_prose_without_a_block_is_an_error(self) -> None:
        # Otherwise a model that chats instead of patching would score as a
        # solver that did nothing rather than as a formatting failure.
        with self.assertRaises(PatchError) as caught:
            parse_replacements("You should probably add a field here.")
        self.assertIn("no replacement block", str(caught.exception))

    def test_empty_search_is_rejected(self) -> None:
        with self.assertRaises(PatchError):
            parse_replacements(
                f"{SEARCH_MARKER}\n\n{DIVIDER_MARKER}\nnew\n{REPLACE_MARKER}"
            )

    def test_oversized_insertion_is_rejected(self) -> None:
        huge = "\n".join(f"line {index}" for index in range(MAX_INSERTED_LINES + 5))
        with self.assertRaises(PatchError) as caught:
            parse_replacements(block("old", huge))
        self.assertIn("limit is", str(caught.exception))

    def test_too_many_blocks_is_rejected(self) -> None:
        text = "\n".join(
            block(f"s{index}", f"r{index}") for index in range(MAX_REPLACEMENTS + 1)
        )
        with self.assertRaises(PatchError):
            parse_replacements(text)


class PatchApplicationTests(unittest.TestCase):
    def test_applies_and_derives_the_line_edit(self) -> None:
        # A model quoting the exact code produces the same line edit the corpus
        # stored, with no line numbers supplied by the model.
        source, edit = apply_replacement(
            CAPABILITY_CASE.source,
            parse_replacements(gold_reply(CAPABILITY_CASE))[0],
        )
        self.assertEqual(edit.to_dict(), CAPABILITY_CASE.fix_edit)
        self.assertEqual(source, CAPABILITY_CASE.fix_source)

    def test_absent_search_text_is_rejected(self) -> None:
        with self.assertRaises(PatchError) as caught:
            apply_replacement(SOURCE, parse_replacements(block("nowhere", "x"))[0])
        self.assertIn("does not appear", str(caught.exception))

    def test_ambiguous_search_text_is_rejected(self) -> None:
        # Applying to an arbitrary occurrence would look like a fix while
        # silently corrupting the program.
        source = "step a {\n  let x = 1\n  return x\n}\n" * 3
        with self.assertRaises(PatchError) as caught:
            apply_replacement(
                source, parse_replacements(block("  let x = 1", "  let x = 2"))[0]
            )
        self.assertIn("must be unique", str(caught.exception))

    def test_multiline_replacement_keeps_surrounding_lines(self) -> None:
        replacement = parse_replacements(block("record A {", "record A {\n  y: Text"))[
            0
        ]
        result, _edit = apply_replacement(SOURCE, replacement)
        self.assertIn("record A {\n  y: Text\n  x: Text", result)


class PromptTests(unittest.TestCase):
    def test_describe_diagnostic_includes_structured_data(self) -> None:
        text = describe_diagnostic(CAPABILITY_CASE.diagnostic)
        self.assertIn("check/capability-denied", text)
        self.assertIn("writer", text)
        self.assertIn("w", text)

    def test_describe_diagnostic_drops_noise_fields(self) -> None:
        text = describe_diagnostic(
            {
                "code": "check/missing-field",
                "message": "m",
                "data": {
                    "phase": "check",
                    "anchor_missing": True,
                    "missing_fields": ["a"],
                },
            }
        )
        self.assertNotIn("anchor_missing", text)
        self.assertNotIn("phase", text)
        self.assertIn("missing_fields", text)

    def test_contextual_strategy_shows_the_failing_region(self) -> None:
        messages = build_messages(
            CAPABILITY_CASE,
            CAPABILITY_CASE.diagnostic,
            strategy=RepairStrategy.CONTEXTUAL,
        )
        self.assertEqual(messages[0].role, "system")
        self.assertIn("check/capability-denied", messages[1].content)
        span = CAPABILITY_CASE.diagnostic["range"]
        # Both the region start and the failing line are stated, so the model
        # knows which part of the region to act on.
        self.assertIn(f"line {span['start']['line'] + 1}", messages[1].content)
        self.assertIn("from line", messages[1].content)

    def test_contextual_region_is_unnumbered_verbatim_source(self) -> None:
        # Numbered lines read well to a human, but the model quotes the numbers
        # back inside the search text and no patch ever matches. The shown
        # region must be byte-identical to the program it came from.
        from kosika.corpus import source_lines

        messages = build_messages(
            CAPABILITY_CASE,
            CAPABILITY_CASE.diagnostic,
            strategy=RepairStrategy.CONTEXTUAL,
        )
        body = messages[1].content.split("```kosika\n", 1)[1].split("\n```", 1)[0]
        self.assertIn(body, CAPABILITY_CASE.source)
        lines = source_lines(CAPABILITY_CASE.source)
        for line in body.splitlines():
            self.assertIn(line, lines)

    def test_full_strategy_shows_the_whole_program(self) -> None:
        long_source = "\n".join(f"# filler {index}" for index in range(200))
        case = CorpusCase(
            case_id="long",
            origin="examples/long.kos",
            mutation="none",
            source=long_source,
            diagnostic=CAPABILITY_CASE.diagnostic,
        )
        contextual = build_messages(
            case, case.diagnostic, strategy=RepairStrategy.CONTEXTUAL
        )
        full = build_messages(case, case.diagnostic, strategy=RepairStrategy.FULL)
        self.assertLess(len(contextual[1].content), len(full[1].content))
        self.assertIn("# filler 199", full[1].content)

    def test_feedback_is_included(self) -> None:
        messages = build_messages(
            CAPABILITY_CASE,
            CAPABILITY_CASE.diagnostic,
            feedback=("attempt 1: search text appears 2 times",),
        )
        self.assertIn("Attempts that did not work", messages[1].content)
        self.assertIn("appears 2 times", messages[1].content)

    def test_prompt_includes_the_diagnostic_guide(self) -> None:
        messages = build_messages(CAPABILITY_CASE, CAPABILITY_CASE.diagnostic)
        self.assertIn("check/capability-denied", messages[1].content)


class SolverTests(unittest.TestCase):
    def _solver(self, responses, **kwargs) -> ModelRepairSolver:
        return ModelRepairSolver(
            ScriptedModelProvider([ModelResponse(content=r) for r in responses]),
            max_attempts=kwargs.pop("max_attempts", 1),
            **kwargs,
        )

    def test_a_correct_patch_solves_the_case(self) -> None:
        solver = self._solver([gold_reply(CAPABILITY_CASE)])
        outcome = solver.repair(CAPABILITY_CASE)
        self.assertTrue(outcome.solved)
        self.assertEqual(outcome.model_calls, 1)
        self.assertTrue(outcome.attempts[0].applied)

    def test_the_callable_seam_returns_the_repaired_source(self) -> None:
        solver = self._solver([gold_reply(CAPABILITY_CASE)])
        result = solver(CAPABILITY_CASE)
        self.assertEqual(result, CAPABILITY_CASE.fix_source)

    def test_prose_only_response_fails_with_a_formatting_error(self) -> None:
        solver = self._solver(["I would add a field."])
        outcome = solver.repair(CAPABILITY_CASE)
        self.assertFalse(outcome.solved)
        self.assertFalse(outcome.attempts[0].applied)
        error = outcome.attempts[0].error
        self.assertIsNotNone(error)
        assert error is not None
        self.assertIn("no replacement block", error)

    def test_a_format_failure_is_retried_with_feedback(self) -> None:
        # The second response sees the first failure in its prompt, so a model
        # can correct its format rather than being retried blindly.
        provider = ScriptedModelProvider(
            [
                ModelResponse(content="no blocks here"),
                ModelResponse(content=gold_reply(CAPABILITY_CASE)),
            ]
        )
        solver = ModelRepairSolver(provider, max_attempts=2)
        outcome = solver.repair(CAPABILITY_CASE)
        self.assertTrue(outcome.solved)
        self.assertEqual(len(provider.requests), 2)
        self.assertIn(
            "Attempts that did not work", provider.requests[1].messages[1].content
        )

    def test_attempts_are_bounded(self) -> None:
        solver = self._solver(["nope", "still nope", "nope again"], max_attempts=2)
        outcome = solver.repair(CAPABILITY_CASE)
        self.assertEqual(outcome.model_calls, 2)
        self.assertFalse(outcome.solved)

    def test_zero_attempts_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ModelRepairSolver(ScriptedModelProvider([]), max_attempts=0)

    def test_a_correct_but_insufficient_patch_is_tried_again(self) -> None:
        # A patch that applies but leaves a failure must not count as solved.
        from kosika.corpus import source_lines

        case = case_with_code("check/missing-field")
        lines = source_lines(case.source)
        start = case.fix_edit["start"]
        # Touch a line the gold patch does not search for, so the second
        # attempt is still applicable. A bare comment is semantically inert, so
        # the same diagnostic remains afterwards.
        other = next(
            index
            for index, line in enumerate(lines)
            if line.strip() and index != start and lines.count(line) == 1
        )
        weak = block(lines[other], f"// left for later\n{lines[other]}")
        provider = ScriptedModelProvider(
            [ModelResponse(content=weak), ModelResponse(content=gold_reply(case))]
        )
        solver = ModelRepairSolver(provider, max_attempts=2)
        outcome = solver.repair(case)
        self.assertTrue(outcome.attempts[0].applied)
        self.assertTrue(outcome.attempts[0].remaining)
        self.assertTrue(outcome.solved, "the second attempt should have fixed it")
        self.assertEqual(outcome.model_calls, 2)

    def test_provider_errors_are_recorded_not_raised(self) -> None:
        class Failing:
            def complete(self, request):
                raise RuntimeError("connection refused")

        solver = ModelRepairSolver(Failing(), max_attempts=1)
        outcome = solver.repair(CAPABILITY_CASE)
        self.assertFalse(outcome.solved)
        error = outcome.attempts[0].error
        self.assertIsNotNone(error)
        assert error is not None
        self.assertIn("provider error", error)

    def test_repair_does_not_run_the_provider_twice(self) -> None:
        provider = ScriptedModelProvider(
            [ModelResponse(content=gold_reply(CAPABILITY_CASE))]
        )
        solver = ModelRepairSolver(provider, max_attempts=1)
        solver.repair(CAPABILITY_CASE)
        self.assertEqual(len(provider.requests), 1)

    def test_outcome_serialises(self) -> None:
        import json

        solver = self._solver([gold_reply(CAPABILITY_CASE)])
        json.dumps(solver.repair(CAPABILITY_CASE).to_dict())


class BenchmarkIntegrationTests(unittest.TestCase):
    def test_model_solver_is_scored_by_the_harness(self) -> None:
        from kosika.benchmark import score

        provider = ScriptedModelProvider(
            [ModelResponse(content=gold_reply(CAPABILITY_CASE))]
        )
        solver = ModelRepairSolver(provider, max_attempts=1)
        report = score([CAPABILITY_CASE], solver, name="model")
        self.assertEqual(report.solved, 1)
        self.assertEqual(report.score, 1.0)

    def test_a_useless_model_scores_zero(self) -> None:
        from kosika.benchmark import score

        provider = ScriptedModelProvider([ModelResponse(content="I cannot help.")])
        solver = ModelRepairSolver(provider, max_attempts=1)
        report = score([CAPABILITY_CASE], solver, name="model")
        self.assertEqual(report.score, 0.0)


if __name__ == "__main__":
    unittest.main()
