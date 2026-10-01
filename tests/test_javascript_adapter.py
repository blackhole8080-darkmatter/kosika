from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kosika.javascript_adapter import (
    MAX_SOURCE_BYTES,
    MAX_SYMBOLS,
    JavaScriptSourceError,
    scan_javascript_file,
    scan_javascript_source,
)
from kosika.language_adapters import adapter_for_suffix
from kosika.source_schema import (
    SOURCE_ANALYSIS_SCHEMA,
    SourceAnalysisError,
    build_source_analysis,
    validate_source_analysis,
)


def scan(source: str, language: str = "javascript") -> dict:
    return scan_javascript_source(source, path="sample.ts", language=language)


def names(document: dict) -> list[str]:
    return [symbol["qualified_name"] for symbol in document["symbols"]]


def symbol(document: dict, qualified_name: str) -> dict:
    for item in document["symbols"]:
        if item["qualified_name"] == qualified_name:
            return item
    raise AssertionError(f"missing symbol {qualified_name!r} in {names(document)}")


class JavaScriptDeclarationTests(unittest.TestCase):
    def test_reports_functions_classes_methods_and_async(self) -> None:
        document = scan(
            """
            function plain(a) { return a; }
            async function loaded(a) { return a; }
            class Store {
              put(key, value) { return value; }
              get size() { return 0; }
            }
            """,
            "typescript",
        )
        self.assertIn("plain", names(document))
        self.assertIn("loaded", names(document))
        self.assertIn("Store", names(document))
        self.assertIn("Store.put", names(document))
        self.assertIn("Store.size", names(document))
        self.assertTrue(symbol(document, "loaded")["async"])
        self.assertFalse(symbol(document, "plain")["async"])

    def test_reports_typescript_interfaces_types_and_enums(self) -> None:
        document = scan(
            """
            interface Order { id: string; }
            type Result = { ok: boolean };
            enum Status { On, Off }
            """,
            "typescript",
        )
        self.assertIn("Order", names(document))
        self.assertIn("Result", names(document))
        self.assertIn("Status", names(document))
        self.assertEqual(symbol(document, "Order")["kind"], "interface")
        self.assertEqual(symbol(document, "Status")["kind"], "enum")

    def test_reports_arrow_functions_typed_and_untyped(self) -> None:
        document = scan(
            """
            const plain = (a, b) => a + b;
            const typed = (a: number): number => a * 2;
            const expression = async (a) => a;
            const plainValue = 42;
            """,
            "typescript",
        )
        self.assertEqual(symbol(document, "plain")["kind"], "function")
        self.assertEqual(symbol(document, "typed")["kind"], "function")
        self.assertEqual(symbol(document, "expression")["kind"], "function")
        self.assertEqual(symbol(document, "plainValue")["kind"], "constant")

    def test_records_documented_declaration_line_ranges(self) -> None:
        source = "// Loads the thing.\nfunction load() {\n  return 1;\n}\n"
        document = scan_javascript_source(source, path="doc.ts", language="typescript")
        entry = symbol(document, "load")
        self.assertEqual(entry["start_line"], 2)
        self.assertEqual(entry["end_line"], 4)
        self.assertEqual(entry["docstring"], "Loads the thing.")

    def test_reports_class_extends_clause(self) -> None:
        document = scan("class Base {}\nclass Child extends Base {}\n", "typescript")
        self.assertEqual(symbol(document, "Child")["extends"], "Base")


class JavaScriptImportTests(unittest.TestCase):
    def test_reports_module_specifiers_with_named_bindings(self) -> None:
        document = scan(
            'import { readFile, writeFile } from "node:fs/promises";\n'
            'import def from "./local.js";\n'
            'const lazy = require("./other.js");\n'
            'import "./side-effect.js";\n',
            "typescript",
        )
        modules = {item["module"] for item in document["imports"]}
        self.assertIn("node:fs/promises", modules)
        self.assertIn("./local.js", modules)
        self.assertIn("./other.js", modules)
        self.assertIn("./side-effect.js", modules)
        named = {
            item["name"]
            for item in document["imports"]
            if item["kind"] == "named_import"
        }
        self.assertEqual(named, {"readFile", "writeFile"})

    def test_reports_re_exports_separately(self) -> None:
        document = scan('export { helper } from "./helper.js";\n', "typescript")
        self.assertTrue(
            any(item["kind"] == "re_export" for item in document["imports"])
        )

    def test_counts_dynamic_imports_without_resolving_them(self) -> None:
        document = scan('async function go() { return await import("./late.js"); }\n')
        self.assertEqual(len(document["dynamic_imports"]), 1)
        self.assertNotIn("./late.js", {item["module"] for item in document["imports"]})


