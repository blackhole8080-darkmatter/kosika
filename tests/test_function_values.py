import json
import tempfile
import unittest
from pathlib import Path

from kosika.checker import CheckError, check
from kosika.design import build_design_from_program
from kosika.mindmap import build_mindmap
from kosika.modules import load_program
from kosika.parser import parse
from kosika.runtime import replay, run
from kosika.schema import SchemaValidationError

SOURCE = """
record Result { value: Number }
fn make(base: Number) -> fn(value: Number) -> Number {
  fn add(value: Number) -> Number { return base + value }
  return ref add
}
fn apply(operation: fn(value: Number) -> Number, value: Number) -> Result {
  return {value: operation(value: value)}
}
workflow main() -> Result {
  step create { let operation: fn(value: Number) -> Number = make(base: 10) return operation }
  step use { let result = apply(operation: operation, value: 5) return result }
}
"""


class FunctionValueTests(unittest.TestCase):
    def test_function_returning_array_is_not_itself_an_array(self) -> None:
        for source in (
            "fn values() -> Number[] { return [1] } "
            "fn wrong() -> fn() -> Number[] { return [ref values] }",
            "fn wrong(operation: fn() -> Number[]) -> Number { return operation[0] }",
        ):
            with self.subTest(source=source), self.assertRaises(CheckError):
                check(parse(source))

    def test_later_local_does_not_hide_recursive_initializer(self) -> None:
        with self.assertRaisesRegex(CheckError, "Recursive"):
            check(
                parse(
                    "fn again() -> Number { let again: Number = again() return again }"
                )
            )

    def test_escaping_closure_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            self.assertEqual(run(parse(SOURCE), "main", trace), {"value": 15})
            self.assertEqual(replay(trace), {"value": 15})

    def test_function_binding_can_be_invoked_as_workflow_step(self) -> None:
        source = SOURCE.replace(
            "step use { let result = apply(operation: operation, value: 5) return result }",
            "step invoke { let number = operation(value: 7) return number } "
            "step use { let result = apply(operation: operation, value: number) return result }",
        )
        self.assertEqual(run(parse(source), "main", None), {"value": 27})
        program = parse(source)
        graph = build_mindmap(program)
        node_ids = {node["id"] for node in graph["nodes"]}
        self.assertTrue(all(edge["target"] in node_ids for edge in graph["edges"]))
        build_design_from_program(program, design_id="closures", name="Closures")

    def test_closures_keep_independent_captures_including_callbacks(self) -> None:
        source = SOURCE.replace(
            "workflow main()",
            """
        fn compose(operation: fn(value: Number) -> Number) -> fn(value: Number) -> Number {
          fn invoke(value: Number) -> Number { return operation(value: value) }
          return ref invoke
        }
        workflow main()""",
        ).replace(
            "step use { let result = apply(operation: operation, value: 5) return result }",
            "step other { let second = make(base: 100) return second } "
            "step compose_it { let composed = compose(operation: operation) return composed } "
            "step use { let result = apply(operation: composed, value: second(value: 5)) return result }",
        )
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            self.assertEqual(run(parse(source), "main", trace), {"value": 115})
            self.assertEqual(replay(trace), {"value": 115})

    def test_replay_rejects_closure_signature_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            run(parse(SOURCE), "main", trace)
            events = [json.loads(line) for line in trace.read_text().splitlines()]
            for event in events:
                if isinstance(event.get("result"), dict) and "$type" in event["result"]:
                    event["result"]["$type"] = "fn(value: Bool)->Number"
            trace.write_text(
                "\n".join(json.dumps(event) for event in events), encoding="utf-8"
            )
            with self.assertRaises(SchemaValidationError):
                replay(trace)

    def test_top_level_references_and_function_locals(self) -> None:
        source = SOURCE.replace(
            "return {value: operation(value: value)}",
            "let saved: fn(value: Number) -> Number = operation "
            "return {value: saved(value: value)}",
        )
        self.assertEqual(run(parse(source), "main", None), {"value": 15})
        source = source.replace(
            "fn make(base:",
            "fn double(value: Number) -> Number { return value * 2 } fn make(base:",
        )
        source = source.replace(
            "apply(operation: operation, value: 5)",
            "apply(operation: ref double, value: 5)",
        )
        self.assertEqual(run(parse(source), "main", None), {"value": 10})

    def test_signature_mismatches_and_external_closures_are_rejected(self) -> None:
        for source in (
            SOURCE.replace("return ref add", "return 7"),
            SOURCE.replace("operation(value: value)", "operation(wrong: value)"),
            SOURCE.replace(
                "fn add(value: Number) -> Number", "fn add(value: Bool) -> Number"
            ),
            "record Unsafe { callback: fn(value: Number) -> Number }",
            SOURCE.replace("return ref add", "return ref missing"),
        ):
            with self.subTest(source=source), self.assertRaises(CheckError):
                check(parse(source))

    def test_module_visibility_for_function_references(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "lib.kos").write_text(
                "fn hidden(value: Number) -> Number { return value }", encoding="utf-8"
            )
            entry = root / "main.kos"
            entry.write_text(
                'import "lib.kos" '
                "fn leak(hidden: Number) -> fn(value: Number) -> Number { return ref hidden }",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CheckError, "private"):
                load_program(entry)
            (root / "lib.kos").write_text(
                "export [make, apply, Result]\n" + SOURCE.split("workflow main")[0],
                encoding="utf-8",
            )
            entry.write_text(
                'import "lib.kos"\nworkflow main' + SOURCE.split("workflow main")[1],
                encoding="utf-8",
            )
            self.assertEqual(run(load_program(entry), "main", None), {"value": 15})

    def test_expanded_closures_example_runs_and_replays(self) -> None:
        from kosika.modules import load_program

        source_path = Path(__file__).resolve().parent.parent / "examples" / "closures.kos"
        program = load_program(source_path)
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "closures.jsonl"
            self.assertEqual(run(program, "main", trace), {"value": 115})
            self.assertEqual(replay(trace), {"value": 115})
            mindmap = build_mindmap(program)
            node_labels = {
                node["label"] for node in mindmap["nodes"] if node["kind"] == "function"
            }
            self.assertIn("compose", node_labels)
            self.assertIn("make_adder", node_labels)
            self.assertIn("double", node_labels)
            self.assertIn("apply", node_labels)
