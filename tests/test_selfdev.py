import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kosika import cli
from kosika.selfdev import (
    DelegateProposer,
    Edit,
    Ledger,
    Proposal,
    ProposalError,
    SelfDevEngine,
    apply_edits,
    build_prompt,
    parse_proposal,
    rank,
    restore,
    run_gate,
    snapshot,
    survey,
)

MODULE = """# TODO: give this a docstring

def add(left, right):
    return left + right
"""


def tree(directory: Path) -> Path:
    """A small but real package, so the import check has something to import."""

    (directory / "kosika" / "sample").mkdir(parents=True)
    (directory / "kosika" / "__init__.py").write_text("", encoding="utf-8")
    (directory / "kosika" / "sample" / "__init__.py").write_text("", encoding="utf-8")
    (directory / "kosika" / "sample" / "module.py").write_text(MODULE, encoding="utf-8")
    (directory / "tests").mkdir()
    (directory / "KOSIKA_ROADMAP.md").write_text(
        "# Roadmap\n\n## Phase 1\n\n- [ ] native tensors\n- [x] done thing\n",
        encoding="utf-8",
    )
    return directory


class FixedProposer:
    backend = "fake"
    in_place = False

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts: list[str] = []

    def propose(self, task, prompt):
        self.prompts.append(prompt)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class SurveyTests(unittest.TestCase):
    def test_survey_finds_markers_unimplemented_and_roadmap_items(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory))
            (root / "kosika" / "gap.py").write_text(
                "def planned():\n    raise NotImplementedError\n", encoding="utf-8"
            )
            found = survey(root)
            kinds = {task.kind for task in found}
            self.assertIn("marker", kinds)
            self.assertIn("unimplemented", kinds)
            self.assertIn("roadmap", kinds)
            titles = " ".join(task.title for task in found)
            self.assertIn("give this a docstring", titles)
            self.assertIn("planned", titles)
            self.assertIn("native tensors", titles)
            self.assertNotIn("done thing", titles)

    def test_a_task_keeps_its_id_when_unrelated_lines_are_added(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory))
            before = survey(root)
            module = root / "kosika" / "sample" / "module.py"
            module.write_text(
                '"Added above."\n\n' + MODULE, encoding="utf-8"
            )
            after = survey(root)
            self.assertEqual(
                [task.id for task in before], [task.id for task in after]
            )
            self.assertNotEqual(before[0].line, after[0].line)

    def test_survey_ignores_a_marker_inside_a_string_literal(self) -> None:
        """A test fixture that writes a TODO writes a string, not a note."""

        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory))
            (root / "kosika" / "fixture.py").write_text(
                'TEMPLATE = """# TODO: imaginary work"""\n', encoding="utf-8"
            )
            found = survey(root)
            self.assertEqual(
                [task.path for task in found if task.path.endswith("fixture.py")], []
            )

    def test_survey_only_counts_a_marker_that_opens_the_comment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory))
            (root / "kosika" / "aside.py").write_text(
                "speed = 1  # see TODO: later, for speed\n", encoding="utf-8"
            )
            found = survey(root)
            self.assertEqual(
                [task.path for task in found if task.path.endswith("aside.py")], []
            )

    def test_a_messaged_not_implemented_error_is_an_error_path_not_a_stub(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory))
            (root / "kosika" / "guard.py").write_text(
                "def run(implementation):\n"
                "    if implementation is None:\n"
                '        raise NotImplementedError("no implementation was given")\n'
                "    return implementation()\n",
                encoding="utf-8",
            )
            found = survey(root)
            self.assertEqual(
                [task.path for task in found if task.path.endswith("guard.py")], []
            )

    def test_survey_ignores_a_marker_that_is_prose(self) -> None:
        """A document naming TODO while describing this scanner is not work."""

        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory))
            (root / "kosika" / "prose.py").write_text(
                '"""Names TODO and FIXME, and ``raise NotImplementedError``."""\n'
                "\n"
                "raise NotImplementedError  # in a string? no: module level\n",
                encoding="utf-8",
            )
            found = survey(root)
            prose = [task for task in found if task.path.endswith("prose.py")]
            self.assertEqual([task.kind for task in prose], ["unimplemented"])
            self.assertEqual(prose[0].title, "unimplemented: module level")


class RankTests(unittest.TestCase):
    def test_rank_drops_kept_and_given_up_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory))
            (root / "kosika" / "sample" / "other.py").write_text(
                "# FIXME: the second thing\n", encoding="utf-8"
            )
            tasks = survey(root)
            ledger = Ledger(Path(directory) / "ledger.jsonl")
            kept, stubborn = tasks[0], tasks[1]
            ledger.append({"task": kept.id, "kind": kept.kind, "status": "kept"})
            for _ in range(3):
                ledger.append(
                    {"task": stubborn.id, "kind": stubborn.kind, "status": "reverted"}
                )
            remaining = rank(tasks, ledger, max_task_failures=3)
            self.assertNotIn(kept.id, [task.id for task in remaining])
            self.assertNotIn(stubborn.id, [task.id for task in remaining])
            self.assertTrue(remaining)


