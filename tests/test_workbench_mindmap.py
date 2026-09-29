import copy
import unittest
from unittest.mock import patch

from kosika.workbench import dispatch
from kosika.workbench_mcp import DEMO_SAMPLE
from kosika.workbench_samples import SAMPLES


def payload(sample):
    return {key: copy.deepcopy(sample[key]) for key in ("files", "entry", "workflow")}


def nodes(root):
    yield root
    for child in root["children"]:
        yield from nodes(child)


class MindmapTests(unittest.TestCase):
    def test_all_examples_have_deterministic_nonexecuting_maps(self):
        with (
            patch(
                "kosika.workbench.run",
                side_effect=AssertionError("Analysis executed code"),
            ),
            patch(
                "kosika.workbench._providers",
                side_effect=AssertionError("Analysis created a provider"),
            ),
        ):
            for sample in [*SAMPLES, DEMO_SAMPLE]:
                with self.subTest(sample=sample["id"]):
                    first = dispatch("mindmap", payload(sample))["mindmap"]
                    self.assertEqual(
                        first, dispatch("mindmap", payload(sample))["mindmap"]
                    )
                    self.assertEqual(
                        first["node_count"], len(list(nodes(first["root"])))
                    )
                    self.assertIn("static explanation", first["notice"])

    def test_imported_workflow_and_step_locations_are_exact(self):
        data = payload(SAMPLES[0])
        data["files"]["research.kos"] = (
            "// workflow fake { step misleading { } }\n" + data["files"]["research.kos"]
        )
        result = dispatch("mindmap", data)["mindmap"]
        all_nodes = list(nodes(result["root"]))
        self.assertEqual(result["files"], ["main.kos", "research.kos", "types.kos"])
        step = next(item for item in all_nodes if item["title"] == "1. respond")
        self.assertEqual(step["location"]["file"], "research.kos")
        line = data["files"]["research.kos"].splitlines()[step["location"]["line"] - 1]
        self.assertIn("step respond", line)
        self.assertTrue(
            any("Call workflow research" in item["explanation"] for item in all_nodes)
        )
        self.assertFalse(any("fake" in item["title"] for item in all_nodes))

    def test_declared_capabilities_are_not_reported_as_grants(self):
        result = dispatch("mindmap", payload(DEMO_SAMPLE))["mindmap"]
        tool = next(
            item
            for item in nodes(result["root"])
            if item["title"] == "inspect_text" and "Access:" in item["explanation"]
        )
        self.assertIn("requires a host grant", tool["explanation"])
        agent = next(
            item for item in nodes(result["root"]) if item["title"] == "inspector"
        )
        self.assertIn("arbitrary_shell", agent["explanation"])

    def test_invalid_source_unknown_workflow_and_paths_fail(self):
        data = payload(SAMPLES[1])
        data["files"]["main.kos"] = "record Broken {"
        with self.assertRaises(ValueError):
            dispatch("mindmap", data)
        data = payload(SAMPLES[1])
        data["workflow"] = "missing"
        with self.assertRaisesRegex(ValueError, "Unknown workflow"):
            dispatch("mindmap", data)
        with self.assertRaises(ValueError):
            dispatch(
                "mindmap", {"files": {"../escape.kos": ""}, "entry": "../escape.kos"}
            )
        with self.assertRaises(ValueError):
            dispatch("mindmap", {**payload(SAMPLES[1]), "mode": "live"})

    def test_changed_source_changes_explanation_and_bounds_large_maps(self):
        data = payload(SAMPLES[1])
        before = dispatch("mindmap", data)
        data["files"]["main.kos"] = data["files"]["main.kos"].replace(
            "step answer", "step explain"
        )
        after = dispatch("mindmap", data)
        self.assertNotEqual(before, after)
        self.assertTrue(
            any(
                item["title"] == "1. explain"
                for item in nodes(after["mindmap"]["root"])
            )
        )
        data["files"]["main.kos"] += "\n" + "\n".join(
            f"record R{i} {{ text: Text }}" for i in range(401)
        )
        with self.assertRaisesRegex(ValueError, "400 nodes"):
            dispatch("mindmap", data)


if __name__ == "__main__":
    unittest.main()
