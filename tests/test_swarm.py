"""Swarm declaration: cooperative agents that always require human approval."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kosika import cli
from kosika.ast import SwarmDecl
from kosika.checker import CheckError, check
from kosika.design import build_design_from_program, render_design
from kosika.explain import (
    build_explanation,
    to_explanation_outsider,
    to_explanation_text,
)
from kosika.mindmap import build_mindmap
from kosika.modules import load_program
from kosika.parser import ParseError, parse

ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "examples" / "swarm_review.kos"

HEADER = (
    "record Q { text: Text }\n"
    "record A { text: Text }\n"
    "model m { input: A output: A }\n"
    "agent helper { model: m }\n"
    "tool poke {\n"
    '  source mcp "local_mock"\n'
    '  operation: "go"\n'
    "  access: observe\n"
    "  output: Q\n"
    "}\n"
    "agent worker { can: [poke] }\n"
    "workflow inner() -> Q {\n"
    "  step s {\n"
    "    let r: Q = execute worker.poke()\n"
    "    return r\n"
    "  }\n"
    "}\n"
)

VALID_SWARM = (
    "swarm Crew {\n"
    "  agents: [worker]\n"
    "  workflow: inner\n"
    "  max_rounds: 2\n"
    "  require approval\n"
    "}\n"
)


def swarm_program(body: str) -> str:
    return HEADER + body


class SwarmParsingTests(unittest.TestCase):
    def test_parses_a_swarm_declaration(self) -> None:
        program = parse(swarm_program(VALID_SWARM))
        self.assertEqual(
            program.swarms,
            [SwarmDecl("Crew", ("worker",), "inner", 2, True)],
        )

    def test_swarm_without_approval_is_refused(self) -> None:
        with self.assertRaisesRegex(ParseError, "must declare `require approval`"):
            parse(
                swarm_program(
                    "swarm Crew {\n"
                    "  agents: [worker]\n"
                    "  workflow: inner\n"
                    "}\n"
                )
            )

    def test_swarm_requires_agents_and_workflow(self) -> None:
        with self.assertRaisesRegex(ParseError, "requires an agents list"):
            parse(
                swarm_program(
                    "swarm Crew {\n  workflow: inner\n  require approval\n}\n"
                )
            )
        with self.assertRaisesRegex(ParseError, "requires a workflow"):
            parse(
                swarm_program(
                    "swarm Crew {\n  agents: [worker]\n  require approval\n}\n"
                )
            )
        with self.assertRaisesRegex(ParseError, "at least one agent"):
            parse(
                swarm_program(
                    "swarm Crew {\n  agents: []\n  workflow: inner\n"
                    "  require approval\n}\n"
                )
            )

    def test_duplicate_and_unknown_fields_are_refused(self) -> None:
        with self.assertRaisesRegex(ParseError, "Duplicate swarm field"):
            parse(
                swarm_program(
                    "swarm Crew {\n  agents: [worker]\n  agents: [worker]\n"
                    "  workflow: inner\n  require approval\n}\n"
                )
            )
        with self.assertRaisesRegex(ParseError, "Unknown swarm field"):
            parse(
                swarm_program(
                    "swarm Crew {\n  agents: [worker]\n  workflow: inner\n"
                    "  budget: 2\n  require approval\n}\n"
                )
            )
        with self.assertRaisesRegex(ParseError, "Duplicate swarm require approval"):
            parse(
                swarm_program(
                    "swarm Crew {\n  agents: [worker]\n  workflow: inner\n"
                    "  require approval\n  require approval\n}\n"
                )
            )

    def test_max_rounds_must_be_a_number(self) -> None:
        with self.assertRaisesRegex(ParseError, "positive integer"):
            parse(
                swarm_program(
                    "swarm Crew {\n  agents: [worker]\n  workflow: inner\n"
                    "  max_rounds: many\n  require approval\n}\n"
                )
            )

class SwarmCheckingTests(unittest.TestCase):
    def check_source(self, body: str) -> None:
        check(parse(swarm_program(body)))

    def test_valid_swarm_passes(self) -> None:
        self.check_source(VALID_SWARM)

    def test_unknown_agent_is_rejected(self) -> None:
        with self.assertRaisesRegex(CheckError, "names unknown agent"):
            self.check_source(
                "swarm Crew {\n  agents: [ghost]\n  workflow: inner\n"
                "  require approval\n}\n"
            )

    def test_unknown_workflow_is_rejected(self) -> None:
        with self.assertRaisesRegex(CheckError, "names unknown workflow"):
            self.check_source(
                "swarm Crew {\n  agents: [worker]\n  workflow: missing\n"
                "  require approval\n}\n"
            )

    def test_duplicate_agent_and_out_of_range_rounds_are_rejected(self) -> None:
        with self.assertRaisesRegex(CheckError, "lists a duplicate agent"):
            self.check_source(
                "swarm Crew {\n  agents: [worker, worker]\n  workflow: inner\n"
                "  require approval\n}\n"
            )
        with self.assertRaisesRegex(CheckError, "max_rounds must be an integer"):
            self.check_source(
                "swarm Crew {\n  agents: [worker]\n  workflow: inner\n"
                "  max_rounds: 0\n  require approval\n}\n"
            )

    def test_agent_the_workflow_never_uses_is_rejected(self) -> None:
        with self.assertRaisesRegex(CheckError, "never uses"):
            self.check_source(
                "swarm Crew {\n  agents: [worker, helper]\n  workflow: inner\n"
                "  require approval\n}\n"
            )

    def test_nested_workflow_participants_are_found(self) -> None:
        self.check_source(
            "workflow outer() -> Q {\n"
            "  step call {\n"
            "    let r: Q = call inner()\n"
            "    return r\n"
            "  }\n"
            "}\n"
            "swarm Crew {\n  agents: [worker]\n  workflow: outer\n"
            "  require approval\n}\n"
        )

    def test_a_hand_built_swarm_cannot_disable_approval(self) -> None:
        program = parse(swarm_program(VALID_SWARM))
        program.swarms[0] = SwarmDecl("Crew", ("worker",), "inner", 2, False)
        with self.assertRaisesRegex(CheckError, "must require human approval"):
            check(program)

    def test_duplicate_swarm_declaration_is_rejected(self) -> None:
        with self.assertRaisesRegex(CheckError, "Duplicate swarm declaration"):
            self.check_source(VALID_SWARM + VALID_SWARM)
class SwarmVisualisationTests(unittest.TestCase):
    def test_mindmap_exposes_the_swarm_and_its_approval(self) -> None:
        mindmap = build_mindmap(load_program(EXAMPLE))
        swarms = [node for node in mindmap["nodes"] if node["kind"] == "swarm"]
        self.assertEqual([node["label"] for node in swarms], ["EditorialBoard"])
        self.assertEqual(swarms[0]["attributes"]["agents"], ["researcher", "editor"])
        self.assertTrue(swarms[0]["attributes"]["approval_required"])
        self.assertEqual(mindmap["summary"]["swarm_count"], 1)
        relations = {
            (edge["source"], edge["relation"], edge["target"])
            for edge in mindmap["edges"]
        }
        self.assertIn(("swarm:EditorialBoard", "coordinates", "agent:researcher"), relations)
        self.assertIn(("swarm:EditorialBoard", "coordinates", "agent:editor"), relations)
        self.assertIn(("swarm:EditorialBoard", "runs", "workflow:review"), relations)

    def test_mindmap_is_deterministic_with_a_swarm(self) -> None:
        program = load_program(EXAMPLE)
        self.assertEqual(build_mindmap(program), build_mindmap(program))

    def test_outsider_view_states_the_group_and_its_human_gate(self) -> None:
        outsider = to_explanation_outsider(build_explanation(load_program(EXAMPLE)))
        self.assertIn("How the agents work together:", outsider)
        self.assertIn("EditorialBoard brings together editor, researcher", outsider)
        self.assertIn("A person must approve this group before it starts.", outsider)
        self.assertNotIn("Swarm EditorialBoard", outsider)

    def test_developer_view_reports_swarm_fields(self) -> None:
        text = to_explanation_text(build_explanation(load_program(EXAMPLE)))
        self.assertIn("Swarm EditorialBoard runs review with editor, researcher", text)
        self.assertIn("max_rounds=3", text)
        self.assertIn("approval required: True", text)

    def test_design_contains_a_swarm_node_and_an_approval_edge(self) -> None:
        manifest = build_design_from_program(
            load_program(EXAMPLE), "swarm.design", "Editorial Board"
        )
        kinds = {node["id"]: node["kind"] for node in manifest.nodes}
        self.assertEqual(kinds["swarm.EditorialBoard"], "swarm")
        self.assertEqual(kinds["approval.swarm.EditorialBoard"], "approval")
        edges = {(edge["source"], edge["kind"], edge["target"]) for edge in manifest.edges}
        self.assertIn(
            ("swarm.EditorialBoard", "requires_approval", "approval.swarm.EditorialBoard"),
            edges,
        )
        self.assertIn(("swarm.EditorialBoard", "uses", "agent.researcher"), edges)
        self.assertIn(("swarm.EditorialBoard", "invokes", "workflow.review"), edges)

    def test_design_renders_every_format_with_a_swarm(self) -> None:
        manifest = build_design_from_program(
            load_program(EXAMPLE), "swarm.design", "Editorial Board"
        )
        for output_format in ("text", "json", "mermaid", "svg"):
            self.assertTrue(render_design(manifest, output_format, "workflow", "lab"))
class SwarmCliTests(unittest.TestCase):
    def run_cli(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with (
            patch.object(sys, "argv", argv),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = cli.main()
        return code, out.getvalue(), err.getvalue()

    def test_cli_check_accepts_the_example(self) -> None:
        code, out, _ = self.run_cli(["kosika", "check", str(EXAMPLE)])
        self.assertEqual(code, 0)
        self.assertIn("OK:", out)

    def test_cli_check_reports_an_unapproved_swarm_safely(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "crew.kos"
            source.write_text(
                swarm_program(
                    "swarm Crew {\n  agents: [worker]\n  workflow: inner\n}\n"
                ),
                encoding="utf-8",
            )
            code, _, err = self.run_cli(["kosika", "check", str(source)])
        self.assertEqual(code, 2)
        self.assertIn("require approval", err)

    def test_cli_check_reports_a_misleading_swarm_safely(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "crew.kos"
            source.write_text(
                swarm_program(
                    "swarm Crew {\n  agents: [worker, helper]\n  workflow: inner\n"
                    "  require approval\n}\n"
                ),
                encoding="utf-8",
            )
            code, _, err = self.run_cli(["kosika", "check", str(source)])
        self.assertEqual(code, 2)
        self.assertIn("never uses", err)

    def test_cli_mindmap_and_explain_include_the_swarm(self) -> None:
        code, out, _ = self.run_cli(
            ["kosika", "mindmap", str(EXAMPLE), "--format", "json"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["summary"]["swarm_count"], 1)

        code, out, _ = self.run_cli(
            ["kosika", "explain", str(EXAMPLE), "--format", "outsider"]
        )
        self.assertEqual(code, 0)
        self.assertIn("A person must approve this group", out)


if __name__ == "__main__":
    unittest.main()