class JavaScriptMaskingTests(unittest.TestCase):
    def test_does_not_invent_symbols_from_regex_literals(self) -> None:
        document = scan("const p = /function fakeDecl\\(\\) \\{\\}/g;\n")
        self.assertNotIn("fakeDecl", names(document))

    def test_does_not_invent_symbols_from_template_literals(self) -> None:
        document = scan(
            "const t = `\n  function alsoFake() {}\n  class FakeClass {}\n`;\n"
        )
        self.assertNotIn("alsoFake", names(document))
        self.assertNotIn("FakeClass", names(document))

    def test_does_not_invent_symbols_from_comments(self) -> None:
        document = scan("// function commented() {}\n/* class AlsoCommented {} */\n")
        self.assertEqual(document["symbols"], [])

    def test_does_not_invent_imports_from_strings(self) -> None:
        document = scan("const s = 'import x from \"not-a-real-module\";';\n")
        self.assertEqual(document["imports"], [])

    def test_distinguishes_division_from_regex(self) -> None:
        document = scan(
            "function ratio(a, b) { return a.length / b.size; }\n", "typescript"
        )
        self.assertIn("ratio", names(document))
        self.assertEqual(document["uncertainties"], [])

    def test_handles_jsx_closing_tags_without_reporting_uncertainty(self) -> None:
        document = scan("class W {\n  render() { return <div>hi</div>; }\n}\n")
        self.assertEqual(document["uncertainties"], [])
        self.assertIn("W.render", names(document))

    def test_reports_uncertainty_for_unterminated_regex(self) -> None:
        document = scan("const p = /unclosed\n")
        self.assertTrue(
            any(
                item["reason"] == "unterminated_regex_literal"
                for item in document["uncertainties"]
            )
        )
        self.assertEqual(
            document["support_level"],
            "partial_declarations_with_reported_uncertainties",
        )

    def test_unterminated_string_fails_closed(self) -> None:
        with self.assertRaises(JavaScriptSourceError):
            scan('const a = "unterminated\n')

    def test_unterminated_block_comment_fails_closed(self) -> None:
        with self.assertRaises(JavaScriptSourceError):
            scan("/* never closed\nfunction a() {}\n")


class JavaScriptEffectTests(unittest.TestCase):
    def test_classifies_known_external_capabilities(self) -> None:
        document = scan(
            "async function go() {\n"
            '  await writeFile("a", "b");\n'
            "  const r = await fetch('https://example.com');\n"
            "  spawn('ls');\n"
            "  sqlite3.connect('db');\n"
            "}\n"
        )
        kinds = {effect["kind"] for effect in document["observed_effects"]}
        self.assertEqual(kinds, {"filesystem", "network", "process", "database"})

    def test_pure_code_reports_no_effects(self) -> None:
        document = scan("function add(a, b) { return a + b; }\n")
        self.assertEqual(document["observed_effects"], [])

    def test_does_not_treat_dotted_names_as_effects(self) -> None:
        document = scan("function run(client) { return client.send(); }\n")
        self.assertEqual(document["observed_effects"], [])


class JavaScriptLimitTests(unittest.TestCase):
    def test_reports_non_text_source_error(self) -> None:
        with self.assertRaises(JavaScriptSourceError):
            scan_javascript_source(b"bytes", path="x.js")  # type: ignore[arg-type]

    def test_rejects_unsupported_language(self) -> None:
        with self.assertRaises(JavaScriptSourceError):
            scan_javascript_source("const a = 1;\n", language="python")

    def test_symbol_limit_fails_safely(self) -> None:
        source = "\n".join(f"function f{i}() {{}}" for i in range(MAX_SYMBOLS + 5))
        with self.assertRaises(JavaScriptSourceError):
            scan(source)

    def test_nesting_limit_fails_safely(self) -> None:
        source = "function a(){" + "{" * 5000 + "}" * 5000 + "}"
        with self.assertRaises(JavaScriptSourceError):
            scan(source)

    def test_oversize_source_fails_safely(self) -> None:
        oversized = "a" * (MAX_SOURCE_BYTES + 1)
        with self.assertRaises(JavaScriptSourceError):
            scan(oversized)


