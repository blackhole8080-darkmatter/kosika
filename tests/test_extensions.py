import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kosika.extensions import (
    ExtensionManifestError,
    extension_manifest_digest,
    extension_manifest_to_dict,
    load_extension_manifest,
    parse_extension_manifest,
)

ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "examples" / "extension_manifest.json"


class ExtensionManifestTests(unittest.TestCase):
    def test_example_manifest_round_trips_and_has_stable_digest(self) -> None:
        manifest = load_extension_manifest(EXAMPLE)
        self.assertEqual(manifest.id, "kosika.example.cognitive-design")
        self.assertEqual(
            [contribution.kind for contribution in manifest.contributions],
            ["cognitive", "design"],
        )
        self.assertEqual(
            extension_manifest_digest(manifest),
            extension_manifest_digest(
                parse_extension_manifest(extension_manifest_to_dict(manifest))
            ),
        )

    def test_contribution_permissions_must_be_declared_at_manifest_level(self) -> None:
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        data["contributions"][0]["permissions"] = ["network:read"]
        with self.assertRaisesRegex(ExtensionManifestError, "undeclared permissions"):
            parse_extension_manifest(data)

    def test_rejects_duplicate_contributions_and_self_dependency(self) -> None:
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        data["contributions"].append(copy.deepcopy(data["contributions"][0]))
        with self.assertRaisesRegex(ExtensionManifestError, "duplicate extension contribution"):
            parse_extension_manifest(data)

        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        data["dependencies"] = [
            {"id": data["id"], "constraint": ">=0.1.0", "optional": False}
        ]
        with self.assertRaisesRegex(ExtensionManifestError, "cannot depend on itself"):
            parse_extension_manifest(data)

    def test_rollback_requires_hook_for_state_restoration(self) -> None:
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        data["rollback"] = {"strategy": "restore"}
        with self.assertRaisesRegex(ExtensionManifestError, "requires rollback.entrypoint"):
            parse_extension_manifest(data)

    def test_health_check_timeout_is_bounded_positive_integer(self) -> None:
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        data["healthChecks"][0]["timeoutMs"] = 0
        with self.assertRaisesRegex(ExtensionManifestError, "positive integer"):
            parse_extension_manifest(data)

    def test_cli_checks_valid_manifest_as_json(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "kosika.cli",
                "extension",
                "check",
                str(EXAMPLE),
                "--json",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["valid"])
        self.assertEqual(payload["id"], "kosika.example.cognitive-design")
        self.assertEqual(len(payload["contributions"]), 2)
        self.assertRegex(payload["digest"], r"^[0-9a-f]{64}$")

    def test_cli_rejects_invalid_manifest(self) -> None:
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        data["permissions"] = ["not-a-permission"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "kosika.cli",
                    "extension",
                    "check",
                    str(path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Extension check failed", result.stderr)


if __name__ == "__main__":
    unittest.main()
