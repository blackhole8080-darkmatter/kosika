from __future__ import annotations

import json
import unittest
from pathlib import Path

from examples.agent_factory_mcp_server import source_for
from kosika.modules import load_program
from kosika.parser import parse

ROOT = Path(__file__).resolve().parents[1]


class AgentFactoryTests(unittest.TestCase):
    def test_factory_program_is_checked_with_an_approval_gate(self) -> None:
        program = load_program(ROOT / "examples" / "agent_factory.kos")

        self.assertEqual(program.workflows[0].name, "build_agent")
        self.assertTrue(program.workflows[0].approval_required)
        self.assertEqual(program.models[0].name, "architect")
        self.assertEqual(program.agents[0].name, "source_writer")

    def test_source_writer_renders_parseable_restricted_agent_source(self) -> None:
        arguments = {
            "name": "kosika_reviewer",
            "model": "reviewer_model",
            "can": ["read_diff", "run_tests"],
            "cannot": ["write_files", "arbitrary_shell", "grant_capabilities"],
            "max_steps": 4,
        }

        generated = source_for(arguments)
        parsed = parse(generated)

        self.assertEqual(parsed.agents[0].name, "kosika_reviewer")
        self.assertEqual(parsed.agents[0].cannot, tuple(arguments["cannot"]))
        self.assertNotIn("open(", generated)
        self.assertNotIn("write_text", generated)

    def test_pinned_manifest_matches_factory_tool_contract(self) -> None:
        manifest = json.loads(
            (ROOT / "examples" / "agent_factory_manifest.json").read_text(
                encoding="utf-8"
            )
        )

        tool = manifest["tools"]["write_source"]
        self.assertEqual(manifest["server"]["source"], "agent_factory")
        self.assertEqual(
            tool["inputSchema"]["required"],
            ["name", "model", "can", "cannot", "max_steps"],
        )
        self.assertEqual(
            tool["outputSchema"]["required"], ["source", "status", "action"]
        )


if __name__ == "__main__":
    unittest.main()
