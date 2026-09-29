import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kosika import cli
from kosika.design import (
    DesignError,
    build_design_from_program,
    load_design_manifest,
    render_design,
    validate_design,
)
from kosika.modules import load_program
from kosika.network import load_network_manifest
from kosika.parser import parse

ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "examples" / "test_host_live.kos"
NETWORK = ROOT / "network" / "example-network.json"


class DesignManifestTests(unittest.TestCase):
    def test_program_and_network_generate_deterministic_vector_design(self) -> None:
        program = parse(SOURCE.read_text(encoding="utf-8"))
        network = load_network_manifest(NETWORK)
        design = build_design_from_program(
            program,
            "test-host.design",
            "Test Host Agent Workflow",
            network=network,
        )
        self.assertGreaterEqual(len(design.nodes), 10)
        self.assertIn("workflow.main", {node["id"] for node in design.nodes})
        self.assertIn("agent.monitor", {node["id"] for node in design.nodes})
        self.assertIn("network.kosika.example", {node["id"] for node in design.nodes})
        self.assertIn(
            "network_context",
            {edge["kind"] for edge in design.edges},
        )
        self.assertEqual(
            design.digest,
            build_design_from_program(
                program,
                "test-host.design",
                "Test Host Agent Workflow",
                network=network,
            ).digest,
        )

        svg = render_design(design, "svg")
        self.assertIn("<svg", svg)
        self.assertIn('marker-end="url(#arrow)"', svg)
        self.assertIn('stroke-dasharray="7 5"', svg)
        self.assertIn('<linearGradient id="background"', svg)
        self.assertIn('filter="url(#shadow)"', svg)
        self.assertIn("Test Host Agent Workflow", svg)
        self.assertIn('id="workflow.main"', svg)
        self.assertIn('<path d="M', svg)

        mindmap = render_design(design, "svg", "mindmap")
        self.assertIn("STRUCTURE MINDMAP", mindmap)
        self.assertIn('<path d="M', mindmap)

        mermaid = render_design(design, "mermaid")
        self.assertIn("flowchart LR", mermaid)
        self.assertNotIn("requires_approval", mermaid)
        self.assertIn("network_context", mermaid)

    def test_design_maps_model_agent_and_nested_workflow_calls(self) -> None:
        source = """
        record Question { text: Text }
        record Answer { text: Text }
        model writer { input: Question output: Answer }
        agent researcher {
          model: writer
          can: []
          cannot: []
        }
        workflow child(question: Question) -> Answer {
          step respond {
            let result: Answer = use_model writer(text: question.text)
            return result
          }
        }
        workflow delegate(question: Question) -> Answer {
          step ask {
            let result: Answer = ask researcher(text: question.text)
            return result
          }
        }
        workflow main(question: Question) -> Answer {
          step nested {
            let result: Answer = call child(question: question)
            return result
          }
        }
        """
        design = build_design_from_program(
            parse(source), "calls.design", "Call Relationships"
        )
        node_ids = {str(node["id"]) for node in design.nodes}
        self.assertTrue(
            {
                "model.writer",
                "agent.researcher",
                "workflow.child",
                "workflow.delegate",
                "workflow.main",
            }.issubset(node_ids)
        )
        relationships = {
            (str(edge["source"]), str(edge["target"]), str(edge["kind"]))
            for edge in design.edges
        }
        self.assertIn(
            ("stage.child.respond", "model.writer", "calls"), relationships
        )
        self.assertIn(
            ("stage.delegate.ask", "agent.researcher", "calls"), relationships
        )
        self.assertIn(
            ("stage.main.nested", "workflow.child", "calls"), relationships
        )
        self.assertIn(
            ("record.Question", "model.writer", "inputs"), relationships
        )
        self.assertIn(
            ("model.writer", "record.Answer", "outputs"), relationships
        )
        self.assertIn(
            ("agent.researcher", "model.writer", "uses"), relationships
        )

    def test_design_includes_imported_module_declarations(self) -> None:
        root = ROOT / "examples" / "composed"
        program = load_program(root / "main.kos")
        design = build_design_from_program(
            program, "composed.design", "Composed Workflow"
        )
        self.assertEqual(
            set(program.module_hashes), {"main.kos", "research.kos", "types.kos"}
        )
        node_ids = {str(node["id"]) for node in design.nodes}
        self.assertTrue(
            {
                "module.main.kos",
                "module.research.kos",
                "module.types.kos",
                "record.Question",
                "record.Answer",
                "model.reader",
                "workflow.research",
                "workflow.main",
                "stage.research.answer",
                "stage.main.research",
            }.issubset(node_ids)
        )
        relationships = {
            (str(edge["source"]), str(edge["target"]), str(edge["kind"]))
            for edge in design.edges
        }
        self.assertIn(
            ("stage.research.answer", "model.reader", "calls"), relationships
        )
        self.assertIn(
            ("stage.main.research", "workflow.research", "calls"), relationships
        )
        self.assertIn(
            ("module.main.kos", "module.research.kos", "imports"), relationships
        )
        self.assertIn(
            ("module.research.kos", "module.types.kos", "imports"), relationships
        )
        self.assertIn(
            ("module.types.kos", "record.Question", "contains"), relationships
        )
        self.assertIn(
            ("module.research.kos", "model.reader", "contains"), relationships
        )
        self.assertIn(
            ("module.main.kos", "workflow.main", "contains"), relationships
        )
        self.assertEqual(
            design.digest,
            build_design_from_program(
                program, "composed.design", "Composed Workflow"
            ).digest,
        )

    def test_design_rejects_unknown_edges_and_invalid_geometry(self) -> None:
        base = {
            "design": {"id": "test.design", "name": "Test"},
            "nodes": [
                {
                    "id": "one",
                    "label": "One",
                    "kind": "note",
                    "position": {"x": 0, "y": 0},
                }
            ],
            "edges": [],
            "views": [],
        }
        unknown_edge = dict(base)
        unknown_edge["edges"] = [{"id": "bad", "source": "one", "target": "missing"}]
        with self.assertRaises(DesignError):
            validate_design(unknown_edge)

        invalid_geometry = dict(base)
        invalid_geometry["nodes"] = [
            {
                "id": "one",
                "label": "One",
                "kind": "note",
                "position": {"x": 0, "y": 0},
                "size": {"width": 0, "height": 10},
            }
        ]
        with self.assertRaises(DesignError):
            validate_design(invalid_geometry)

    def test_design_round_trip_preserves_rendered_structure(self) -> None:
        program = parse(SOURCE.read_text(encoding="utf-8"))
        design = build_design_from_program(program, "roundtrip.design", "Roundtrip")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "design.json"
            path.write_text(
                json.dumps(
                    {
                        "manifestVersion": design.manifest_version,
                        "designVersion": 1,
                        "design": design.design,
                        "nodes": list(design.nodes),
                        "edges": list(design.edges),
                        "views": list(design.views),
                    }
                ),
                encoding="utf-8",
            )
            loaded = load_design_manifest(path)
            self.assertEqual(design.digest, loaded.digest)
            self.assertEqual(
                render_design(design, "text"), render_design(loaded, "text")
            )


