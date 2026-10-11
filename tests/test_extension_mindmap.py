from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from kosika import cli
from kosika.extensions import load_extension_manifest
from kosika.mindmap import build_mindmap, to_mermaid, to_text
from kosika.parser import parse

ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "examples" / "test_host.kos"
EXTENSION = ROOT / "examples" / "extension_manifest.json"


class ExtensionMindmapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.program = parse(SOURCE.read_text(encoding="utf-8"))
        self.extension = load_extension_manifest(EXTENSION)

    def test_mindmap_contains_extension_contract_and_relationships(self) -> None:
        mindmap = build_mindmap(
            self.program,
            extension_manifests=(self.extension,),
        )
        self.assertEqual(mindmap["summary"]["extension_count"], 1)
        self.assertEqual(
            [item["id"] for item in mindmap["extensions"]],
            ["kosika.example.cognitive-design"],
        )
        kinds = {node["kind"] for node in mindmap["nodes"]}
        self.assertTrue(
            {
                "extension",
                "extension_contribution",
                "extension_permission",
                "extension_dependency",
                "extension_lifecycle",
                "extension_health",
                "extension_rollback",
            }.issubset(kinds)
        )
        relations = {edge["relation"] for edge in mindmap["edges"]}
        self.assertTrue(
            {
                "contribution",
                "permission",
                "requires",
                "lifecycle",
                "health_check",
                "dependency",
                "rollback",
            }.issubset(relations)
        )
        rendered = to_text(mindmap)
        self.assertIn("Extension kosika.example.cognitive-design 0.1.0", rendered)
        self.assertIn("memory:read", rendered)
        self.assertIn("health check extension_ready timeout=1000ms", rendered)
        self.assertIn("rollback: disable", rendered)
        self.assertIn("extension: kosika.example.cognitive-design", to_mermaid(mindmap))

    def test_cli_mindmap_accepts_extension_manifest(self) -> None:
        output = io.StringIO()
        with (
            patch.object(
                sys,
                "argv",
                [
                    "kosika",
                    "mindmap",
                    str(SOURCE),
                    "--extension-manifest",
                    str(EXTENSION),
                    "--format",
                    "json",
                ],
            ),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(cli.main(), 0)
        mindmap = json.loads(output.getvalue())
        self.assertEqual(len(mindmap["extensions"]), 1)
        self.assertEqual(mindmap["summary"]["extension_count"], 1)


if __name__ == "__main__":
    unittest.main()
