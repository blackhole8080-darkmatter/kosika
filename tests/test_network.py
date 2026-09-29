import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kosika import cli
from kosika.network import (
    NetworkDirectory,
    NetworkError,
    load_network_manifest,
    new_network_manifest,
    render_network,
    validate_network,
    write_network_manifest,
)

ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "network" / "example-network.json"


class NetworkManifestTests(unittest.TestCase):
    def test_example_manifest_validates_and_renders_deterministically(self) -> None:
        manifest = load_network_manifest(EXAMPLE)
        self.assertEqual(manifest.network_id, "kosika.example")
        self.assertEqual(len(manifest.modules), 4)
        self.assertEqual(len(manifest.nodes), 5)
        self.assertEqual(len(manifest.tabs), 3)
        self.assertEqual(len(manifest.memory_graphs), 1)
        self.assertEqual(manifest.digest, load_network_manifest(EXAMPLE).digest)

        text = render_network(manifest, "text")
        self.assertIn("Model gateway", text)
        self.assertIn("Project Memory Graph", text)
        self.assertIn("approval-required", text)
        mermaid = render_network(manifest, "mermaid")
        self.assertIn("flowchart LR", mermaid)
        self.assertIn("model_request", mermaid)

    def test_network_rejects_unknown_references_and_unapproved_execute(self) -> None:
        base = {
            "network": {"id": "test", "name": "Test"},
            "developers": [],
            "modules": [],
            "nodes": [
                {"id": "source", "name": "Source", "kind": "kosika"},
                {"id": "target", "name": "Target", "kind": "service"},
            ],
            "tabs": [],
            "links": [],
            "memoryGraphs": [],
        }
        unknown_link = dict(base)
        unknown_link["links"] = [{"id": "bad", "source": "source", "target": "missing"}]
        with self.assertRaises(NetworkError):
            validate_network(unknown_link)

        unapproved = dict(base)
        unapproved["links"] = [
            {
                "id": "execute",
                "source": "source",
                "target": "target",
                "access": "execute",
            }
        ]
        with self.assertRaises(NetworkError):
            validate_network(unapproved)

    def test_network_rejects_credentials_and_invalid_memory_edges(self) -> None:
        with self.assertRaises(NetworkError):
            validate_network(
                {
                    "network": {"id": "secret", "name": "Secret"},
                    "metadata": {"apiKey": "must-not-be-here"},
                }
            )
        with self.assertRaises(NetworkError):
            validate_network(
                {
                    "network": {"id": "memory", "name": "Memory"},
                    "memoryGraphs": [
                        {
                            "id": "graph",
                            "nodes": [{"id": "known"}],
                            "edges": [
                                {
                                    "id": "edge",
                                    "source": "known",
                                    "target": "missing",
                                }
                            ],
                        }
                    ],
                }
            )

    def test_directory_index_is_sorted_and_rejects_duplicate_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = new_network_manifest("network.a", "A")
            second = new_network_manifest("network.b", "B")
            write_network_manifest(first, root / "b.json")
            write_network_manifest(second, root / "a.json")
            index = NetworkDirectory.load(root)
            self.assertEqual(
                [entry.network_id for entry in index.entries],
                [
                    "network.b",
                    "network.a",
                ],
            )
            self.assertEqual(index.to_dict()["summary"]["networkCount"], 2)

            duplicate = root / "nested"
            duplicate.mkdir()
            write_network_manifest(first, duplicate / "duplicate.json")
            with self.assertRaises(NetworkError):
                NetworkDirectory.load(root, recursive=True)


class NetworkCliTests(unittest.TestCase):
    def call_cli(self, *arguments: str) -> tuple[int, str]:
        output = io.StringIO()
        with (
            patch.object(sys, "argv", ["kosika", *arguments]),
            contextlib.redirect_stdout(output),
        ):
            result = cli.main()
        return result, output.getvalue()

    def test_network_init_validate_show_and_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "network.json"
            result, output = self.call_cli(
                "network",
                "init",
                str(path),
                "--id",
                "cli.network",
                "--name",
                "CLI Network",
            )
            self.assertEqual(result, 0)
            self.assertIn("Initialized network manifest", output)
            self.assertTrue(path.exists())

            result, output = self.call_cli("network", "validate", str(path))
            self.assertEqual(result, 0)
            self.assertIn("Valid network manifest", output)

            result, output = self.call_cli(
                "network", "show", str(EXAMPLE), "--format", "json"
            )
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output)["network"]["id"], "kosika.example")

            result, output = self.call_cli(
                "network", "index", str(ROOT / "network"), "--format", "json"
            )
            self.assertEqual(result, 0)
            indexed = json.loads(output)
            self.assertEqual(indexed["summary"]["networkCount"], 1)


if __name__ == "__main__":
    unittest.main()
