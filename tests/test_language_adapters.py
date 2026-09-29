from __future__ import annotations

import unittest

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
        self.assertEqual(adapter_for_suffix(".PY").language, "python")
        self.assertEqual(adapter_for_suffix(".mjs").language, "javascript")
        self.assertEqual(adapter_for_suffix(".tsx").language, "typescript")
        self.assertEqual(adapter_for_suffix(".rs").language, "rust")
        self.assertIsNone(adapter_for_language("ruby"))

        analysis = adapter_for_suffix(".ts").analyze_source(
            "interface User { name: string; }\n",
            path="user.ts",
        )
        self.assertEqual(analysis["language"], "typescript")

        rust_analysis = adapter_for_suffix(".rs").analyze_source(
            'pub fn greet() { println!("hi"); }\n',
            path="lib.rs",
        )
        self.assertEqual(rust_analysis["language"], "rust")
        self.assertEqual(rust_analysis["symbols"][0]["name"], "greet")

    def test_registry_rejects_duplicate_language_and_suffix_ownership(self) -> None:
        def analyze(_source: str, _path: str) -> dict:
            return {}

        def analyze_file(_path: str) -> dict:
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