class JavaScriptFileTests(unittest.TestCase):
    def test_selects_language_from_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, expected in (
                ("a.js", "javascript"),
                ("a.mjs", "javascript"),
                ("a.jsx", "javascript"),
                ("a.ts", "typescript"),
                ("a.tsx", "typescript"),
            ):
                path = root / name
                path.write_text("function f() {}\n", encoding="utf-8")
                self.assertEqual(scan_javascript_file(path)["language"], expected)

    def test_missing_file_fails_safely(self) -> None:
        with self.assertRaises(JavaScriptSourceError):
            scan_javascript_file(Path("does-not-exist.js"))

    def test_output_is_deterministic(self) -> None:
        source = "class A {\n  b() { return 1; }\n}\nfunction c() { return new A(); }\n"
        first = scan_javascript_source(source, path="a.ts", language="typescript")
        second = scan_javascript_source(source, path="a.ts", language="typescript")
        self.assertEqual(
            json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True)
        )


class SourceSchemaTests(unittest.TestCase):
    def test_javascript_document_satisfies_shared_contract(self) -> None:
        document = scan("function a() { return 1; }\n", "typescript")
        self.assertEqual(document["schema"], SOURCE_ANALYSIS_SCHEMA)
        validate_source_analysis(document)

    def test_suffix_mapping(self) -> None:
        python = adapter_for_suffix(".py")
        typescript = adapter_for_suffix(".TS")
        javascript = adapter_for_suffix(".mjs")
        assert python is not None
        assert typescript is not None
        assert javascript is not None
        self.assertEqual(python.language, "python")
        self.assertEqual(typescript.language, "typescript")
        self.assertEqual(javascript.language, "javascript")
        self.assertIsNone(adapter_for_suffix(".toml"))

    def test_validator_rejects_wrong_schema(self) -> None:
        with self.assertRaises(SourceAnalysisError):
            validate_source_analysis({"schema": "other", "language": "python"})

    def test_validator_rejects_unknown_symbol_kind(self) -> None:
        with self.assertRaises(SourceAnalysisError):
            build_source_analysis(
                language="python",
                path="a.py",
                source="x = 1\n",
                support_level="test",
                imports=[],
                module_calls=[],
                symbols=[
                    {
                        "kind": "wat",
                        "name": "a",
                        "qualified_name": "a",
                        "start_line": 1,
                        "end_line": 1,
                        "docstring": None,
                        "calls": [],
                    }
                ],
            )

    def test_validator_rejects_inverted_line_range(self) -> None:
        with self.assertRaises(SourceAnalysisError):
            build_source_analysis(
                language="python",
                path="a.py",
                source="x = 1\n",
                support_level="test",
                imports=[],
                module_calls=[],
                symbols=[
                    {
                        "kind": "function",
                        "name": "a",
                        "qualified_name": "a",
                        "start_line": 9,
                        "end_line": 2,
                        "docstring": None,
                        "calls": [],
                    }
                ],
            )

    def test_validator_permits_repeated_qualified_names(self) -> None:
        # Legal JavaScript: a module may declare the same name more than once.
        row = {
            "kind": "function",
            "name": "node",
            "qualified_name": "node",
            "start_line": 1,
            "end_line": 1,
            "docstring": None,
            "calls": [],
        }
        document = build_source_analysis(
            language="javascript",
            path="a.js",
            source="function node() {}\nfunction node() {}\n",
            support_level="test",
            imports=[],
            module_calls=[],
            symbols=[row, dict(row, start_line=5, end_line=5)],
        )
        self.assertEqual(len(document["symbols"]), 2)

    def test_builder_records_effects_from_symbol_calls(self) -> None:
        document = build_source_analysis(
            language="javascript",
            path="a.js",
            source="open('x');\n",
            support_level="test",
            imports=[],
            module_calls=[
                {"callee": "open", "line": 1, "observed_effect": "filesystem"}
            ],
            symbols=[],
        )
        self.assertEqual(document["observed_effects"][0]["kind"], "filesystem")
        self.assertEqual(document["observed_effects"][0]["symbol"], "<module>")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
