"""Tests for what a declared swarm does and does not promise.

A swarm declaration is checked, mapped, and required to carry ``require
approval``. What it cannot do yet is run. These tests pin that boundary so the
gap stays a stated limitation rather than a silent one: a program whose only
entry point is a swarm must say so in the error, and the mindmap must not claim
the swarm runs anything.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from kosika.ast import Program, SwarmDecl
from kosika.mindmap import build_mindmap
from kosika.modules import load_program
from kosika.runtime import _entry_point_error

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
SWARM_REVIEW = EXAMPLES / "swarm_review.kos"
HELLO = EXAMPLES / "hello_world.kos"


def program_from(text: str) -> Program:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "p.kos"
        path.write_text(text, encoding="utf-8")
        return load_program(path)


#: Variants are derived from the real example so the tool contract and the
#: declaration syntax stay whatever the checker actually requires, rather than a
#: second guess at them. Only the workflow section is moved.
BASE = SWARM_REVIEW.read_text(encoding="utf-8")
_SWARM_AT = BASE.index("swarm EditorialBoard")
_WORKFLOWS_AT = BASE.index("workflow draft")
PRELUDE = BASE[:_WORKFLOWS_AT]
WORKFLOWS = BASE[_WORKFLOWS_AT:_SWARM_AT]
SWARM_BLOCK = BASE[_SWARM_AT:]

SWARM_ONLY = PRELUDE + WORKFLOWS + SWARM_BLOCK
SWARM_TWICE = SWARM_ONLY + SWARM_BLOCK.replace("EditorialBoard", "Second")


class EntryPointErrorTests(unittest.TestCase):
    def test_a_swarm_only_program_says_it_has_nothing_to_run(self) -> None:
        message = _entry_point_error(program_from(SWARM_ONLY), "main")
        self.assertIn("EditorialBoard", message)
        self.assertIn("not an executable entry point", message)
        self.assertIn("Runnable workflow(s):", message)
        self.assertIn("review", message)
        self.assertNotIn("Unknown workflow", message)

    def test_the_message_does_not_read_as_a_forgotten_main(self) -> None:
        message = _entry_point_error(program_from(SWARM_ONLY), "main")
        self.assertIn("only under approval through `kosika swarm`", message)

    def test_a_program_without_a_swarm_keeps_the_plain_error(self) -> None:
        program = load_program(HELLO)
        self.assertEqual(
            _entry_point_error(program, "missing"), "Unknown workflow 'missing'"
        )

    def test_every_declared_swarm_is_named(self) -> None:
        message = _entry_point_error(program_from(SWARM_TWICE), "main")
        self.assertIn("EditorialBoard", message)
        self.assertIn("Second", message)

    def test_a_swarm_naming_no_runnable_workflow_says_so(self) -> None:
        # Unreachable through the checker, which requires a swarm to name an
        # existing workflow, so the branch is exercised on the value directly.
        empty = replace(SwarmDecl("Lonely", ("a",), "gone", 1, True), agents=())
        program = replace(load_program(HELLO), swarms=(empty,), workflows=())
        message = _entry_point_error(program, "main")
        self.assertIn("declares no workflow to run", message)


class SwarmMindmapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.map = build_mindmap(load_program(SWARM_REVIEW))
        self.swarm = next(node for node in self.map["nodes"] if node["kind"] == "swarm")

    def test_a_swarm_node_states_that_it_is_not_executable(self) -> None:
        self.assertIs(self.swarm["attributes"]["executable"], False)

    def test_the_swarm_edge_does_not_claim_to_run_the_workflow(self) -> None:
        labels = {
            edge["label"]
            for edge in self.map["edges"]
            if edge["source"] == self.swarm["id"]
            and edge["target"].startswith("workflow:")
        }
        self.assertNotIn("runs", labels)
        self.assertNotIn("runs together", labels)
        self.assertEqual(labels, {"would run together"})

    def test_approval_is_still_reported_as_required(self) -> None:
        self.assertIs(self.swarm["attributes"]["approval_required"], True)
        self.assertEqual(self.swarm["attributes"]["max_rounds"], 3)

    def test_the_map_is_still_deterministic(self) -> None:
        again = build_mindmap(load_program(SWARM_REVIEW))
        self.assertEqual(self.map["digest"], again["digest"])


class DeclarationStillChecksTests(unittest.TestCase):
    """K10a narrows the promise; it must not loosen what is already enforced."""

    def test_require_approval_is_still_mandatory(self) -> None:
        text = SWARM_ONLY.replace("  require approval\n", "")
        with self.assertRaisesRegex(Exception, "require approval"):
            program_from(text)

    def test_the_example_program_still_checks(self) -> None:
        program = load_program(SWARM_REVIEW)
        self.assertEqual(len(program.swarms), 1)
        self.assertTrue(program.swarms[0].approval_required)

    def test_the_example_is_declarative_only(self) -> None:
        program = load_program(SWARM_REVIEW)
        self.assertEqual([swarm.name for swarm in program.swarms], ["EditorialBoard"])
        self.assertIn(
            "not an executable entry point", _entry_point_error(program, "main")
        )


if __name__ == "__main__":
    unittest.main()
