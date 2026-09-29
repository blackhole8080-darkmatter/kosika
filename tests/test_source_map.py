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
from kosika.source_map import (
    MAX_FILES,
    SourceMapError,
    build_source_map,
    to_mermaid,
    to_text,
)
from kosika.source_schema import build_source_analysis


class SourceMapTests(unittest.TestCase):
    def make_tree(self, root: Path) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        (root / "service.ts").write_text(
            'import { readFile } from "node:fs/promises";\n'
            'import { helper } from "./helper.js";\n'
            "/** Loads and saves. */\n"
            "export async function load(path) {\n"
            "  return readFile(path);\n"
            "}\n"
            "export class Store {\n"
            "  put(k, v) { return v; }\n"
            "}\n",
            encoding="utf-8",
        )
        (root / "helper.js").write_text(
            "export function helper(a) { return a * 2; }\n", encoding="utf-8"
        )
        (root / "engine.py").write_text(
            '"""Engine."""\n'
            "def run():\n"
            "    return helper()\n"
            "\n"
            "def helper():\n"
            "    return 1\n",
            encoding="utf-8",
        )
        return root

    def test_maps_multiple_languages_into_one_graph(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_tree(Path(directory))
            report = build_source_map(root)
            self.assertEqual(report["schema"], "kosika.source-map/v1")
            counts = report["summary"]["language_file_counts"]
            self.assertEqual(counts["typescript"], 1)
            self.assertEqual(counts["javascript"], 1)
            self.assertEqual(counts["python"], 1)
            kinds = report["summary"]["symbol_kind_counts"]
            # load, helper (js), helper (py), and run are all functions; the two
            # same-named helpers are distinct declarations in distinct modules.
            self.assertEqual(kinds["function"], 4)
            self.assertEqual(kinds["class"], 1)
            self.assertEqual(kinds["method"], 1)

    def test_links_callers_to_uniquely_named_declarations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.js").write_text(
                "function target() { return 1; }\n"
                "function caller() { return target(); }\n",
                encoding="utf-8",
            )
            report = build_source_map(root)
            calls = {
                (edge["source"], edge["target"])
                for edge in report["edges"]
                if edge["relation"] == "calls"
            }
            self.assertEqual(len(calls), 1)
            source, target = next(iter(calls))
            self.assertIn("caller", source)
            self.assertIn("target", target)
            self.assertEqual(report["summary"]["resolved_call_edges"], 1)

    def test_refuses_to_guess_when_a_callee_name_is_ambiguous(self) -> None:
        # The tree intentionally declares "helper" in two languages, so a call
        # to it cannot be resolved to one definition and must not be guessed.
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_tree(Path(directory))
            report = build_source_map(root)
            self.assertIn("helper", report["ambiguous_callees"])
            targets = {
                edge["target"]
                for edge in report["edges"]
                if edge["relation"] == "calls"
            }
            self.assertFalse(any("helper" in target for target in targets))

    def test_links_local_imports_to_discovered_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_tree(Path(directory))
            report = build_source_map(root)
            imports = [
                edge for edge in report["edges"] if edge["relation"] == "imports"
            ]
            self.assertTrue(any("helper.js" in edge["label"] for edge in imports))

    def test_surfaces_observed_external_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_tree(Path(directory))
            report = build_source_map(root)
            effects = {
                effect["kind"]
                for node in report["nodes"]
                for effect in node.get("effects", ())
            }
            self.assertIn("filesystem", effects)
            self.assertGreater(report["summary"]["observed_effect_count"], 0)

    def test_records_unreadable_files_instead_of_failing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "broken.js").write_text(
                'const a = "unterminated\n', encoding="utf-8"
            )
            (root / "ok.js").write_text("function good() {}\n", encoding="utf-8")
            report = build_source_map(root)
            self.assertEqual(report["summary"]["file_count"], 1)
            self.assertEqual(report["summary"]["unreadable_files"], 1)
            self.assertEqual(report["unreadable_files"][0]["path"], "broken.js")

    def test_reports_ambiguous_and_unresolved_callees(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # "dupe" is declared twice, so a call to it cannot be resolved to a
            # single definition and is reported rather than guessed.
            (root / "a.js").write_text(
                "function dupe() {}\nfunction u(){ return dupe() + other(); }\n",
                encoding="utf-8",
            )
            (root / "b.js").write_text("function dupe() {}\n", encoding="utf-8")
            report = build_source_map(root)
            self.assertIn("other", report["unresolved_callees"])
            self.assertIn("dupe", report["ambiguous_callees"])
            targets = {
                edge["target"]
                for edge in report["edges"]
                if edge["relation"] == "calls"
            }
            self.assertFalse(any("dupe" in target for target in targets))

    def test_skips_unsupported_extensions_and_hidden_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.js").write_text("function a() {}\n", encoding="utf-8")
            (root / "readme.md").write_text("# hi\n", encoding="utf-8")
            (root / "node_modules").mkdir()
            (root / "node_modules" / "x.js").write_text(
                "function x() {}\n", encoding="utf-8"
            )
            (root / ".git").mkdir()
            (root / ".git" / "y.js").write_text("function y() {}\n", encoding="utf-8")
            report = build_source_map(root)
            self.assertEqual(report["summary"]["file_count"], 1)

    def test_output_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_tree(Path(directory))
            first = build_source_map(root)
            second = build_source_map(root)
            self.assertEqual(
                json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True)
            )
            self.assertEqual(to_mermaid(first), to_mermaid(second))
            self.assertEqual(to_text(first), to_text(second))

    def test_missing_root_fails_safely(self) -> None:
        with self.assertRaises(SourceMapError):
            build_source_map("does-not-exist-directory-xyz")

    def test_file_root_fails_safely(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.js"
            path.write_text("function a() {}\n", encoding="utf-8")
            with self.assertRaises(SourceMapError):
                build_source_map(path)

    def test_empty_tree_reports_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = build_source_map(directory)
            self.assertEqual(report["summary"]["file_count"], 0)
            self.assertEqual(report["summary"]["node_count"], 0)

    def test_allows_repeated_declaration_names_in_one_file(self) -> None:
        # JavaScript permits several declarations of the same name in a module.
        # The map must keep them distinct rather than reject the file.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.js").write_text(
                "function node() { return 1; }\n"
                "function node() { return 2; }\n"
                "const host = 1;\n"
                "const host = 2;\n",
                encoding="utf-8",
            )
            report = build_source_map(root)
            node_ids = {
                node["id"]
                for node in report["nodes"]
                if node.get("qualified_name") == "node"
            }
            self.assertEqual(len(node_ids), 2)
            self.assertEqual(report["summary"]["unreadable_files"], 0)

    def test_validator_permits_repeated_qualified_names(self) -> None:
        row = {
            "kind": "function",
            "name": "node",
            "qualified_name": "node",
            "start_line": 1,
            "end_line": 1,
            "docstring": None,
            "calls": [],
        }
        second = dict(row, start_line=5, end_line=5)
        document = build_source_analysis(
            language="javascript",
            path="a.js",
            source="function node() {}\nfunction node() {}\n",
            support_level="test",
            imports=[],
            module_calls=[],
            symbols=[row, second],
        )
        self.assertEqual(len(document["symbols"]), 2)

    def test_renderings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_tree(Path(directory))
            self.assertIn("Source map for", to_text(build_source_map(root)))
            self.assertIn("flowchart", to_mermaid(build_source_map(root)))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(3):
                (root / f"f{index}.js").write_text(
                    f"function f{index}() {{}}\n", encoding="utf-8"
                )
            report = build_source_map(root)
            self.assertEqual(report["limits"]["max_files"], MAX_FILES)
            self.assertEqual(report["summary"]["file_count"], 3)


