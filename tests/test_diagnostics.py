"""Tests for structured, machine-readable diagnostics.

The contract these tests protect is deliberately narrow: a stable code for each
failure category, a span that points at the declaration actually responsible,
and structured data an editing agent can act on without parsing prose.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kosika.diagnostics import (
    CODES,
    DIAGNOSTIC_SCHEMA,
    Severity,
    analyze,
    analyze_path,
    code_for,
    is_known_code,
    report,
    report_path,
)
from kosika.diagnostics_source import (
    HOLE,
    PASS_THROUGH,
    RaiseSite,
    coverage_report,
    iter_templates,
    raise_sites,
    unclassified,
    unreachable_templates,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

MISSING_FIELD = """record Greeting {
  text: Text
  name: Text
}

fn greeting() -> Greeting {
  return {text: "hi"}
}
"""

RETURN_MISMATCH = """record Greeting {
  text: Text
}

fn greeting() -> Greeting {
  return "not a record"
}
"""

CONDITION_TYPE = """fn f(n: Number) -> Number {
  return if n { 1 } else { 2 }
}
"""

BRANCH_MISMATCH = """fn f(n: Bool) -> Number {
  return if n { 1 } else { "two" }
}
"""

OPERATOR_MISMATCH = """fn f() -> Number {
  return 1 + "a"
}
"""

UNKNOWN_VARIABLE = """workflow main() -> Number {
  step s { let v: Number = 1 return v }
}
"""

AGENT_UNKNOWN_MODEL = """agent runner {
  model: ghost
  can: []
  cannot: []
}
"""

PARSE_ERROR = "record Broken { value: Text\n"
LEX_ERROR = "record R { value: Text }\n@@@\n"


def only(source: str):
    diagnostics = analyze(source)
    assert len(diagnostics) == 1, [d.message for d in diagnostics]
    return diagnostics[0]


class CodeRegistryTests(unittest.TestCase):
    def test_every_code_has_a_description(self) -> None:
        for code, description in CODES.items():
            self.assertTrue(description.strip(), f"{code} has no description")
            self.assertEqual(code, code.lower(), f"{code} is not lower case")

    def test_code_prefixes_are_expected(self) -> None:
        for code in CODES:
            self.assertTrue(
                code.startswith(("parse/", "check/", "lex/", "module/", "kosika/")),
                f"unexpected code namespace: {code}",
            )

    def test_unmapped_message_uses_explicit_fallback(self) -> None:
        # No real source currently produces an unmapped message, so the
        # fallback is exercised through the conversion API directly.
        from kosika.diagnostics import diagnostic_from_exception

        diagnostic = diagnostic_from_exception(
            ValueError("Step 'x' something nobody has classified yet")
        )
        self.assertEqual(diagnostic.code, "kosika/internal-error")
        # An unmapped message must never look like a confident category.
        self.assertTrue(diagnostic.data.get("unmapped"))

    def test_fallback_marks_itself_unmapped(self) -> None:
        from kosika.diagnostics import diagnostic_from_exception

        diagnostic = diagnostic_from_exception(ValueError("something entirely new"))
        self.assertEqual(diagnostic.code, "kosika/internal-error")
        self.assertTrue(diagnostic.data.get("unmapped"))
        self.assertIn("unmapped", diagnostic.to_dict()["data"])

    def test_is_known_code(self) -> None:
        self.assertTrue(is_known_code("check/missing-field"))
        self.assertFalse(is_known_code("check/not-a-real-code"))


class ClassificationTests(unittest.TestCase):
    def test_missing_field(self) -> None:
        diagnostic = only(MISSING_FIELD)
        self.assertEqual(diagnostic.code, "check/missing-field")
        self.assertEqual(diagnostic.data["missing_fields"], ["name"])
        self.assertEqual(diagnostic.owner, "greeting")
        self.assertEqual(diagnostic.owner_kind, "function")

    def test_missing_field_anchors_to_the_function(self) -> None:
        diagnostic = only(MISSING_FIELD)
        self.assertIsNotNone(diagnostic.span)
        # `greeting` is the 6th line, 0-indexed to 5.
        self.assertEqual(diagnostic.span.start_line, 5)
        self.assertEqual(diagnostic.span.start_character, 3)
        self.assertNotIn("anchor_missing", diagnostic.data)

    def test_return_type_mismatch(self) -> None:
        diagnostic = only(RETURN_MISMATCH)
        self.assertEqual(diagnostic.code, "check/return-type-mismatch")
        self.assertEqual(diagnostic.data["expected"], "Greeting")
        self.assertEqual(diagnostic.data["actual"], "Text")

    def test_condition_type(self) -> None:
        diagnostic = only(CONDITION_TYPE)
        self.assertEqual(diagnostic.code, "check/condition-type")
        self.assertEqual(diagnostic.data["expected"], "Bool")
        self.assertEqual(diagnostic.data["actual"], "Number")

    def test_branch_mismatch_lists_both_branches(self) -> None:
        diagnostic = only(BRANCH_MISMATCH)
        self.assertEqual(diagnostic.code, "check/branch-type-mismatch")
        self.assertEqual(diagnostic.data["branches"], ["Number", "Text"])

    def test_operator_mismatch_reports_operands(self) -> None:
        diagnostic = only(OPERATOR_MISMATCH)
        self.assertEqual(diagnostic.code, "check/operator-type-mismatch")
        self.assertEqual(diagnostic.data["operator"], "+")
        self.assertEqual(diagnostic.data["left"], "Number")
        self.assertEqual(diagnostic.data["right"], "Text")

    def test_parser_expected_and_actual_values_have_exact_token_span(self) -> None:
        source = "record Result { value Text }\n"
        diagnostic = only(source)
        self.assertEqual(diagnostic.code, "parse/expected-token")
        self.assertEqual(diagnostic.data["expected"], ":")
        self.assertEqual(diagnostic.data["actual"], "Text")
        assert diagnostic.span is not None
        line = source.splitlines()[diagnostic.span.start_line]
        self.assertEqual(
            line[diagnostic.span.start_character : diagnostic.span.end_character],
            "Text",
        )

    def test_binding_mismatch_reports_expected_and_actual(self) -> None:
        source = """record A { text: Text }
