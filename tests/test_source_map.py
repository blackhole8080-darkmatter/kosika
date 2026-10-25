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
    MAX_VIEW_CHARS,
    SourceMapError,
    build_source_map,
    readable_source,
    scout_sources,
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

    def test_a_package_relative_import_does_not_crash_the_map(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "pkg"
            root.mkdir()
            (root / "__init__.py").write_text(
                "from . import helper\n", encoding="utf-8"
            )
            (root / "helper.py").write_text(
                "def twice(n):\n    return n * 2\n", encoding="utf-8"
            )
            report = build_source_map(root)
            self.assertEqual(report["summary"]["file_count"], 2)
            self.assertEqual(report["summary"]["symbol_kind_counts"]["function"], 1)

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


class SourceScoutTests(unittest.TestCase):
    def test_names_the_languages_and_declarations_in_a_folder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "notes.py").write_text(
                "def summarise(text):\n    return text\n", encoding="utf-8"
            )
            (root / "view.ts").write_text(
                "export function draw() { return 1; }\n", encoding="utf-8"
            )
            for hidden in (".venv", "node_modules"):
                (root / hidden).mkdir()
                (root / hidden / "ignored.py").write_text(
                    "def never():\n    return 0\n", encoding="utf-8"
                )
            (root / "README.md").write_text("# Notes\n", encoding="utf-8")

            report = scout_sources(root)
            self.assertEqual(report["file_count"], 2)
            self.assertEqual(
                report["language_file_counts"], {"python": 1, "typescript": 1}
            )
            self.assertEqual(
                [(item["path"], item["language"]) for item in report["files"]],
                [("notes.py", "python"), ("view.ts", "typescript")],
            )
            self.assertEqual(report["files"][0]["declarations"], ["summarise"])
            self.assertEqual(report["files"][0]["declaration_count"], 1)
            self.assertFalse(report["truncated"])

    def test_stops_at_its_budget_instead_of_refusing_a_big_folder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for number in range(5):
                (root / f"part{number}.py").write_text(
                    f"def part{number}():\n    return {number}\n", encoding="utf-8"
                )
            with patch("kosika.source_map.SCOUT_FILE_LIMIT", 3):
                report = scout_sources(root)
            self.assertEqual(report["file_count"], 3)
            self.assertTrue(report["truncated"])
            # Still a complete answer for the files it did read.
            self.assertEqual(len(report["files"]), 3)
            self.assertTrue(all(item["declarations"] for item in report["files"]))

    def test_a_file_it_cannot_read_is_counted_not_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "good.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
            # Not UTF-8, so there is nothing to read as structure.
            (root / "broken.js").write_bytes(b"const p = \xff\xfe;\n")
            report = scout_sources(root)
            self.assertEqual(report["file_count"], 1)
            self.assertEqual(report["unreadable_files"], 1)

    def test_a_missing_folder_fails_safely(self) -> None:
        with self.assertRaises(SourceMapError):
            scout_sources("missing-directory-xyz")

    def test_an_import_and_a_call_become_arrows_between_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "store.py").write_text(
                "def load():\n    return 1\n", encoding="utf-8"
            )
            (root / "main.py").write_text(
                "from store import load\n\n"
                "def start():\n    return load()\n",
                encoding="utf-8",
            )
            graph = scout_sources(root)["graph"]
            self.assertEqual(
                [(link["from"], link["to"], link["kind"]) for link in graph["links"]],
                [("main.py", "store.py", "import"), ("main.py", "store.py", "call")],
            )
            levels = {node["path"]: node["level"] for node in graph["nodes"]}
            # The file that reaches the other starts the map.
            self.assertEqual(levels, {"main.py": 0, "store.py": 1})
            self.assertEqual(graph["unresolved_imports"], 0)

    def test_one_arrow_per_pair_of_files_however_many_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "store.py").write_text(
                "class Store:\n    pass\n\ndef load():\n    return 1\n",
                encoding="utf-8",
            )
            (root / "main.py").write_text(
                "from store import Store, load\n", encoding="utf-8"
            )
            graph = scout_sources(root)["graph"]
            self.assertEqual(
                [(link["from"], link["to"]) for link in graph["links"]],
                [("main.py", "store.py")],
            )

    def test_a_name_declared_twice_is_not_linked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.py").write_text("def build():\n    return 1\n", encoding="utf-8")
            (root / "b.py").write_text("def build():\n    return 2\n", encoding="utf-8")
            (root / "use.py").write_text(
                "def go():\n    return build()\n", encoding="utf-8"
            )
            graph = scout_sources(root)["graph"]
            self.assertEqual(graph["links"], [])
            self.assertEqual(graph["ambiguous_calls"], 1)

    def test_a_cycle_stays_inside_its_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.py").write_text(
                "import b\n\ndef one():\n    return b.two()\n", encoding="utf-8"
            )
            (root / "b.py").write_text(
                "import a\n\ndef two():\n    return a.one()\n", encoding="utf-8"
            )
            graph = scout_sources(root)["graph"]
            self.assertEqual(len(graph["nodes"]), 2)
            self.assertTrue(all(node["level"] < 4 for node in graph["nodes"]))

    def test_a_folder_too_full_to_draw_is_counted_not_crowded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for number in range(10):
                (root / f"part{number}.py").write_text(
                    f"def part{number}():\n    return {number}\n", encoding="utf-8"
                )
            graph = scout_sources(root)["graph"]
            # Six boxes to a column, so the rest are counted and left out.
            self.assertEqual(graph["drawn_files"], 6)
            self.assertEqual(graph["hidden_files"], 4)
            self.assertEqual(graph["rows"], 6)


class ReadableSourceTests(unittest.TestCase):
    def test_a_file_inside_the_folder_is_given_as_it_is(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "store.py").write_text(
                "def load():\n    return 1\n", encoding="utf-8", newline="\n"
            )
            document = readable_source(root, "store.py")
            self.assertEqual(
                (document["path"], document["language"]), ("store.py", "python")
            )
            self.assertEqual(document["text"], "def load():\n    return 1\n")
            self.assertEqual(
                (document["line_count"], document["truncated"]), (2, False)
            )

    def test_a_path_outside_the_folder_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "inner"
            root.mkdir()
            (Path(directory) / "secret.py").write_text("x = 1\n", encoding="utf-8")
            for given in (
                "../secret.py",
                str(Path(directory) / "secret.py"),
                "",
            ):
                with self.assertRaises(SourceMapError):
                    readable_source(root, given)

    def test_a_folder_a_map_skips_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "node_modules").mkdir()
            (root / "node_modules" / "hidden.py").write_text(
                "x = 1\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(SourceMapError, "skips"):
                readable_source(root, "node_modules/hidden.py")

    def test_what_is_not_source_at_all_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "notes.txt").write_text("hi\n", encoding="utf-8")
            (root / "broken.py").write_bytes(b"x = \xff\xfe\n")
            with self.assertRaisesRegex(SourceMapError, "cannot read"):
                readable_source(root, "notes.txt")
            with self.assertRaisesRegex(SourceMapError, "not UTF-8"):
                readable_source(root, "broken.py")

    def test_a_long_file_says_it_was_cut(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "long.py").write_text("x = 1\n" * 30_000, encoding="utf-8")
            document = readable_source(root, "long.py")
            self.assertEqual(len(document["text"]), MAX_VIEW_CHARS)
            self.assertTrue(document["truncated"])
            self.assertEqual(document["line_count"], 30_000)


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