class SourceMapRenderingTests(unittest.TestCase):
    def render(self) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.ts").write_text(
                "/** Does a thing. */\nexport function work(x) { return x; }\n",
                encoding="utf-8",
            )
            (root / "b.py").write_text(
                'def run():\n    """Runs it."""\n    return 1\n', encoding="utf-8"
            )
            return build_source_map(root)

    def test_text_output_names_files_declarations_and_limits(self) -> None:
        text = to_text(self.render())
        self.assertIn("a.ts", text)
        self.assertIn("work", text)
        self.assertIn("Does a thing.", text)
        self.assertIn("b.py", text)
        self.assertIn("Structure only", text)

    def test_text_output_collapses_multiline_documentation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.py").write_text(
                'def run():\n    """First line.\n\n    Second paragraph.\n    """\n    return 1\n',
                encoding="utf-8",
            )
            text = to_text(build_source_map(root))
            declaration_lines = [
                line for line in text.splitlines() if "function" in line
            ]
            self.assertEqual(len(declaration_lines), 1)

    def test_mermaid_output_is_stable_and_labels_nodes(self) -> None:
        report = self.render()
        mermaid = to_mermaid(report)
        self.assertTrue(mermaid.startswith("flowchart LR"))
        self.assertIn('["work<br/>function (typescript)"]', mermaid)
        identifiers = {
            line.split("[")[0].strip()
            for line in mermaid.splitlines()[1:]
            if "-->" not in line
        }
        self.assertTrue(all(item.startswith("n_") for item in identifiers))