class ProposalTests(unittest.TestCase):
    def test_parse_reads_whole_file_and_span_edits(self) -> None:
        proposal = parse_proposal(
            'text first {"edits": [{"path": "a.py", "find": "x", "replace": "y"},'
            ' {"path": "b.py", "content": "whole"}], "note": "ok"}'
        )
        self.assertEqual(len(proposal.edits), 2)
        self.assertEqual(proposal.edits[0].find, "x")
        self.assertIsNone(proposal.edits[1].find)
        self.assertEqual(proposal.note, "ok")

    def test_parse_refuses_an_answer_that_is_not_one_object(self) -> None:
        with self.assertRaises(ProposalError):
            parse_proposal("I cannot help with that.")

    def test_apply_refuses_a_path_outside_what_may_be_edited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory))
            with self.assertRaises(ProposalError) as caught:
                apply_edits(root, [Edit("README.md", "a", "b")], allow=("kosika",))
            self.assertIn("outside", str(caught.exception))
            with self.assertRaises(ProposalError):
                apply_edits(root, [Edit("../escape.py", "a", "b")], allow=("kosika",))

    def test_apply_refuses_an_ambiguous_span_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory))
            module = root / "kosika" / "sample" / "module.py"
            original = module.read_bytes()
            with self.assertRaises(ProposalError) as caught:
                apply_edits(
                    root,
                    [Edit("kosika/sample/module.py", "left", "right")],
                    allow=("kosika",),
                )
            self.assertIn("2 times", str(caught.exception))
            self.assertEqual(module.read_bytes(), original)

    def test_apply_keeps_the_file_line_endings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory))
            module = root / "kosika" / "sample" / "module.py"
            module.write_bytes(b"one\r\ntwo\r\n")
            apply_edits(
                root,
                [Edit("kosika/sample/module.py", "one\ntwo", "one\nand two")],
                allow=("kosika",),
            )
            self.assertEqual(module.read_bytes(), b"one\r\nand two\r\n")


class GateTests(unittest.TestCase):
    def test_gate_fails_a_file_that_does_not_compile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory))
            (root / "kosika" / "sample" / "broken.py").write_text(
                "def broken(:\n", encoding="utf-8"
            )
            result = run_gate(root, ["kosika/sample/broken.py"])
            self.assertFalse(result.ok)
            self.assertEqual(result.first_failure().name, "compile")

    def test_gate_passes_a_clean_change_and_runs_the_owners_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory))
            result = run_gate(
                root,
                ["kosika/sample/module.py"],
                extra=(
                    f'{sys.executable} -c "import sys; sys.exit(0)"',
                ),
            )
            self.assertTrue(result.ok, [step.to_dict() for step in result.steps])


