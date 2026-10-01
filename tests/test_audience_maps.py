"""Tests for audience-adaptive maps.

The module's whole reason to exist is that four different kinds of fact stay
apart: what the checker verified, what a component contract claims, what a run
observed, and what a human asserted. These tests mostly assert that separation
holds, because that is the property that cannot be recovered later once a map
has been read as if it were uniform.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from kosika import cli
from kosika.audience_maps import (
    AUDIENCE_MAP_SCHEMA,
    AUDIENCES,
    EVIDENCE_CHECKED,
    EVIDENCE_CLASSES,
    EVIDENCE_DECLARED,
    EVIDENCE_HUMAN,
    EVIDENCE_OBSERVED,
    AudienceEdge,
    AudienceEntry,
    AudienceMap,
    AudienceMapError,
    _merge,
    build_audience_map,
    to_mermaid,
    to_text,
    verify,
)
from kosika.components import load_component_contract

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
HELLO = EXAMPLES / "hello_world.kos"
CLOSURES = EXAMPLES / "closures.kos"
COMPONENT_SOURCE = EXAMPLES / "component_uppercase.kos"
COMPONENT_CONTRACT = EXAMPLES / "components" / "uppercase.component.json"
COMPONENT_INPUT = EXAMPLES / "component_uppercase_input.json"
TEST_HOST = EXAMPLES / "test_host.kos"


def run_component(source: Path, trace: Path) -> str:
    """Run the component example for real and leave a validated trace behind."""

    output = io.StringIO()
    with (
        patch.object(
            sys,
            "argv",
            [
                "kosika",
                "run",
                str(source),
                "--input",
                str(COMPONENT_INPUT),
                "--trace",
                str(trace),
                "--component-manifest",
                str(COMPONENT_CONTRACT),
                "--component-runtime",
                shutil.which("node") or "node",
                "--allow-component-effect",
                "cpu:compute",
                "--allow-component-capability",
                "text:transform",
            ],
        ),
        contextlib.redirect_stdout(output),
    ):
        result = cli.main()
    if result != 0:
        raise AssertionError(f"component run failed: {output.getvalue()}")
    return output.getvalue()


class AudienceMapBuildTests(unittest.TestCase):
    def test_every_adience_builds_a_verifiable_map(self) -> None:
        for audience in AUDIENCES:
            with self.subTest(audience=audience):
                result = build_audience_map(HELLO, audience=audience)
                report = verify(result)
                self.assertTrue(report["ok"], report["problems"])
                self.assertEqual(report["schema"], AUDIENCE_MAP_SCHEMA)
                self.assertEqual(result.audience, audience)
                self.assertTrue(result.question)

    def test_unknown_audience_is_rejected(self) -> None:
        with self.assertRaises(AudienceMapError) as caught:
            build_audience_map(HELLO, audience="nobody")
        self.assertIn("Unknown audience", str(caught.exception))

    def test_non_string_audience_is_rejected(self) -> None:
        with self.assertRaises(AudienceMapError):
            build_audience_map(HELLO, audience=object())  # type: ignore[arg-type]

    def test_missing_source_is_reported_as_a_map_error(self) -> None:
        with self.assertRaises(AudienceMapError):
            build_audience_map(EXAMPLES / "does_not_exist.kos", audience="overview")

    def test_maps_are_deterministic_across_repeated_builds(self) -> None:
        first = build_audience_map(CLOSURES, audience="maintainer")
        second = build_audience_map(CLOSURES, audience="maintainer")
        self.assertEqual(first.digest, second.digest)
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(to_text(first), to_text(second))
        self.assertEqual(to_mermaid(first), to_mermaid(second))

    def test_entry_outside_the_declared_root_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "boundary"
            root.mkdir()
            with self.assertRaises(AudienceMapError):
                build_audience_map(HELLO, audience="overview", root=root)


class SourceLinkTests(unittest.TestCase):
    def test_declarations_link_to_a_source_line(self) -> None:
        result = build_audience_map(CLOSURES, audience="maintainer")
        linked = [
            entry for entry in result.entries if ":" in entry.source.split("/")[-1]
        ]
        self.assertTrue(linked, "expected at least one declaration linked to a line")
        for entry in linked:
            module, _, line = entry.source.rpartition(":")
            self.assertTrue(module.endswith(".kos"))
            self.assertGreaterEqual(int(line), 1)

    def test_declaration_links_ignore_comments_and_nested_declaration_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "commented.kos"
            source.write_text(
                "// fn target() { }\n"
                "fn target() -> Number { return 1 }\n"
                "fn wrapper() -> Number {\n"
                "  fn target() -> Number { return 2 }\n"
                "  return target()\n"
                "}\n",
                encoding="utf-8",
            )
            result = build_audience_map(source, audience="maintainer")
            target = next(
                entry for entry in result.entries if entry.id == "function:target"
            )
            nested = next(
                entry
                for entry in result.entries
                if entry.id == "function:wrapper/target"
            )
            self.assertEqual(target.source, "commented.kos:2")
            self.assertEqual(nested.source, "commented.kos:4")

    def test_step_links_ignore_numbered_display_label(self) -> None:
        result = build_audience_map(CLOSURES, audience="maintainer")
        invoke = next(
            entry
            for entry in result.entries
            if entry.kind == "step" and entry.id == "step:main:invoke"
        )
        self.assertEqual(invoke.label, "4. invoke")
        self.assertEqual(invoke.source, "closures.kos:43")

    def test_imported_declarations_link_to_their_own_module(self) -> None:
        source = EXAMPLES / "composed" / "main.kos"
        result = build_audience_map(source, audience="maintainer")
        answer = next(
            entry for entry in result.entries if entry.id == "record:Answer"
        )
        research = next(
            entry for entry in result.entries if entry.id == "workflow:research"
        )
        self.assertEqual(answer.source, "types.kos:2")
        self.assertEqual(research.source, "research.kos:3")

    def test_source_override_controls_its_source_link(self) -> None:
        override = "\n\nrecord Greeting { text: Text }\n\n"
        result = build_audience_map(
            HELLO,
            audience="maintainer",
            source_overrides={HELLO: override},
        )
        greeting = next(
            entry for entry in result.entries if entry.id == "record:Greeting"
        )
        self.assertEqual(greeting.source, "hello_world.kos:3")


class EvidenceSeparationTests(unittest.TestCase):
    def test_checked_only_map_makes_no_other_claims(self) -> None:
        result = build_audience_map(HELLO, audience="overview")
        self.assertEqual(result.entries_with(EVIDENCE_OBSERVED), ())
        self.assertEqual(result.entries_with(EVIDENCE_DECLARED), ())
        self.assertEqual(result.entries_with(EVIDENCE_HUMAN), ())
        self.assertEqual(result.evidence_counts[EVIDENCE_OBSERVED], 0)
        self.assertEqual(result.evidence_limits, ())

    def test_every_entry_declares_a_known_evidence_class(self) -> None:
        result = build_audience_map(CLOSURES, audience="maintainer")
        for entry in result.entries:
            self.assertTrue(entry.evidence, entry.id)
            for name in entry.evidence:
                self.assertIn(name, EVIDENCE_CLASSES)

    def test_edges_carry_exactly_one_evidence_class(self) -> None:
        result = build_audience_map(CLOSURES, audience="operator")
        for edge in result.edges:
            self.assertIn(edge.evidence, EVIDENCE_CLASSES)

    def test_human_notes_are_labelled_and_flagged_unverified(self) -> None:
        result = build_audience_map(
            HELLO, audience="overview", purpose=["Answers a greeting."]
        )
        notes = result.entries_with(EVIDENCE_HUMAN)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].kind, "purpose")
        self.assertEqual(notes[0].source, "human:purpose-note")
        self.assertIs(notes[0].detail["independently_verified"], False)
        self.assertIn(
            "human-purpose-not-independently-verified", result.evidence_limits
        )
        self.assertTrue(verify(result)["ok"])

    def test_purpose_note_validation_fails_closed(self) -> None:
        cases = {
            "too many": tuple(f"note {index}" for index in range(21)),
            "empty": ("",),
            "whitespace only": ("   ",),
            "too long": ("x" * 1001,),
            "duplicate": ("same", "same"),
        }
        for label, notes in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(AudienceMapError):
                    build_audience_map(HELLO, audience="overview", purpose=notes)


class AudienceDetailTests(unittest.TestCase):
    def test_outsider_gets_connection_level_map_without_record_schemas(self) -> None:
        outsider = build_audience_map(TEST_HOST, audience="outsider")
        overview = build_audience_map(TEST_HOST, audience="overview")

        self.assertFalse(any(entry.kind == "record" for entry in outsider.entries))
        self.assertTrue(any(entry.kind == "record" for entry in overview.entries))
        self.assertTrue(any(edge.relation == "output" for edge in outsider.edges))
        self.assertTrue(any(entry.kind == "tool" for entry in outsider.entries))
        outsider_tool = next(entry for entry in outsider.entries if entry.kind == "tool")
        self.assertEqual(outsider_tool.detail["access"], "observe")
        self.assertTrue(verify(outsider)["ok"])
        self.assertTrue(verify(overview)["ok"])

    def test_maintainer_sees_fields_and_operator_does_not(self) -> None:
        maintainer = build_audience_map(CLOSURES, audience="maintainer")
        operator = build_audience_map(CLOSURES, audience="operator")
        self.assertTrue(any(entry.kind == "field" for entry in maintainer.entries))
        self.assertFalse(any(entry.kind == "field" for entry in operator.entries))

    def test_operator_hides_authority_that_reviewer_sees(self) -> None:
        operator = build_audience_map(HELLO, audience="operator")
        reviewer = build_audience_map(HELLO, audience="reviewer")
        hidden = ("access", "capabilities", "cannot", "approval_required")
        for entry in operator.entries:
            for key in hidden:
                self.assertNotIn(key, entry.detail, entry.id)
        self.assertTrue(reviewer.entries)

    def test_narrower_audience_shows_no_more_checked_entries(self) -> None:
        maintainer = build_audience_map(CLOSURES, audience="maintainer")
        reviewer = build_audience_map(CLOSURES, audience="reviewer")
        self.assertLessEqual(
            reviewer.coverage["checked_shown"], maintainer.coverage["checked_shown"]
        )
        self.assertLessEqual(
            reviewer.coverage["checked_shown"], reviewer.coverage["checked_total"]
        )

    def test_coverage_reports_omissions_instead_of_hiding_them(self) -> None:
        result = build_audience_map(CLOSURES, audience="reviewer")
        self.assertGreater(result.coverage["checked_total"], 0)
        self.assertLessEqual(
            result.coverage["checked_shown"], result.coverage["checked_total"]
        )


class TraceObservationTests(unittest.TestCase):
    def test_matching_trace_adds_observed_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            output = io.StringIO()
            with (
                patch.object(
                    sys,
                    "argv",
                    ["kosika", "run", str(HELLO), "--trace", str(trace)],
                ),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(cli.main(), 0)
            result = build_audience_map(HELLO, audience="operator", trace_path=trace)
            self.assertTrue(result.entries_with(EVIDENCE_OBSERVED))
            self.assertNotIn("trace-program-hash-mismatch", result.evidence_limits)
            self.assertTrue(verify(result)["ok"])

    def test_observation_visibility_and_result_detail_vary_by_audience(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            with (
                patch.object(
                    sys,
                    "argv",
                    ["kosika", "run", str(HELLO), "--trace", str(trace)],
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(cli.main(), 0)

            maps = {
                audience: build_audience_map(HELLO, audience=audience, trace_path=trace)
                for audience in AUDIENCES
            }

        for audience in ("outsider", "overview", "maintainer"):
            with self.subTest(audience=audience):
                self.assertEqual(maps[audience].entries_with(EVIDENCE_OBSERVED), ())
                self.assertIn(
                    "trace-details-not-included-for-audience",
                    maps[audience].evidence_limits,
                )
        operator_run = next(
            entry for entry in maps["operator"].entries if entry.kind == "run"
        )
        reviewer_run = next(
            entry for entry in maps["reviewer"].entries if entry.kind == "run"
        )
        self.assertIn("eventCount", operator_run.detail)
        self.assertNotIn("result", operator_run.detail)
        self.assertEqual(reviewer_run.detail["result"], {"text": "Hello, world!"})
        self.assertNotIn('"Hello, world!"', to_text(maps["operator"]))
        self.assertIn('"Hello, world!"', to_text(maps["reviewer"]))
        self.assertTrue(verify(maps["operator"])["ok"])
        self.assertTrue(verify(maps["reviewer"])["ok"])

    def test_mismatched_trace_contributes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            output = io.StringIO()
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "kosika",
                        "run",
                        str(ROOT / "examples" / "test_host.kos"),
                        "--trace",
                        str(trace),
                    ],
                ),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(cli.main(), 0)
            # The trace describes test_host.kos; mapping hello_world.kos with it
            # is exactly the cross-program collapse the module forbids.
            result = build_audience_map(HELLO, audience="operator", trace_path=trace)
            self.assertIn("trace-program-hash-mismatch", result.evidence_limits)
            self.assertEqual(result.entries_with(EVIDENCE_OBSERVED), ())
            self.assertTrue(verify(result)["ok"])

    def test_trace_without_program_hash_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "kosika",
                        "run",
                        str(HELLO),
                        "--trace",
                        str(trace),
                    ],
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(cli.main(), 0)
            events = [json.loads(line) for line in trace.read_text().splitlines()]
            events[0].pop("program_hash", None)
            trace.write_text(
                "\n".join(json.dumps(event) for event in events), encoding="utf-8"
            )
            result = build_audience_map(HELLO, audience="reviewer", trace_path=trace)

        self.assertEqual(result.entries_with(EVIDENCE_OBSERVED), ())
        self.assertIn("trace-program-hash-unavailable", result.evidence_limits)
        self.assertTrue(verify(result)["ok"])

    def test_unreadable_trace_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            trace.write_text("this is not a trace\n", encoding="utf-8")
            with self.assertRaises(AudienceMapError) as caught:
                build_audience_map(HELLO, audience="overview", trace_path=trace)
            self.assertIn("Trace could not be validated", str(caught.exception))

    def test_missing_trace_path_fails_closed(self) -> None:
        with self.assertRaises(AudienceMapError):
            build_audience_map(
                HELLO, audience="overview", trace_path=ROOT / "no_such_trace.jsonl"
            )


class ComponentContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_component_contract(COMPONENT_CONTRACT)

    def test_matching_contract_becomes_declared_evidence(self) -> None:
        result = build_audience_map(
            COMPONENT_SOURCE, audience="reviewer", contracts=[self.contract]
        )
        components = result.entries_with(EVIDENCE_DECLARED)
        self.assertEqual(len(components), 1)
        entry = components[0]
        self.assertEqual(entry.kind, "component")
        self.assertEqual(entry.label, "example.uppercase")
        self.assertEqual(entry.source, "contract:example.uppercase@1.0.0")
        self.assertEqual(entry.detail["access"], "analyze")
        self.assertIn("effects", entry.detail)
        self.assertEqual(
            entry.detail["artifactSha256"], self.contract.implementation.sha256
        )
        self.assertTrue(verify(result)["ok"])

    def test_non_authority_audience_sees_identity_without_authority(self) -> None:
        result = build_audience_map(
            COMPONENT_SOURCE, audience="operator", contracts=[self.contract]
        )
        entry = next(iter(result.entries_with(EVIDENCE_DECLARED)))
        self.assertIn("version", entry.detail)
        self.assertNotIn("access", entry.detail)
        self.assertNotIn("effects", entry.detail)
        self.assertNotIn("capabilities", entry.detail)

    def test_contract_that_does_not_match_a_checked_tool_is_rejected(self) -> None:
        mismatched = replace(self.contract, access="execute")
        with self.assertRaises(AudienceMapError) as caught:
            build_audience_map(
                COMPONENT_SOURCE, audience="reviewer", contracts=[mismatched]
            )
        self.assertIn("does not match a checked tool", str(caught.exception))

    def test_contract_for_an_absent_tool_is_rejected(self) -> None:
        with self.assertRaises(AudienceMapError):
            build_audience_map(HELLO, audience="reviewer", contracts=[self.contract])


class ComponentObservationTests(unittest.TestCase):
    """The distinction the module is most likely to get wrong."""

    def test_executing_a_component_never_makes_its_contract_observed(self) -> None:
        if shutil.which("node") is None:
            self.skipTest("Node.js is required to execute the component")
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            run_component(COMPONENT_SOURCE, trace)
            contract = load_component_contract(COMPONENT_CONTRACT)
            result = build_audience_map(
                COMPONENT_SOURCE,
                audience="reviewer",
                trace_path=trace,
                contracts=[contract],
            )
            component = next(
                entry for entry in result.entries if entry.kind == "component"
            )
            self.assertEqual(component.evidence, (EVIDENCE_DECLARED,))
            component_edges = [
                edge
                for edge in result.edges
                if edge.evidence == EVIDENCE_OBSERVED and edge.target == component.id
            ]
            self.assertEqual(
                component_edges, [], "a declared component was reported as observed"
            )
            self.assertTrue(verify(result)["ok"])


class EvidenceMergeTests(unittest.TestCase):
    """The merge rule is the module's central claim, so it is tested directly.

    Nothing in the current example programs makes a checked node and an observed
    node share an id, so this path cannot be reached through `build_audience_map`
    alone. Exercising it here is what makes the claim "one entry, both classes"
    an assertion rather than an intention.
    """

    def checked(self) -> AudienceEntry:
        return AudienceEntry(
            id="workflow:main",
            kind="workflow",
            label="main",
            evidence=(EVIDENCE_CHECKED,),
            source="main.kos:1",
            detail={"input": "Request"},
        )

    def observed(self, step: str = "main/first") -> AudienceEntry:
        return AudienceEntry(
            id="workflow:main",
            kind="step",
            label=step,
            evidence=(EVIDENCE_OBSERVED,),
            source="trace:run.jsonl",
            detail={"call": "tool_call", "target": "uppercase"},
        )

    def test_one_id_with_two_classes_is_emitted_once(self) -> None:
        merged = _merge([self.checked(), self.observed()])
        self.assertEqual(len(merged), 1)
        entry = merged[0]
        self.assertEqual(entry.evidence, (EVIDENCE_CHECKED, EVIDENCE_OBSERVED))
        self.assertEqual(entry.source, "main.kos:1")
        self.assertEqual(entry.detail["input"], "Request")
        self.assertEqual(
            entry.detail["observations"], [{"call": "tool_call", "target": "uppercase"}]
        )

    def test_evidence_order_is_registry_order_not_arrival_order(self) -> None:
        merged = _merge([self.observed(), self.checked()])
        self.assertEqual(merged[0].evidence, (EVIDENCE_CHECKED, EVIDENCE_OBSERVED))

    def test_repeated_observations_accumulate_rather_than_overwrite(self) -> None:
        merged = _merge([self.checked(), self.observed(), self.observed("main/second")])
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0].detail["observations"]), 2)

    def test_the_first_entry_wins_for_identity_fields(self) -> None:
        merged = _merge([self.checked(), self.observed()])
        self.assertEqual(merged[0].kind, "workflow")
        self.assertEqual(merged[0].label, "main")

    def test_distinct_ids_are_left_alone(self) -> None:
        merged = _merge(
            [
                self.checked(),
                AudienceEntry(
                    "purpose:1",
                    "purpose",
                    "A note.",
                    (EVIDENCE_HUMAN,),
                    "human:purpose-note",
                    {"independently_verified": False},
                ),
            ]
        )
        self.assertEqual(len(merged), 2)


class VerifyTests(unittest.TestCase):
    def base(self) -> AudienceMap:
        return build_audience_map(
            HELLO, audience="overview", purpose=["A greeting program."]
        )

    def test_a_healthy_map_verifies(self) -> None:
        report = verify(self.base())
        self.assertTrue(report["ok"])
        self.assertEqual(report["problems"], [])
        self.assertEqual(report["audience"], "overview")

    def test_duplicate_entry_ids_are_reported(self) -> None:
        result = self.base()
        broken = replace(result, entries=(*result.entries, result.entries[0]))
        self.assertIn("duplicate entry ids", verify(broken)["problems"])

    def test_entry_without_evidence_is_reported(self) -> None:
        result = self.base()
        entry = result.entries[0]
        broken = replace(
            result, entries=(replace(entry, evidence=()), *result.entries[1:])
        )
        self.assertIn(
            f"entry {entry.id} has invalid evidence", verify(broken)["problems"]
        )

    def test_unknown_evidence_class_is_reported(self) -> None:
        result = self.base()
        entry = result.entries[0]
        broken = replace(
            result, entries=(replace(entry, evidence=("guessed",)), *result.entries[1:])
        )
        self.assertIn(
            f"entry {entry.id} has invalid evidence", verify(broken)["problems"]
        )

    def test_entry_without_a_source_link_is_reported(self) -> None:
        result = self.base()
        entry = result.entries[0]
        broken = replace(
            result, entries=(replace(entry, source=""), *result.entries[1:])
        )
        self.assertIn(
            f"entry {entry.id} has no source link", verify(broken)["problems"]
        )

    def test_mislabelled_human_evidence_is_reported(self) -> None:
        result = self.base()
        note = next(iter(result.entries_with(EVIDENCE_HUMAN)))
        broken_entries = [
            replace(note, detail={**note.detail, "independently_verified": True})
            if entry.id == note.id
            else entry
            for entry in result.entries
        ]
        broken = replace(result, entries=tuple(broken_entries))
        self.assertIn(
            f"entry {note.id} mislabels human evidence", verify(broken)["problems"]
        )

    def test_edge_referencing_an_omitted_entry_is_reported(self) -> None:
        result = self.base()
        edge = AudienceEdge("module:ghost", "module:phantom", "calls", EVIDENCE_CHECKED)
        broken = replace(result, edges=(*result.edges, edge))
        self.assertIn(
            "edge module:ghost->module:phantom references omitted entry",
            verify(broken)["problems"],
        )

    def test_edge_with_unknown_evidence_is_reported(self) -> None:
        result = self.base()
        entry = result.entries[0]
        edge = AudienceEdge(entry.id, entry.id, "self", "assumed")
        broken = replace(result, edges=(*result.edges, edge))
        self.assertIn(
            f"edge {entry.id}->{entry.id} has invalid evidence",
            verify(broken)["problems"],
        )

    def test_observed_entries_despite_a_rejected_trace_are_reported(self) -> None:
        result = self.base()
        forged = AudienceEntry(
            id="run:forged",
            kind="run",
            label="run forged",
            evidence=(EVIDENCE_OBSERVED,),
            source="trace:elsewhere.jsonl",
        )
        broken = replace(
            result,
            entries=(*result.entries, forged),
            evidence_limits=("trace-program-hash-mismatch",),
        )
        self.assertIn(
            "unmatched trace contributed observed entries",
            verify(broken)["problems"],
        )

    def test_tampered_digest_is_reported(self) -> None:
        result = self.base()
        broken = replace(result, digest="0" * 64)
        self.assertIn("map digest does not match contents", verify(broken)["problems"])


class RenderTests(unittest.TestCase):
    def test_text_and_mermaid_carry_evidence_labels(self) -> None:
        result = build_audience_map(
            HELLO, audience="overview", purpose=["A greeting program."]
        )
        text = to_text(result)
        self.assertIn("Audience: overview", text)
        self.assertIn(EVIDENCE_CHECKED, text)
        self.assertIn(EVIDENCE_HUMAN, text)
        self.assertIn("not independently verified", text)
        self.assertIn(result.notice, text)

        mermaid = to_mermaid(result)
        self.assertTrue(mermaid.startswith("flowchart TD"))
        self.assertIn(f"[{EVIDENCE_CHECKED}]", mermaid)
        self.assertIn(f"[{EVIDENCE_HUMAN}]", mermaid)

    def test_mermaid_escapes_characters_that_would_break_the_diagram(self) -> None:
        result = build_audience_map(
            HELLO,
            audience="overview",
            purpose=['A "quoted" | piped\nmulti-line note'],
        )
        mermaid = to_mermaid(result)
        body = [line for line in mermaid.splitlines() if "purpose" in line]
        self.assertTrue(body)
        for line in body:
            self.assertNotIn("\n", line)
            self.assertIn("&quot;", line)
            self.assertIn("&#124;", line)

    def test_map_serializes_to_the_versioned_schema(self) -> None:
        payload = build_audience_map(HELLO, audience="reviewer").to_dict()
        self.assertEqual(payload["schema"], AUDIENCE_MAP_SCHEMA)
        self.assertEqual(
            set(payload),
            {
                "schema",
                "audience",
                "question",
                "project",
                "entries",
                "edges",
                "coverage",
                "evidence_counts",
                "evidence_limits",
                "digest",
                "notice",
            },
        )
        self.assertEqual(json.loads(json.dumps(payload)), payload)


class CommandLineTests(unittest.TestCase):
    def call(self, *argv: str) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with (
            patch.object(sys, "argv", ["kosika", *argv]),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            try:
                code = cli.main()
            except SystemExit as exit_request:
                code = int(exit_request.code or 0)
        return code, out.getvalue() + err.getvalue()

    def test_audience_map_is_registered_and_discoverable(self) -> None:
        code, output = self.call("--help")
        self.assertEqual(code, 0)
        self.assertIn("audience-map", output)

    def test_json_output_round_trips_into_a_verifiable_map(self) -> None:
        code, output = self.call(
            "audience-map", str(HELLO), "--audience", "reviewer", "--format", "json"
        )
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["schema"], AUDIENCE_MAP_SCHEMA)
        self.assertEqual(payload["audience"], "reviewer")
        rebuilt = AudienceMap(
            payload["audience"],
            payload["question"],
            payload["project"],
            tuple(AudienceEntry(**item) for item in payload["entries"]),
            tuple(AudienceEdge(**item) for item in payload["edges"]),
            payload["coverage"],
            payload["evidence_counts"],
            tuple(payload["evidence_limits"]),
            payload["digest"],
            payload["notice"],
        )
        self.assertTrue(verify(rebuilt)["ok"])

    def test_text_output(self) -> None:
        code, output = self.call(
            "audience-map", str(HELLO), "--audience", "overview", "--format", "text"
        )
        self.assertEqual(code, 0)
        self.assertIn("Audience: overview", output)

    def test_mermaid_output(self) -> None:
        code, output = self.call(
            "audience-map", str(HELLO), "--audience", "operator", "--format", "mermaid"
        )
        self.assertEqual(code, 0)
        self.assertTrue(output.startswith("flowchart TD"))

    def test_output_file_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "nested" / "map.json"
            code, output = self.call(
                "audience-map",
                str(HELLO),
                "--audience",
                "overview",
                "--format",
                "json",
                "--output",
                str(target),
            )
            self.assertEqual(code, 0)
            self.assertIn("Audience map written", output)
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8"))["audience"], "overview"
            )

    def test_purpose_notes_reach_the_map(self) -> None:
        code, output = self.call(
            "audience-map",
            str(HELLO),
            "--audience",
            "overview",
            "--format",
            "json",
            "--purpose",
            "Greets the caller.",
        )
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["evidence_counts"][EVIDENCE_HUMAN], 1)

    def test_component_contract_is_accepted_through_the_cli(self) -> None:
        code, output = self.call(
            "audience-map",
            str(COMPONENT_SOURCE),
            "--audience",
            "reviewer",
            "--format",
            "json",
            "--component-contract",
            str(COMPONENT_CONTRACT),
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["evidence_counts"][EVIDENCE_DECLARED], 1)

    def test_integrity_failure_blocks_output(self) -> None:
        with patch(
            "kosika.audience_maps.verify",
            return_value={"ok": False, "problems": ["forged digest"]},
        ):
            code, output = self.call(
                "audience-map", str(HELLO), "--audience", "overview"
            )
        self.assertEqual(code, 2)
        self.assertIn("integrity check failed", output)
        self.assertIn("forged digest", output)

    def test_unknown_audience_is_rejected_by_the_parser(self) -> None:
        code, output = self.call(
            "audience-map", str(HELLO), "--audience", "nobody", "--format", "json"
        )
        self.assertEqual(code, 2)
        self.assertIn("audience-map", output)

    def test_unreadable_source_fails_closed(self) -> None:
        code, output = self.call(
            "audience-map", str(EXAMPLES / "absent.kos"), "--audience", "overview"
        )
        self.assertEqual(code, 2)
        self.assertIn("failed safely", output)


if __name__ == "__main__":
    unittest.main()
