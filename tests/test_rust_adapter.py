from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kosika.project_analysis import analyze_python_project
from kosika.rust_adapter import (
    RustSourceError,
    analyze_rust_file,
    analyze_rust_source,
)
from kosika.source_map import build_source_map


class RustAdapterTests(unittest.TestCase):
    def test_extracts_rust_declarations_imports_methods_calls_and_effects(self) -> None:
        source = """
use std::fs::File;
use reqwest::Client;

/// Storage service.
pub struct Store {
    path: String,
}

pub enum Status { Ready, Failed }
pub trait Loadable { fn load(&self); }

impl Store {
    pub fn load(&self) {
        File::open(&self.path);
    }
}

pub async fn fetch(client: Client) {
    reqwest::Client::get("https://example.com");
}
"""
        analysis = analyze_rust_source(source, path="store.rs")

        self.assertEqual(analysis["language"], "rust")
        self.assertEqual(analysis["analysis_mode"], "parser_ast")
        self.assertEqual(analysis["parser_backend"], "tree-sitter-rust")
        self.assertEqual(
            [item["module"] for item in analysis["imports"]],
            ["std::fs::File", "reqwest::Client"],
        )
        symbols = analysis["symbols"]
        store_struct = next(item for item in symbols if item["kind"] == "struct")
        self.assertEqual(store_struct["qualified_name"], "Store")
        store_load = next(
            item for item in symbols if item["qualified_name"] == "Store.load"
        )
        self.assertEqual(store_load["kind"], "method")
        self.assertEqual(store_load["calls"][0]["observed_effect"], "filesystem")
        fetch = next(item for item in symbols if item["qualified_name"] == "fetch")
        self.assertEqual(fetch["async"], True)
        self.assertEqual(fetch["calls"][0]["callee"], "reqwest::Client::get")
        self.assertEqual(fetch["calls"][0]["observed_effect"], "network")
        self.assertEqual(store_struct["docstring"], "Storage service.")

    def test_project_and_source_map_consumers_use_rust_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "lib.rs").write_text(
                "pub struct Store {}\n"
                "impl Store {\n"
                "    pub fn load(&self) { helper(); }\n"
                "}\n"
                "fn helper() {}\n",
                encoding="utf-8",
            )

            source_map = build_source_map(root)
            self.assertEqual(source_map["summary"]["language_file_counts"], {"rust": 1})
            self.assertTrue(
                any(edge["relation"] == "member_of" for edge in source_map["edges"])
            )

            project = analyze_python_project(root)
            self.assertEqual(project["summary"]["language_file_counts"], {"rust": 1})
            self.assertEqual(project["files"][0]["language"], "rust")

    def test_masking_does_not_invent_declarations_or_calls(self) -> None:
        source = """// fn fake() { File::open("no"); }
const TEXT: &str = r#"fn hidden() { println!(\"no\"); }"#;
fn real() { helper(); }
"""
        analysis = analyze_rust_source(source)
        names = [item["qualified_name"] for item in analysis["symbols"]]
        self.assertEqual(names, ["TEXT", "real"])
        self.assertEqual(analysis["symbols"][1]["calls"][0]["callee"], "helper")

    def test_output_is_deterministic_and_file_analysis_is_read_only(self) -> None:
        source = "pub fn run() -> i32 { 42 }\n"
        first = analyze_rust_source(source)
        second = analyze_rust_source(source)
        self.assertEqual(first, second)
        self.assertEqual(
            json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True)
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lib.rs"
            path.write_text(source, encoding="utf-8")
            result = analyze_rust_file(path)
            self.assertEqual(result["source"]["path"], path.as_posix())
            path.write_bytes(b"\xff")
            with self.assertRaisesRegex(RustSourceError, "UTF-8"):
                analyze_rust_file(path)

    def test_rejects_invalid_and_oversized_source(self) -> None:
        with self.assertRaisesRegex(RustSourceError, "parser found syntax errors"):
            analyze_rust_source("/* unfinished")
        with self.assertRaisesRegex(RustSourceError, "parser found syntax errors"):
            analyze_rust_source('fn broken() { println!("unfinished); }')
        with patch("kosika.rust_adapter.parser_available", return_value=False):
            with self.assertRaisesRegex(RustSourceError, "unterminated block comment"):
                analyze_rust_source("/* unfinished")
        with self.assertRaisesRegex(RustSourceError, "1-byte"):
            with patch("kosika.rust_adapter.MAX_SOURCE_BYTES", 1):
                analyze_rust_source("fn")


if __name__ == "__main__":
    unittest.main()
