"""Tests for the repair corpus.

The corpus is supervision, so its labels have to be trustworthy above all
else. These tests protect three properties: every label is what the checker
actually reports, no case contradicts the intent of the mutation that produced
it, and every case round-trips through the serialised form intact.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from kosika.corpus import (
    CORPUS_SCHEMA,
    CorpusCase,
    Edit,
    Mutation,
    build_corpus,
    corpus_sources,
    corpus_summary,
    declarations,
    mutations,
    to_jsonl,
)
from kosika.diagnostics import CODES, analyze

REPO_ROOT = Path(__file__).resolve().parent.parent

_CACHE: list[CorpusCase] | None = None


def corpus() -> list[CorpusCase]:
    global _CACHE
    if _CACHE is None:
        _CACHE = build_corpus(REPO_ROOT)
    return _CACHE


class DeclarationDiscoveryTests(unittest.TestCase):
    """Declaration scanning underpins every operator, so it is tested directly."""

    def test_finds_declarations_with_and_without_headers(self) -> None:
        source = (REPO_ROOT / "examples" / "hello_world.kos").read_text("utf-8")
        found = [(d.kind, d.name) for d in declarations(source)]
        # A function header carries parameters and a return type, so a scanner
        # that assumed `name {` would silently miss it.
        self.assertEqual(
            found, [("record", "Greeting"), ("fn", "greeting"), ("workflow", "main")]
        )

    def test_body_lines_are_captured(self) -> None:
        source = (REPO_ROOT / "examples" / "hello_world.kos").read_text("utf-8")
        record = declarations(source)[0]
        self.assertIn("text: Text", "\n".join(record.body))

    def test_declaration_end_is_correct(self) -> None:
        source = (REPO_ROOT / "examples" / "hello_world.kos").read_text("utf-8")
        lines = source.splitlines()
        for decl in declarations(source):
            self.assertEqual(lines[decl.end_line].strip(), "}")

    def test_finds_every_declaration_in_a_rich_example(self) -> None:
        source = (REPO_ROOT / "examples" / "research_assistant.kos").read_text("utf-8")
        kinds = {d.kind for d in declarations(source)}
        self.assertLessEqual({"record", "model", "tool", "agent", "workflow"}, kinds)


class MutationTests(unittest.TestCase):
    def test_mutations_never_return_the_original(self) -> None:
        for path in corpus_sources(REPO_ROOT):
            source = path.read_text("utf-8")
            if analyze(source):
                continue
            for mutation in mutations(source):
                self.assertNotEqual(
                    mutation.source,
                    source,
                    f"{path.name}: {mutation.intent} produced no change",
                )

    def test_duplicate_step_name_preserves_the_opening_brace(self) -> None:
        # An earlier version rebuilt the whole step line and dropped the brace,
        # turning a duplicate-name error into a spurious parse error.
        found = False
        for path in corpus_sources(REPO_ROOT):
            source = path.read_text("utf-8")
            for mutation in mutations(source):
                if mutation.intent == "workflow.duplicate-step-name":
                    found = True
                    step_lines = [
                        line
                        for line in mutation.source.splitlines()
                        if line.strip().startswith("step ")
                    ]
                    for line in step_lines:
                        self.assertTrue(
                            line.rstrip().endswith("{"),
                            f"{path.name}: step line lost its brace: {line!r}",
                        )
        self.assertTrue(found, "no example exercised duplicate-step-name")

    def test_intent_match_helper(self) -> None:
        edit = Edit(start=0, removed=0)
        assert_match = Mutation("x", "y", edit, frozenset({"check/missing-field"}))
        self.assertTrue(assert_match.matches("check/missing-field"))
        self.assertFalse(assert_match.matches("check/unknown-field"))
        self.assertIsNone(Mutation("x", "y", edit).matches("check/missing-field"))
        self.assertIsNone(Mutation("x", "y", edit, frozenset({"a"})).matches(None))


class CorpusIntegrityTests(unittest.TestCase):
    def test_corpus_is_substantial(self) -> None:
        self.assertGreater(len(corpus()), 200)

    def test_no_case_contradicts_its_mutation_intent(self) -> None:
        # This is the guard against a broken operator producing a confident but
        # false label: the single most damaging defect a repair corpus can have.
        mislabelled = [case for case in corpus() if case.is_mislabelled]
        self.assertEqual(
            [(case.mutation, case.code) for case in mislabelled],
            [],
            "operators produced diagnostics they did not intend",
        )

    def test_every_label_is_reproducible_from_its_source(self) -> None:
        # A stored label must be what the checker says today, not what it said
        # when the corpus was generated.
        for case in corpus():
            observed = analyze(case.source, path=case.origin)
            if case.is_valid:
                self.assertEqual(observed, [], f"{case.case_id} should be clean")
            else:
                self.assertEqual(len(observed), 1, f"{case.case_id} should raise one")
                self.assertEqual(observed[0].code, case.code, case.case_id)

    def test_every_case_code_is_registered(self) -> None:
        for case in corpus():
            if case.code is not None:
                self.assertIn(case.code, CODES, case.case_id)

    def test_valid_cases_are_genuinely_valid(self) -> None:
        valid = [case for case in corpus() if case.is_valid]
        self.assertGreater(len(valid), 10)
        for case in valid:
            self.assertEqual(case.mutation, "none")
            self.assertIsNone(case.diagnostic)

    def test_case_ids_are_unique(self) -> None:
        ids = [case.case_id for case in corpus()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_no_repair_case_equals_its_origin(self) -> None:
        for case in corpus():
            if case.is_valid:
                continue
            original = (REPO_ROOT / case.origin).read_text("utf-8")
            self.assertNotEqual(case.source, original, case.case_id)


class CoverageTests(unittest.TestCase):
    def test_summary_shape(self) -> None:
        summary = corpus_summary(corpus())
        self.assertEqual(summary["schema"], CORPUS_SCHEMA)
        self.assertEqual(
            summary["cases"], summary["valid_cases"] + summary["repair_cases"]
        )
        self.assertEqual(summary["mislabelled_cases"], 0)

    def test_capability_codes_are_represented(self) -> None:
        # The capability failures are the ones an editing agent most needs, so
        # the corpus must actually contain them.
        codes = {case.code for case in corpus()}
        for expected in (
            "check/capability-denied",
            "check/agent-conflict",
            "check/unknown-model",
            "check/unknown-tool",
        ):
            self.assertIn(expected, codes)

    def test_covers_a_reasonable_share_of_the_taxonomy(self) -> None:
        summary = corpus_summary(corpus())
        self.assertGreaterEqual(summary["distinct_codes"], 20)

    def test_uncovered_codes_are_reported_not_hidden(self) -> None:
        summary = corpus_summary(corpus())
        self.assertEqual(
            set(summary["codes"]) | set(summary["uncovered_codes"]),
            set(CODES),
        )


class SerializationTests(unittest.TestCase):
    def test_jsonl_round_trips(self) -> None:
        lines = to_jsonl(corpus()).splitlines()
        self.assertEqual(len(lines), len(corpus()))
        for line, case in zip(lines, corpus()):
            payload = json.loads(line)
            self.assertEqual(payload["id"], case.case_id)
            self.assertEqual(payload["source"], case.source)
            self.assertEqual(payload["valid"], case.is_valid)

    def test_case_dict_is_serialisable(self) -> None:
        for case in corpus()[:20]:
            json.dumps(case.to_dict())


class RepairTests(unittest.TestCase):
    """A reference repair has to be correct, not merely present."""

    def test_every_repair_case_carries_a_repair(self) -> None:
        for case in corpus():
            if case.is_valid:
                self.assertIsNone(case.fix_source, case.case_id)
                continue
            self.assertIsNotNone(case.fix_source, case.case_id)
            self.assertIsNotNone(case.fix_edit, case.case_id)

    def test_stored_patch_reproduces_the_repair(self) -> None:
        from kosika.corpus import source_lines

        for case in corpus():
            if case.fix_edit is None or case.fix_source is None:
                continue
            edit = Edit(**case.fix_edit)
            applied = "\n".join(edit.apply(source_lines(case.source)))
            self.assertEqual(applied, case.fix_source, case.case_id)

    def test_every_repair_produces_a_clean_program(self) -> None:
        # A repair that still fails to check is not a repair.
        for case in corpus():
            if case.fix_source is None:
                continue
            self.assertEqual(
                analyze(case.fix_source, path=case.origin),
                [],
                f"{case.case_id}: repair still fails",
            )

    def test_repair_restores_the_origin_exactly(self) -> None:
        # The reference repair must restore the program byte for byte. This is
        # why the corpus splits on the separator rather than using
        # str.splitlines, which would drop a trailing blank line.
        for case in corpus():
            if case.fix_source is None:
                continue
            original = (REPO_ROOT / case.origin).read_text("utf-8")
            self.assertEqual(case.fix_source, original, case.case_id)

    def test_repair_differs_from_the_broken_source(self) -> None:
        for case in corpus():
            if case.fix_source is None:
                continue
            self.assertNotEqual(case.fix_source, case.source, case.case_id)


class EditTests(unittest.TestCase):
    def test_apply_and_revert_round_trip(self) -> None:
        from kosika.corpus import Edit

        lines = ["a", "b", "c"]
        for edit in (
            Edit(start=0, removed=0, inserted=("x",)),
            Edit(start=1, removed=1, inserted=("y", "z")),
            Edit(start=0, removed=3, inserted=()),
            Edit(start=2, removed=1, inserted=("q",)),
        ):
            mutated = edit.apply(lines)
            self.assertEqual(edit.revert(lines).apply(mutated), lines)

    def test_apply_and_revert_preserve_trailing_newline(self) -> None:
        from kosika.corpus import source_lines

        original = "a\nb\n"
        original_lines = source_lines(original)
        edit = Edit(start=1, removed=1)
        mutated = "\n".join(edit.apply(original_lines))
        inverse = edit.revert(original_lines)

        self.assertEqual(
            "\n".join(inverse.apply(source_lines(mutated))),
            original,
        )

    def test_insertion_and_deletion_are_distinct_shapes(self) -> None:
        from kosika.corpus import Edit

        self.assertEqual(
            Edit(start=1, removed=0, inserted=("x",)).apply(["a"]), ["a", "x"]
        )
        self.assertEqual(Edit(start=0, removed=1).apply(["a", "b"]), ["b"])

    def test_edit_serialises(self) -> None:
        import json

        from kosika.corpus import Edit

        payload = Edit(start=1, removed=2, inserted=("x",)).to_dict()
        json.dumps(payload)
        self.assertEqual(payload["start"], 1)
        self.assertEqual(payload["removed"], 2)
        self.assertEqual(payload["inserted"], ["x"])


class CommandLineTests(unittest.TestCase):
    """The corpus must be obtainable without importing the module."""

    def _run(self, *arguments: str) -> tuple[int, str, str]:
        import subprocess
        import sys

        process = subprocess.run(
            [sys.executable, "-m", "kosika.cli", *arguments],
            capture_output=True,
            cwd=str(REPO_ROOT),
            timeout=300,
        )
        return (
            process.returncode,
            process.stdout.decode("utf-8"),
            process.stderr.decode("utf-8"),
        )

    def test_summary_json(self) -> None:
        code, out, _ = self._run("corpus", "--summary", "--format", "json")
        summary = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(summary["schema"], CORPUS_SCHEMA)
        self.assertEqual(summary["mislabelled_cases"], 0)
        self.assertGreater(summary["cases"], 200)

    def test_check_flag_passes_on_an_intact_corpus(self) -> None:
        code, _, err = self._run("corpus", "--check")
        self.assertEqual(code, 0, err)

    def test_writes_jsonl(self) -> None:
        import re
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "corpus.jsonl"
            code, out, err = self._run("corpus", "--output", str(path))
            self.assertEqual(code, 0, err)
            lines = path.read_text("utf-8").splitlines()
        reported = re.search(r"\((\d+) cases\)", out)
        self.assertIsNotNone(reported, f"unexpected output: {out!r}")
        assert reported is not None
        self.assertEqual(len(lines), int(reported.group(1)))
        for line in lines[:10]:
            payload = json.loads(line)
            self.assertIn("id", payload)
            self.assertIn("source", payload)
            self.assertIn("valid", payload)


if __name__ == "__main__":
    unittest.main()