class EngineTests(unittest.TestCase):
    def engine(self, root: Path, proposer, **settings) -> SelfDevEngine:
        return SelfDevEngine(root, proposer, allow=("kosika", "tests"), **settings)

    def test_a_change_that_passes_is_kept_and_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory))
            task = survey(root)[0]
            proposer = FixedProposer(
                [
                    Proposal(
                        edits=(
                            Edit(
                                "kosika/sample/module.py",
                                "# TODO: give this a docstring",
                                '"""Arithmetic a reader can trust."""',
                            ),
                        )
                    )
                ]
            )
            attempt = self.engine(root, proposer).attempt(task)
            self.assertEqual(attempt.status, "kept", attempt.note)
            self.assertIn("Arithmetic", (root / "kosika/sample/module.py").read_text())
            ledger = Ledger(default_state(root))
            records = ledger.records()
            self.assertEqual(records[-1]["status"], "kept")
            self.assertEqual(records[-1]["task"], task.id)

    def test_a_change_that_fails_is_undone_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory))
            module = root / "kosika" / "sample" / "module.py"
            original = module.read_bytes()
            task = survey(root)[0]
            proposer = FixedProposer(
                [
                    Proposal(
                        edits=(Edit("kosika/sample/module.py", "return left + right", "return left +"),)
                    ),
                    Proposal(
                        edits=(Edit("kosika/sample/module.py", "return left + right", "return left -"),)
                    ),
                ]
            )
            attempt = self.engine(root, proposer, max_attempts=2).attempt(task)
            self.assertEqual(attempt.status, "reverted")
            self.assertEqual(module.read_bytes(), original)
            self.assertEqual(attempt.gate.first_failure().name, "compile")

    def test_the_failure_is_given_to_the_next_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory))
            task = survey(root)[0]
            proposer = FixedProposer(
                [
                    Proposal(edits=(Edit("kosika/sample/module.py", "return left + right", "return +"),)),
                    Proposal(edits=(Edit("kosika/sample/module.py", "return +", "return left + right"),)),
                ]
            )
            self.engine(root, proposer, max_attempts=2).attempt(task)
            self.assertEqual(len(proposer.prompts), 2)
            self.assertIn("WHAT WENT WRONG LAST TIME", proposer.prompts[1])
            self.assertIn("compile", proposer.prompts[1])

    def test_a_delegate_that_edits_outside_what_is_allowed_is_undone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory))
            (root / "README.md").write_text("kept", encoding="utf-8")
            command = [
                sys.executable,
                "-c",
                "open('README.md', 'w', encoding='utf-8').write('rewritten')",
            ]
            proposer = DelegateProposer(command, root=root)
            task = survey(root)[0]
            attempt = self.engine(root, proposer).attempt(task)
            self.assertEqual(attempt.status, "refused")
            self.assertIn("README.md", attempt.note)
            self.assertEqual((root / "README.md").read_text(encoding="utf-8"), "kept")

    def test_a_dry_run_asks_nothing_and_shows_the_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory))
            task = survey(root)[0]
            proposer = FixedProposer([])
            attempt = self.engine(root, proposer, dry_run=True).attempt(task)
            self.assertEqual(attempt.status, "proposed")
            self.assertIn("kosika/sample/module.py", attempt.note)
            self.assertIn('"edits"', attempt.note)
            self.assertEqual(proposer.prompts, [])

    def test_a_dry_run_records_nothing_and_asks_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory))
            engine = self.engine(root, FixedProposer([]), dry_run=True)
            summary = engine.run(cycles=4)
            self.assertEqual(summary["proposed"], 1)
            self.assertEqual(summary["refused"], 0)
            self.assertEqual(Ledger(default_state(root)).records(), [])

    def test_a_run_stops_after_repeated_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory))
            answers = [
                Proposal(edits=(Edit("kosika/sample/module.py", "return left + right", "return +"),))
                for _ in range(6)
            ]
            summary = self.engine(
                root, FixedProposer(answers), max_attempts=1
            ).run(cycles=6, max_consecutive_failures=2)
            self.assertEqual(summary["stopped"][:5], "2 att")
            self.assertEqual(summary["kept"], 0)

    def test_a_roadmap_task_is_given_the_code_map_not_the_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory))
            roadmap = next(task for task in survey(root) if task.kind == "roadmap")
            prompt = build_prompt(root, roadmap, allow=("kosika",))
            self.assertIn("WHERE THE WORK BELONGS", prompt)
            self.assertIn("kosika/sample/module.py", prompt)
            self.assertNotIn("native tensors\n", prompt.split("WHERE THE WORK")[1])

    def test_the_excerpt_can_be_shortened_for_a_slow_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory))
            body = "\n".join(f"value_{index} = {index}" for index in range(400))
            (root / "kosika" / "sample" / "long.py").write_text(
                "# TODO: shorten this\n" + body + "\n", encoding="utf-8"
            )
            task = next(
                item for item in survey(root) if item.path.endswith("long.py")
            )
            whole = build_prompt(root, task, allow=("kosika",))
            window = build_prompt(root, task, allow=("kosika",), excerpt_chars=2000)
            self.assertLess(len(window), len(whole))
            self.assertIn("(lines", window)

    def test_the_prompt_names_the_file_the_rules_and_the_answer_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory))
            task = survey(root)[0]
            prompt = build_prompt(root, task, allow=("kosika",), notes=("too broad",))
            self.assertIn("kosika/sample/module.py", prompt)
            self.assertIn("WHAT MAY BE CHANGED", prompt)
            self.assertIn("exactly once", prompt)
            self.assertIn("too broad", prompt)


class SnapshotTests(unittest.TestCase):
    def test_restore_removes_a_file_that_was_new_and_puts_back_one_that_changed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory))
            added = root / "kosika" / "sample" / "new.py"
            module = root / "kosika" / "sample" / "module.py"
            before = snapshot(root, ["kosika/sample/new.py", "kosika/sample/module.py"])
            added.write_text("new", encoding="utf-8")
            module.write_text("changed", encoding="utf-8")
            self.assertEqual(
                sorted(restore(root, before)),
                ["kosika/sample/module.py", "kosika/sample/new.py"],
            )
            self.assertFalse(added.exists())
            self.assertEqual(module.read_text(encoding="utf-8"), MODULE)


class CliTests(unittest.TestCase):
    def call(self, *arguments: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with (
            patch.object(sys, "argv", ["kosika", *arguments]),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = cli.main()
        return code, out.getvalue(), err.getvalue()

    def test_survey_prints_the_work_as_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory))
            code, out, _ = self.call("selfdev", "survey", "--root", str(root), "--format", "json")
            self.assertEqual(code, 0)
            payload = json.loads(out)
            self.assertTrue(payload["tasks"])
            self.assertIn("id", payload["tasks"][0])

    def test_run_without_a_proposer_explains_what_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory))
            code, _, err = self.call("selfdev", "run", "--root", str(root))
            self.assertEqual(code, 2)
            self.assertIn("--model", err)

    def test_status_reports_an_empty_ledger_without_failing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory))
            code, out, _ = self.call("selfdev", "status", "--root", str(root))
            self.assertEqual(code, 0)
            self.assertIn("attempts", out)


def default_state(root: Path) -> Path:
    from kosika.selfdev import LEDGER_NAME, default_state_folder

    return default_state_folder(root) / LEDGER_NAME


if __name__ == "__main__":
    unittest.main()
