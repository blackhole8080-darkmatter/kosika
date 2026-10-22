from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from kosika import cli
from kosika.mindmap import build_mindmap, to_json, to_mermaid
from kosika.modules import load_program
from kosika.parser import parse

ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "examples" / "test_host.kos"
COMPOSED = ROOT / "examples" / "composed" / "main.kos"


class MindmapAssistantTests(unittest.TestCase):
    def test_mindmap_is_deterministic_and_typed(self) -> None:
        program = parse(SOURCE.read_text(encoding="utf-8"))
        first = build_mindmap(program)
        second = build_mindmap(program)
        self.assertEqual(first, second)
        self.assertEqual(to_json(first), to_json(second))
        mermaid = to_mermaid(first)
        self.assertTrue(mermaid.startswith("flowchart TD\n"))
        self.assertIn("record: DiskStatus", mermaid)
        self.assertIn("workflow: main", mermaid)
        self.assertTrue(any(node["kind"] == "field" for node in first["nodes"]))
        self.assertTrue(any(edge["relation"] == "tool_call" for edge in first["edges"]))

    def test_a_choice_is_a_type_with_its_alternatives(self) -> None:
        program = load_program(ROOT / "conformance" / "cases" / "choice_outputs.kos")
        mindmap = build_mindmap(program)
        nodes = {(node["kind"], node["label"]): node for node in mindmap["nodes"]}
        self.assertEqual(
            nodes[("choice", "Verdict")]["attributes"]["alternatives"],
            ["accepted", "refused"],
        )
        self.assertEqual(
            nodes[("constructor", "accepted")]["attributes"]["choice"], "Verdict"
        )
        by_id = {node["id"]: node for node in mindmap["nodes"]}
        edges = {
            (
                by_id[edge["source"]]["label"],
                edge["relation"],
                by_id[edge["target"]]["label"],
            )
            for edge in mindmap["edges"]
        }
        # A model or tool answers with the choice itself, not a record.
        self.assertIn(("judge", "output", "Verdict"), edges)
        self.assertIn(("notes", "output", "Lookup"), edges)
        self.assertIn(("Verdict", "alternative", "accepted"), edges)

    def test_imported_modules_and_declarations_keep_boundaries(self) -> None:
        program = load_program(COMPOSED)
        mindmap = build_mindmap(program)
        self.assertEqual(
            [module["path"] for module in mindmap["modules"]],
            ["main.kos", "research.kos", "types.kos"],
        )
        self.assertEqual(
            set(mindmap["modules"][0]["imports"]),
            {"research.kos", "types.kos"},
        )
        owners = {
            node["label"]: node["module"]
            for node in mindmap["nodes"]
            if node["kind"] in {"record", "model", "workflow"}
        }
        self.assertEqual(owners["Question"], "types.kos")
        self.assertEqual(owners["reader"], "research.kos")
        self.assertEqual(owners["main"], "main.kos")
        self.assertTrue(any(edge["relation"] == "imports" for edge in mindmap["edges"]))

    def test_cli_mindmap_renders_mermaid(self) -> None:
        output = io.StringIO()
        with (
            patch.object(
                sys,
                "argv",
                ["kosika", "mindmap", str(SOURCE), "--format", "mermaid"],
            ),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(cli.main(), 0)
        self.assertIn("flowchart TD", output.getvalue())


if __name__ == "__main__":
    unittest.main()
