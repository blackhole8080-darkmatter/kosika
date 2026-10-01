from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kosika.editor_features import build_editor_features
from kosika.unified_mindmap import build_unified_mindmap


class EditorFeatureTests(unittest.TestCase):
    def test_valid_document_has_symbols_hovers_and_completions(self) -> None:
        features = build_editor_features(
            "record Result { ok: Bool }\nagent reviewer { can: [] cannot: [] }\n"
        )
        self.assertEqual(features["diagnostics"], [])
        self.assertEqual(
            [symbol["name"] for symbol in features["symbols"]],
            ["Result", "reviewer"],
        )
        self.assertTrue(any(item["name"] == "Result" for item in features["hovers"]))
        self.assertTrue(
            any(item["label"] == "workflow" for item in features["completions"])
        )

    def test_parse_error_has_editor_location(self) -> None:
        features = build_editor_features("record Result {\n")
        self.assertEqual(features["diagnostics"][0]["severity"], "error")
        self.assertEqual(features["diagnostics"][0]["range"]["start"]["line"], 1)

    def test_checker_error_is_reported_without_execution(self) -> None:
        features = build_editor_features("record Result { value: Missing }\n")
        self.assertTrue(
            any(
                "Unknown field type" in item["message"]
                for item in features["diagnostics"]
            )
        )

    def test_imported_diagnostic_keeps_imported_path_and_range(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / "library.kos"
            library.write_text(
                "export [Result, broken]\n"
                "record Result { text: Text name: Text }\n"
                'fn broken() -> Result { return {text: "x"} }\n',
                encoding="utf-8",
            )
            entry = root / "main.kos"
            source = (
                'import "library.kos"\n'
                "workflow main() -> Result {\n"
                "  step call { let result = broken() return result }\n"
                "}\n"
            )
            entry.write_text(source, encoding="utf-8")
            features = build_editor_features(
                source,
                path=entry.as_posix(),
                source_path=entry,
                root=root,
            )

        diagnostic = features["diagnostics"][0]
        self.assertEqual(diagnostic["code"], "check/missing-field")
        self.assertEqual(diagnostic["path"], str(library.resolve()))
        self.assertEqual(diagnostic["range"]["start"]["line"], 2)


class UnifiedMindmapTests(unittest.TestCase):
    def test_unified_graph_keeps_kosika_and_external_ids_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "main.kos"
            source.write_text(
                "record Result { ok: Bool }\n"
                'tool inspect { source mcp "local" operation: "inspect" access: observe output: Result }\n'
                "agent reviewer { can: [inspect] cannot: [] }\n"
                "workflow main() -> Result { step inspect { let result: Result = execute reviewer.inspect() return result } }\n",
                encoding="utf-8",
            )
            project = root / "external"
            project.mkdir()
            (project / "helper.py").write_text(
                "def helper():\n    return 1\n", encoding="utf-8"
            )
            report = build_unified_mindmap(source, project=project)
            ids = {node["id"] for node in report["nodes"]}
            self.assertIn("kosika:root", ids)
            self.assertIn("source:root", ids)
            self.assertTrue(
                any(node["language"] == "python" for node in report["nodes"])
            )
            self.assertTrue(
                all(
                    edge["source"].split(":", 1)[0] == edge["target"].split(":", 1)[0]
                    for edge in report["edges"]
                )
            )


if __name__ == "__main__":
    unittest.main()
