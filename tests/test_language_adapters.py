from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

from kosika.language_adapters import (
    SUPPORTED_LANGUAGES,
    LanguageAdapter,
    LanguageAdapterError,
    LanguageAdapterRegistry,
    adapter_for_language,
    adapter_for_suffix,
)


class LanguageAdapterRegistryTests(unittest.TestCase):
    def test_builtins_own_supported_suffixes_and_dispatch_language(self) -> None:
        self.assertEqual(
            SUPPORTED_LANGUAGES,
            ("python", "javascript", "typescript", "rust"),
        )
        python = adapter_for_suffix(".PY")
        javascript = adapter_for_suffix(".mjs")
        typescript = adapter_for_suffix(".tsx")
        rust = adapter_for_suffix(".rs")
        assert python is not None
        assert javascript is not None
        assert typescript is not None
        assert rust is not None
        self.assertEqual(python.language, "python")
        self.assertEqual(javascript.language, "javascript")
        self.assertEqual(typescript.language, "typescript")
        self.assertEqual(rust.language, "rust")
        self.assertIsNone(adapter_for_language("ruby"))

        analysis = typescript.analyze_source(
            "interface User { name: string; }\n",
            path="user.ts",
        )
        self.assertEqual(analysis["language"], "typescript")

        rust_analysis = rust.analyze_source(
            'pub fn greet() { println!("hi"); }\n',
            path="lib.rs",
        )
        self.assertEqual(rust_analysis["language"], "rust")
        self.assertEqual(rust_analysis["symbols"][0]["name"], "greet")

    def test_registry_rejects_duplicate_language_and_suffix_ownership(self) -> None:
        def analyze(_source: str, _path: str) -> dict[str, Any]:
            return {}

        def analyze_file(_path: str | Path) -> dict[str, Any]:
            return {}

        adapter = LanguageAdapter(
            "python",
            (".py",),
            1,
            1,
            analyze,
            analyze_file,
        )
        with self.assertRaisesRegex(LanguageAdapterError, "Duplicate language"):
            LanguageAdapterRegistry((adapter, adapter))

        other = LanguageAdapter(
            "other",
            (".py",),
            1,
            1,
            analyze,
            analyze_file,
        )
        with self.assertRaisesRegex(LanguageAdapterError, "claimed"):
            LanguageAdapterRegistry((adapter, other))


if __name__ == "__main__":
    unittest.main()