record B { text: Text }
fn make() -> A { return {text: "x"} }
workflow main() -> A {
  step make { let result: B = make() return result }
}
"""
        diagnostic = only(source)
        self.assertEqual(diagnostic.code, "check/return-type-mismatch")
        self.assertEqual(diagnostic.data["expected"], "B")
        self.assertEqual(diagnostic.data["actual"], "A")

    def test_ambiguous_step_name_has_no_invented_span(self) -> None:
        source = """record R { value: Number }
fn make(value: Number) -> R { return {value: value} }
workflow one() -> R {
  step same { let result = make(value: missing) return result }
}
workflow two() -> R {
  step same { let result = make(value: 1) return result }
}
"""
        diagnostic = only(source)
        self.assertEqual(diagnostic.code, "check/unknown-variable")
        self.assertIsNone(diagnostic.span)
        self.assertTrue(diagnostic.data["anchor_missing"])

    def test_agent_unknown_model_anchors_to_agent(self) -> None:
        diagnostic = only(AGENT_UNKNOWN_MODEL)
        self.assertEqual(diagnostic.code, "check/unknown-model")
        self.assertEqual(diagnostic.owner, "runner")
        self.assertIsNotNone(diagnostic.span)

    def test_valid_source_has_no_diagnostics(self) -> None:
        source = (REPO_ROOT / "examples" / "hello_world.kos").read_text("utf-8")
        self.assertEqual(analyze(source), [])

    def test_phase_is_reported(self) -> None:
        self.assertEqual(only(RETURN_MISMATCH).data["phase"], "check")
        self.assertEqual(only(PARSE_ERROR).data["phase"], "parse")
        self.assertEqual(only(LEX_ERROR).data["phase"], "parse")

    def test_lex_error_is_classified(self) -> None:
        self.assertEqual(only(LEX_ERROR).code, "lex/unexpected-character")

    def test_every_example_reports_a_known_code_when_broken(self) -> None:
        # A source that parses must either check clean or produce a known code,
        # never a silent fallback.
        for diagnostic in analyze(MISSING_FIELD + RETURN_MISMATCH):
            self.assertTrue(is_known_code(diagnostic.code))


class SerializationTests(unittest.TestCase):
    def test_to_dict_is_json_serialisable(self) -> None:
        payload = only(MISSING_FIELD).to_dict()
        json.dumps(payload)
        self.assertEqual(payload["code"], "check/missing-field")
        self.assertEqual(payload["severity"], int(Severity.ERROR))

    def test_to_lsp_uses_standard_fields(self) -> None:
        payload = only(MISSING_FIELD).to_lsp()
        self.assertEqual(payload["source"], "kosika")
        self.assertEqual(payload["code"], "check/missing-field")
        self.assertIn("data", payload)
        self.assertIn("range", payload)

    def test_report_shape(self) -> None:
        payload = report(MISSING_FIELD, path="broken.kos")
        json.dumps(payload)
        self.assertEqual(payload["schema"], DIAGNOSTIC_SCHEMA)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_count"], 1)
        self.assertEqual(payload["path"], "broken.kos")
        self.assertEqual(payload["codes"], ["check/missing-field"])

    def test_report_for_valid_source(self) -> None:
        source = (REPO_ROOT / "examples" / "hello_world.kos").read_text("utf-8")
        payload = report(source)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["diagnostics"], [])

    def test_code_for_is_stable_across_wording(self) -> None:
        # The whole point of a code: message wording may change, the code may not.
        self.assertEqual(
            code_for("Step 'a' is missing record fields ['x']"), "check/missing-field"
        )
        self.assertEqual(
            code_for("Duplicate record declaration"), "check/duplicate-declaration"
        )


class ImportedProgramTests(unittest.TestCase):
    def test_source_only_analysis_reports_unresolved_import(self) -> None:
        source = 'import "other.kos"\nrecord R { v: Text }\n'
        diagnostic = only(source)
        self.assertEqual(diagnostic.code, "parse/unsupported-module")
        self.assertTrue(diagnostic.data["anchor_missing"])

    def test_imported_parser_failure_has_imported_path_and_token_span(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / "library.kos"
            library.write_text("record {\n", encoding="utf-8")
            entry = root / "main.kos"
            entry.write_text('import "library.kos"\n', encoding="utf-8")

            diagnostic = analyze_path(entry)[0]

        self.assertEqual(diagnostic.code, "parse/expected-identifier")
        self.assertEqual(diagnostic.path, str(library.resolve()))
        self.assertNotIn(str(library.resolve()), diagnostic.message)
        self.assertIsNotNone(diagnostic.span)
        assert diagnostic.span is not None
        self.assertEqual(
            diagnostic.span.to_dict(),
            {
                "start": {"line": 0, "character": 7},
                "end": {"line": 0, "character": 8},
            },
        )

    def test_imported_checker_failure_has_imported_path_and_span(self) -> None:
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
            entry.write_text(
                'import "library.kos"\n'
                "workflow main() -> Result {\n"
                "  step call { let result = broken() return result }\n"
                "}\n",
                encoding="utf-8",
            )

            diagnostic = analyze_path(entry)[0]
            payload = report_path(entry)

        self.assertEqual(diagnostic.code, "check/missing-field")
        self.assertEqual(diagnostic.path, str(library.resolve()))
        self.assertEqual(diagnostic.data["missing_fields"], ["name"])
        span = diagnostic.span
        self.assertIsNotNone(span)
        assert span is not None
        self.assertEqual(span.start_line, 2)
        self.assertFalse(payload["ok"])
        self.assertEqual(
            payload["diagnostics"][0]["path"],
            str(library.resolve()),
        )

    def test_missing_import_has_module_code_and_missing_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "main.kos"
            entry.write_text('import "missing.kos"\n', encoding="utf-8")
            diagnostic = analyze_path(entry)[0]

        self.assertEqual(diagnostic.code, "module/not-found")
        diagnostic_path = diagnostic.path
        self.assertIsNotNone(diagnostic_path)
        assert diagnostic_path is not None
        self.assertTrue(diagnostic_path.endswith("missing.kos"))
        self.assertEqual(diagnostic.data["phase"], "module")


class RegistryCoverageTests(unittest.TestCase):
    """The registry must cover every raise site, in both directions.

    This is the test that makes a reworded error message a CI failure instead
    of a silent runtime regression. Without it, a rule that stops matching
    degrades a failure to ``kosika/internal-error`` and nothing notices.
    """

    def setUp(self) -> None:
        self.sites = raise_sites(REPO_ROOT)
        self.templates = list(iter_templates())

    def test_raise_sites_are_discovered(self) -> None:
        # A silently empty extraction would make every assertion below pass
        # for the wrong reason, so pin the discovery itself.
        self.assertGreater(len(self.sites), 140)
        kinds = {site.kind for site in self.sites}
        self.assertEqual(
            kinds,
            {"CheckError", "ParseError", "LexError", "ThinkingError", "MemoryError_"},
        )

    def test_every_raise_site_is_classified(self) -> None:
        missing = unclassified(self.sites, self.templates)
        self.assertEqual(
            [f"{site.label}: {site.text!r}" for site in missing],
            [],
            "raise sites no classification rule claims",
        )

    def test_no_dead_classification_rules(self) -> None:
        # A rule nothing can reach can never fail loudly, and invites the false
        # belief that a category is still detected.
        self.assertEqual(unreachable_templates(self.sites, self.templates), [])

    def test_pass_through_sites_are_declared_and_minimal(self) -> None:
        pass_through = [site for site in self.sites if site.is_pass_through]
        self.assertEqual(
            sorted(site.path for site in pass_through),
            sorted(PASS_THROUGH),
            "a pass-through site is declared but unused, or used but undeclared",
        )
        for site in pass_through:
            self.assertTrue(site.reason, f"{site.label} has no justification")

    def test_every_rule_code_is_registered(self) -> None:
        for code, _pattern in self.templates:
            self.assertTrue(is_known_code(code), f"rule emits unregistered code {code}")

    def test_every_site_renders_a_sample(self) -> None:
        for site in self.sites:
            if site.is_pass_through:
                continue
            sample = site.sample()
            self.assertNotIn(HOLE, sample, f"{site.label} left a hole unrendered")
            self.assertTrue(sample.strip(), f"{site.label} rendered empty")

    def test_hole_order_is_preserved(self) -> None:
        # Distinct sentinel values per hole mean a template that reorders its
        # interpolations would fail to match, which is the desired behaviour.
        site = RaiseSite(
            path="kosika/checker.py",
            line=1,
            kind="CheckError",
            text=f"Step {HOLE} expects {HOLE}",
            holes=(True, True),
        )
        self.assertEqual(site.sample(), "Step '7' expects '8'")

    def test_coverage_report_is_serialisable(self) -> None:
        import json as _json

        report = coverage_report(self.sites, self.templates)
        _json.dumps(report)
        self.assertEqual(report["unclassified"], [])
        self.assertEqual(report["unreachable_templates"], [])
        self.assertEqual(report["site_count"], len(self.sites))


class CapabilityTests(unittest.TestCase):
    """Capability failures are the errors an editing agent must act on.

    Each is a distinct code, and each reports the agent/tool pair, because
    "the agent used a tool it was not granted" and "the tool is not declared"
    need different repairs.
    """

    DENIED = """record Disk { mount: Text }