class SourceMapCliTests(unittest.TestCase):
    def run_cli(self, argv: list[str]) -> tuple[int, str, str]:
        out = io.StringIO()
        err = io.StringIO()
        with patch.object(sys, "argv", ["kosika", *argv]):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main()
        return code, out.getvalue(), err.getvalue()

    def test_cli_renders_text_map(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.js").write_text("function a() {}\n", encoding="utf-8")
            code, out, _ = self.run_cli(["sourcemap", str(root), "--format", "text"])
            self.assertEqual(code, 0)
            self.assertIn("Source map for", out)
            self.assertIn("a.js", out)

    def test_cli_renders_mermaid_map(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.js").write_text("function a() {}\n", encoding="utf-8")
            code, out, _ = self.run_cli(["sourcemap", str(root), "--format", "mermaid"])
            self.assertEqual(code, 0)
            self.assertIn("flowchart LR", out)

    def test_cli_writes_json_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.js").write_text("function a() {}\n", encoding="utf-8")
            target = Path(directory) / "out" / "map.json"
            code, _, _ = self.run_cli(
                ["sourcemap", str(root), "--format", "json", "--output", str(target)]
            )
            self.assertEqual(code, 0)
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8"))["schema"],
                "kosika.source-map/v1",
            )

    def test_cli_reports_failure_safely(self) -> None:
        code, _, err = self.run_cli(["sourcemap", "missing-directory-xyz"])
        self.assertEqual(code, 2)
        self.assertIn("Source map failed safely", err)

    def test_analyze_dispatches_by_language(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            samples = {
                "a.py": ("Python", "def f():\n    return 1\n"),
                "a.js": ("Javascript", "function f() { return 1; }\n"),
                "a.ts": ("Typescript", "function f(): number { return 1; }\n"),
            }
            for name, (expected, body) in samples.items():
                path = root / name
                path.write_text(body, encoding="utf-8")
                code, out, err = self.run_cli(
                    ["analyze", str(path), "--format", "text"]
                )
                self.assertEqual(code, 0, err)
                self.assertIn(f"{expected} structure", out)

    def test_analyze_rejects_unsupported_language(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text("[project]\n", encoding="utf-8")
            code, _, err = self.run_cli(["analyze", str(path)])
            self.assertEqual(code, 2)
            self.assertIn("No structural adapter", err)

    def test_analyze_reports_uncertainties(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.js"
            path.write_text("const p = /unclosed\n", encoding="utf-8")
            code, out, _ = self.run_cli(["analyze", str(path), "--format", "text"])
            self.assertEqual(code, 0)
            self.assertIn("Uncertainties reported", out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
