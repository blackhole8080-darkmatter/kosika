from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kosika.agent import AgentLoop
from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.parser import parse
from kosika.skills import (
    SkillCatalog,
    SkillCatalogError,
    SkillDefinition,
    SkillRouter,
)


class SkillRoutingTests(unittest.TestCase):
    def catalog(self) -> SkillCatalog:
        return SkillCatalog(
            (
                SkillDefinition(
                    "rust-review",
                    "1.0",
                    "Review Rust source structure and ownership risks.",
                    ("rust", "systems"),
                    ("borrow", "ownership", "rust"),
                    "Inspect ownership boundaries and report uncertainty.",
                ),
                SkillDefinition(
                    "security-review",
                    "1.0",
                    "Review capability and permission safety.",
                    ("security",),
                    ("security", "capability", "permission"),
                    "Check authority boundaries and approval requirements.",
                ),
                SkillDefinition(
                    "unrelated-writing",
                    "1.0",
                    "Improve prose style.",
                    ("writing",),
                    ("grammar",),
                    "Improve clarity without changing meaning.",
                ),
            )
        )

    def test_hybrid_policy_selects_multiple_relevant_skills(self) -> None:
        selection = SkillRouter(self.catalog()).select(
            "Review this Rust agent for capability security risks."
        )
        self.assertEqual(set(selection.ids), {"rust-review", "security-review"})
        self.assertEqual(len(selection.ids), 2)
        self.assertNotIn("unrelated-writing", selection.ids)
        self.assertIn("advisory instructions", selection.render_context())

    def test_explicit_skill_is_required_and_auto_support_is_bounded(self) -> None:
        selection = SkillRouter(self.catalog()).select(
            "Explain the workflow",
            explicit=("security-review",),
            policy="explicit",
        )
        self.assertEqual(selection.ids, ("security-review",))
        self.assertEqual(
            selection.reasons, (("security-review", "explicitly requested"),)
        )

    def test_catalog_loads_manifest_and_instruction_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "skills"
            skill = root / "rust-review"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("Review Rust carefully.", encoding="utf-8")
            (skill / "skill.json").write_text(
                json.dumps(
                    {
                        "id": "rust-review",
                        "version": "1.0",
                        "summary": "Rust review",
                        "domains": ["rust"],
                        "keywords": ["borrow"],
                    }
                ),
                encoding="utf-8",
            )
            catalog = SkillCatalog.from_directory(root)
            self.assertEqual(len(catalog), 1)
            self.assertEqual(
                catalog.require("rust-review").instructions, "Review Rust carefully."
            )

    def test_catalog_rejects_duplicate_ids(self) -> None:
        skill = SkillDefinition("same", "1", "Same", (), (), "Instructions")
        with self.assertRaisesRegex(SkillCatalogError, "Duplicate"):
            SkillCatalog((skill, skill))

    def test_agent_includes_skills_in_context_and_plan_hash(self) -> None:
        program = parse(Path("examples/test_host.kos").read_text(encoding="utf-8"))
        provider = ScriptedModelProvider([ModelResponse(content="Reviewed.")])
        loop = AgentLoop(
            program,
            provider,
            lambda tool, arguments: {},
            skill_router=SkillRouter(self.catalog()),
            skill_policy="hybrid",
        )
        result = loop.run(
            "monitor", "Review this Rust agent for capability security risks."
        )
        self.assertEqual(
            set(result.selected_skills), {"rust-review", "security-review"}
        )
        self.assertEqual(len(result.selected_skills), 2)
        unskilled_loop = AgentLoop(program, provider, lambda tool, arguments: {})
        self.assertNotEqual(
            loop.plan_hash_for(
                "monitor", "Review this Rust agent for capability security risks."
            ),
            unskilled_loop.plan_hash_for(
                "monitor", "Review this Rust agent for capability security risks."
            ),
        )
        self.assertNotEqual(
            loop.plan_hash_for(
                "monitor", "Review this Rust agent for capability security risks."
            ),
            loop.plan_hash_for("monitor", "Explain the workflow"),
        )
        system_messages = [
            message.content
            for message in provider.requests[0].messages
            if message.role == "system"
        ]
        self.assertTrue(any("rust-review" in message for message in system_messages))
        self.assertEqual(provider.requests[0].tools[0].name, "check_disk")


if __name__ == "__main__":
    unittest.main()