tool check_disk {
  source mcp "m"
  operation: "d"
  access: execute
  input: Disk
  output: Disk
}
agent monitor {
  can: []
  cannot: [check_disk]
}
workflow main() -> Disk {
  step s { let d: Disk = execute monitor.check_disk(mount: "/") return d }
}
"""

    def test_capability_denial_reports_agent_and_tool(self) -> None:
        diagnostic = only(self.DENIED)
        self.assertEqual(diagnostic.code, "check/capability-denied")
        self.assertEqual(diagnostic.data["agent"], "monitor")
        self.assertEqual(diagnostic.data["tool"], "check_disk")

    def test_capability_denial_anchors_to_the_agent(self) -> None:
        diagnostic = only(self.DENIED)
        self.assertEqual(diagnostic.owner, "monitor")
        self.assertIsNotNone(diagnostic.span, "capability errors must anchor")
        self.assertNotIn("anchor_missing", diagnostic.data)

    def test_approval_required_is_distinct_from_denial(self) -> None:
        source = """record Disk { mount: Text }
tool wipe {
  source mcp "m"
  operation: "w"
  access: execute
  input: Disk
  output: Disk
}
agent janitor {
  can: [wipe]
  cannot: []
}
workflow main() -> Disk {
  step s { let d: Disk = execute janitor.wipe(mount: "/") return d }
}
"""
        self.assertEqual(only(source).code, "check/approval-required")

    def test_out_of_range_limit(self) -> None:
        source = """record Q { t: Text }
