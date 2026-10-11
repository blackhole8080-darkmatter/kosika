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
from kosika.project_analysis import (
    ProjectAnalysisError,
    analyze_python_project,
)


class ProjectAnalysisTests(unittest.TestCase):
    def make_project(self, root: Path) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        (root / "pkg").mkdir()
        (root / "tests").mkdir()
        (root / "pkg" / "__init__.py").write_text(
            "from .helper import score\n", encoding="utf-8"
        )
        (root / "pkg" / "helper.py").write_text(
            '"""Shared scoring helper."""\ndef score(value):\n    return value + 1\n',
            encoding="utf-8",
        )
        (root / "pkg" / "service.py").write_text(
            "from .helper import score\nimport pkg.helper\nimport requests\n\ndef run():\n    return score(1)\n",
            encoding="utf-8",
        )
        (root / "tests" / "test_service.py").write_text(
            "from pkg.service import run\n\ndef test_run():\n    assert run() == 2\n",
            encoding="utf-8",
        )
        (root / "README.md").write_text(
            "# Sample project\nA small scoring service.\n", encoding="utf-8"
        )
        (root / "pyproject.toml").write_text(
            "[project]\nname = 'sample'\n", encoding="utf-8"
        )
        (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
        (root / "worker.go").write_text("package worker\n", encoding="utf-8")
        (root / "web" / "app.js").parent.mkdir(parents=True, exist_ok=True)
        (root / "web" / "app.js").write_text(
            'import { helper } from "./helper.js";\n'
            "export function run(value) {\n"
            "  return helper(value);\n"
            "}\n",
            encoding="utf-8",
        )
        (root / "web" / "helper.js").write_text(
            "export function helper(value) {\n  return value + 1;\n}\n",
            encoding="utf-8",
        )
        (root / "service.ts").write_text(
            "export function typed(value: number): number {\n  return value * 2;\n}\n",
            encoding="utf-8",
        )
        return root

    def test_indexes_python_files_context_other_languages_and_local_imports(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(Path(directory))
            report = analyze_python_project(root)

        self.assertEqual(report["schema"], "kosika.project-analysis/v1")
        self.assertEqual(
            report["support_level"], "polyglot_structure_and_python_import_edges"
        )
        self.assertEqual(report["summary"]["file_count"], 7)
        self.assertEqual(report["summary"]["test_file_count"], 1)
        self.assertEqual(
            report["summary"]["language_file_counts"],
            {"go": 1, "javascript": 2, "python": 4, "typescript": 1},
        )
        paths = {item["path"] for item in report["files"]}
        self.assertEqual(
            paths,
            {
                "pkg/__init__.py",
                "pkg/helper.py",
                "pkg/service.py",
                "tests/test_service.py",
                "service.ts",
                "web/app.js",
                "web/helper.js",
            },
        )
        by_path = {item["path"]: item for item in report["files"]}
        self.assertEqual(by_path["pkg/service.py"]["language"], "python")
        self.assertEqual(by_path["web/app.js"]["language"], "javascript")
        self.assertEqual(by_path["service.ts"]["language"], "typescript")
        self.assertEqual(
            by_path["web/app.js"]["support_level"],
            "declarations_imports_and_direct_call_observations",
        )
        js_symbols = {
            item["qualified_name"] for item in by_path["web/app.js"]["symbols"]
        }
        self.assertIn("run", js_symbols)
        self.assertIn(
            "helper", {item["name"] for item in by_path["web/helper.js"]["symbols"]}
        )
        edges = {
            (edge["source"], edge["target"], edge["resolution"])
            for edge in report["local_import_edges"]
        }
        self.assertIn(
            ("pkg/service.py", "pkg/helper.py", "candidate_from_import"), edges
        )
        self.assertIn(("pkg/service.py", "pkg/helper.py", "module_import"), edges)
        self.assertIn(
            ("tests/test_service.py", "pkg/service.py", "candidate_from_import"), edges
        )
        # JavaScript imports are reported per file but never become Python import edges.
        self.assertFalse(any(source.endswith(".js") for source, _, _ in edges))
        self.assertEqual(report["unresolved_import_candidates"], ["requests"])
        self.assertEqual(
            report["other_language_files"], [{"path": "worker.go", "language": "go"}]
        )
        evidence = {item["path"]: item for item in report["context_evidence"]}
        self.assertIn("Sample project", evidence["README.md"]["content"])
        self.assertEqual(evidence["README.md"]["kind"], "documentation")
        self.assertEqual(evidence["pyproject.toml"]["kind"], "project_metadata")
        self.assertEqual(evidence["uv.lock"]["kind"], "dependency_lock")
        self.assertTrue(
            any(
                "not execute" in item or "execution" in item
                for item in report["limitations"]
            )
        )

    def test_output_is_deterministic_and_hashes_project_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(Path(directory))
            first = analyze_python_project(root)
            second = analyze_python_project(root)
        self.assertEqual(first, second)
        self.assertEqual(first["context_evidence"], second["context_evidence"])

    def test_skips_virtualenv_hidden_build_and_symlinked_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(Path(directory))
            (root / ".venv").mkdir()
            (root / ".venv" / "ignored.py").write_text(
                "def ignored(): pass\n", encoding="utf-8"
            )
            (root / "build").mkdir()
            (root / "build" / "ignored.py").write_text(
                "def ignored(): pass\n", encoding="utf-8"
            )
            external = Path(directory) / "external.py"
            external.write_text("def external(): pass\n", encoding="utf-8")
            try:
                (root / "linked.py").symlink_to(external)
            except (OSError, NotImplementedError):
                self.skipTest("Symlinks are unavailable in this environment")
            report = analyze_python_project(root)
        paths = {item["path"] for item in report["files"]}
        self.assertNotIn(".venv/ignored.py", paths)
        self.assertNotIn("build/ignored.py", paths)
        self.assertNotIn("linked.py", paths)

    def test_project_file_count_limit_fails_safely(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("kosika.project_analysis.MAX_PROJECT_FILES", 1):
                (root / "a.py").write_text("pass\n", encoding="utf-8")
                (root / "b.py").write_text("pass\n", encoding="utf-8")
                with self.assertRaisesRegex(
                    ProjectAnalysisError, "more than 1 analyzed files"
                ):
                    analyze_python_project(root)

    def test_report_size_limit_fails_safely(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_project(root)
            with patch("kosika.project_analysis.MAX_PROJECT_REPORT_BYTES", 100):
                with self.assertRaisesRegex(ProjectAnalysisError, "report exceeds"):
                    analyze_python_project(root)

    def test_project_cli_writes_json_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_project(Path(directory) / "project")
            output_path = Path(directory) / "report" / "project.json"
            output = io.StringIO()
            with (
                patch.object(
                    sys,
                    "argv",
                    ["kosika", "project", str(root), "--output", str(output_path)],
                ),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(cli.main(), 0)
            report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(report["schema"], "kosika.project-analysis/v1")
            self.assertIn("Project analysis written", output.getvalue())


if __name__ == "__main__":
    unittest.main()