class DesignCliTests(unittest.TestCase):
    def call_cli(self, *arguments: str) -> tuple[int, str]:
        output = io.StringIO()
        with (
            patch.object(sys, "argv", ["kosika", *arguments]),
            contextlib.redirect_stdout(output),
        ):
            result = cli.main()
        return result, output.getvalue()

    def test_design_generate_validate_and_svg_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "design.json"
            svg_path = root / "design.svg"
            result, output = self.call_cli(
                "design",
                "generate",
                str(SOURCE),
                "--id",
                "cli.design",
                "--name",
                "CLI Design",
                "--network",
                str(NETWORK),
                "--output",
                str(manifest_path),
            )
            self.assertEqual(result, 0)
            self.assertIn("Generated design manifest", output)
            self.assertTrue(manifest_path.exists())

            result, output = self.call_cli("design", "validate", str(manifest_path))
            self.assertEqual(result, 0)
            self.assertIn("Valid design manifest", output)

            result, output = self.call_cli(
                "design",
                "show",
                str(manifest_path),
                "--format",
                "svg",
                "--output",
                str(svg_path),
            )
            self.assertEqual(result, 0)
            self.assertIn("Rendered svg design", output)
            self.assertTrue(svg_path.read_text(encoding="utf-8").startswith("<svg"))

            mindmap_path = root / "mindmap.svg"
            result, output = self.call_cli(
                "design",
                "show",
                str(manifest_path),
                "--format",
                "svg",
                "--view",
                "mindmap",
                "--output",
                str(mindmap_path),
            )
            self.assertEqual(result, 0)
            self.assertIn("Rendered svg design", output)
            self.assertIn(
                "STRUCTURE MINDMAP",
                mindmap_path.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
