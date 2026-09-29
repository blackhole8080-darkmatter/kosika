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
from kosika.python_adapter import (
    PythonSourceError,
    analyze_python_file,
    analyze_python_source,
)


class PythonAdapterTests(unittest.TestCase):
    def test_extracts_imports_symbols_calls_and_obvious_effects(self) -> None:
        source = '''"""Small sample module."""
import pathlib as pl
from os import path

open("marker.txt")

class Store:
    """Keeps records."""
    def save(self, value):
        pl.Path(value).write_text("saved")

async def fetch(url):
    import requests
    return requests.get(url)
'''
        analysis = analyze_python_source(source, path="sample.py")

        self.assertEqual(analysis["language"], "python")
        self.assertEqual(analysis["analysis_mode"], "structural_ast")
        self.assertEqual(analysis["module_docstring"], "Small sample module.")
        self.assertEqual(analysis["source"]["path"], "sample.py")
        self.assertEqual(
            [item["module"] for item in analysis["imports"]],
            ["pathlib", "os", "requests"],
        )
        symbols = {item["qualified_name"]: item for item in analysis["symbols"]}
        self.assertEqual(symbols["Store"]["kind"], "class")
        self.assertEqual(symbols["Store.save"]["docstring"], None)
        save_calls = symbols["Store.save"]["calls"]
        self.assertEqual(save_calls[0]["observed_effect"], "filesystem")
        self.assertEqual(save_calls[1]["callee"], "pl.Path")
        self.assertTrue(symbols["fetch"]["async"])
        self.assertEqual(symbols["fetch"]["calls"][0]["callee"], "requests.get")
        self.assertEqual(
            [effect["kind"] for effect in analysis["observed_effects"]],
            ["filesystem", "filesystem", "network"],
        )
        self.assertTrue(any("not analyzed" in item for item in analysis["limitations"]))

    def test_output_is_deterministic_and_never_executes_or_imports_source(self) -> None:
        source = """
raise RuntimeError("must not execute")
import definitely_not_installed

def process():
    open("never-created.txt", "w")
"""
        first = analyze_python_source(source)
        second = analyze_python_source(source)
        self.assertEqual(first, second)
        self.assertEqual(first["source"]["sha256"], second["source"]["sha256"])
        self.assertFalse(Path("never-created.txt").exists())

    def test_module_level_calls_are_reported_separately(self) -> None:
        analysis = analyze_python_source('print("hello")\ndef f():\n    len([])\n')
        self.assertEqual(analysis["module_calls"][0]["callee"], "print")
        function = analysis["symbols"][0]
        self.assertEqual(function["calls"][0]["callee"], "len")

    def test_class_body_and_eager_default_calls_are_not_misattributed(self) -> None:
        analysis = analyze_python_source(
            "class Example:\n"
            "    configure(make_config())\n"
            "    def method(self, value=build_default()):\n"
            "        return value\n"
        )
        symbols = {item["qualified_name"]: item for item in analysis["symbols"]}
        self.assertEqual(symbols["Example"]["calls"][0]["callee"], "configure")
        self.assertEqual(symbols["Example"]["calls"][1]["callee"], "make_config")
        self.assertEqual(symbols["Example"]["calls"][2]["callee"], "build_default")
        self.assertEqual(symbols["Example.method"]["calls"], [])

    def test_complex_call_expressions_are_marked_dynamic(self) -> None:
        analysis = analyze_python_source(
            "def invoke(factory):\n    return factory()()\n"
        )
        self.assertEqual(analysis["symbols"][0]["calls"][0]["callee"], "factory(...)")
        self.assertEqual(analysis["symbols"][0]["calls"][1]["callee"], "factory")

    def test_rejects_invalid_syntax_and_oversized_source(self) -> None:
        with self.assertRaisesRegex(PythonSourceError, "syntax could not be parsed"):
            analyze_python_source("def broken(:\n pass")
        with patch("kosika.python_adapter.MAX_SOURCE_BYTES", 4):
            with self.assertRaisesRegex(PythonSourceError, "4-byte"):
                analyze_python_source("print('too large')")

    def test_file_adapter_reads_utf8_without_importing_and_rejects_invalid_bytes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "sample.py"
            source_path.write_text("def greet():\n    return 'hi'\n", encoding="utf-8")
            result = analyze_python_file(source_path)
            self.assertEqual(result["source"]["path"], source_path.as_posix())
            self.assertEqual(result["symbols"][0]["qualified_name"], "greet")

            source_path.write_bytes(b"\xff")
            with self.assertRaisesRegex(PythonSourceError, "UTF-8"):
                analyze_python_file(source_path)

    def test_cli_analyze_json_and_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "sample.py"
            source_path.write_text("def main():\n    return 42\n", encoding="utf-8")
            for output_format in ("json", "text"):
                output = io.StringIO()
                with (
                    patch.object(
                        sys,
                        "argv",
                        [
                            "kosika",
                            "analyze",
                            str(source_path),
                            "--format",
                            output_format,
                        ],
                    ),
                    contextlib.redirect_stdout(output),
                ):
                    self.assertEqual(cli.main(), 0)
                if output_format == "json":
                    analysis = json.loads(output.getvalue())
                    self.assertEqual(analysis["language"], "python")
                    self.assertEqual(analysis["symbols"][0]["name"], "main")
                else:
                    self.assertIn("Python structure:", output.getvalue())
                    self.assertIn("main", output.getvalue())

    def test_cli_reports_syntax_error_without_running_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "broken.py"
            source_path.write_text("def broken(:\n pass", encoding="utf-8")
            error = io.StringIO()
            with (
                patch.object(sys, "argv", ["kosika", "analyze", str(source_path)]),
                contextlib.redirect_stderr(error),
            ):
                self.assertEqual(cli.main(), 2)
            self.assertIn("Source analysis failed safely", error.getvalue())


if __name__ == "__main__":
    unittest.main()