record A { t: Text }
model writer {
  input: Q
  output: A
  deployment: "d"
  parameters: { temperature: 9.0 }
}
"""
        self.assertEqual(only(source).code, "check/invalid-limit")


class CommandLineTests(unittest.TestCase):
    """`check --format json` is the interface an agent will actually call."""

    def _run(self, *arguments: str) -> tuple[int, str]:
        process = subprocess.run(
            [sys.executable, "-m", "kosika.cli", *arguments],
            capture_output=True,
            cwd=str(REPO_ROOT),
            timeout=120,
        )
        return process.returncode, process.stdout.decode("utf-8")

    def test_valid_file_reports_ok(self) -> None:
        code, output = self._run(
            "check", "examples/hello_world.kos", "--format", "json"
        )
        payload = json.loads(output)
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["diagnostics"], [])
        self.assertEqual(payload["schema"], DIAGNOSTIC_SCHEMA)

    def test_broken_file_reports_structured_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.kos"
            path.write_text(MISSING_FIELD, encoding="utf-8")
            code, output = self._run("check", str(path), "--format", "json")
        payload = json.loads(output)
        self.assertEqual(code, 2)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_count"], 1)
        diagnostic = payload["diagnostics"][0]
        self.assertEqual(diagnostic["code"], "check/missing-field")
        self.assertEqual(diagnostic["data"]["missing_fields"], ["name"])
        self.assertIsNotNone(diagnostic["range"])

    def test_text_output_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.kos"
            path.write_text(MISSING_FIELD, encoding="utf-8")
            process = subprocess.run(
                [sys.executable, "-m", "kosika.cli", "check", str(path)],
                capture_output=True,
                cwd=str(REPO_ROOT),
                timeout=120,
            )
        # The human-facing path must keep its existing wording and exit code.
        self.assertEqual(process.returncode, 2)
        self.assertIn(b"Check failed:", process.stderr)
        self.assertEqual(process.stdout, b"")

    def test_multifile_project_is_still_checked(self) -> None:
        # `check` resolves imports; the JSON path must not regress to a
        # single-file analysis that would call an unresolvable program clean.
        code, output = self._run(
            "check", "examples/composed/main.kos", "--format", "json"
        )
        payload = json.loads(output)
        self.assertEqual(code, 0, output)
        self.assertTrue(payload["ok"])

    def test_multifile_failure_reports_imported_document(self) -> None:
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
            entry.write_text(
                'import "library.kos"\n'
                "workflow main() -> Result {\n"
                "  step call { let result = broken() return result }\n"
                "}\n",
                encoding="utf-8",
            )
            code, output = self._run("check", str(entry), "--format", "json")
            payload = json.loads(output)

        self.assertEqual(code, 2)
        diagnostic = payload["diagnostics"][0]
        self.assertEqual(diagnostic["code"], "check/missing-field")
        self.assertEqual(diagnostic["path"], str(library.resolve()))
        self.assertEqual(diagnostic["range"]["start"]["line"], 2)

    def test_diagnostics_coverage_command(self) -> None:
        code, output = self._run("diagnostics-coverage", "--check")
        self.assertEqual(code, 0, output)
        self.assertIn("raise sites:", output)
        self.assertNotIn("UNCLASSIFIED", output)
        self.assertNotIn("DEAD CODE", output)

    def test_diagnostics_coverage_command_json(self) -> None:
        code, output = self._run("diagnostics-coverage", "--format", "json")
        payload = json.loads(output)
        self.assertEqual(code, 0)
        self.assertEqual(payload["unclassified"], [])
        self.assertEqual(payload["unreachable_templates"], [])
        self.assertEqual(payload["site_count"], payload["classified"])


if __name__ == "__main__":
    unittest.main()
