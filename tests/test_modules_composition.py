import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kosika.checker import check
from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.modules import load_program
from kosika.parser import parse
from kosika.runtime import replay, run


class ModuleCompositionTests(unittest.TestCase):
    def test_composed_example_and_offline_replay(self):
        root = Path(__file__).parents[1] / "examples/composed"
        program = load_program(root / "main.syn")
        self.assertEqual(len(program.module_hashes), 3)
        provider = ScriptedModelProvider([ModelResponse(content='{"text":"done"}')])
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            result = run(
                program,
                "main",
                trace,
                inputs={"question": {"text": "hello"}},
                model_bindings={"reader": provider},
            )
            self.assertEqual(result, {"text": "done"})
            with patch.object(
                provider, "complete", side_effect=AssertionError("live call")
            ):
                self.assertEqual(replay(trace), result)
            events = [json.loads(line) for line in trace.read_text().splitlines()]
            events[3]["events"][0]["inputs"]["question"]["text"] = "tampered"
            trace.write_text("\n".join(json.dumps(event) for event in events))
            with self.assertRaises(ValueError):
                replay(trace)

    def test_import_privacy_cycles_and_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "types.syn").write_text("record R { text: Text }")
            entry = root / "main.syn"
            entry.write_text('import "types.syn" model m { input: R output: R }')
            with self.assertRaisesRegex(ValueError, "private"):
                load_program(entry)
            entry.write_text('import "main.syn"')
            with self.assertRaisesRegex(ValueError, "cycle"):
                load_program(entry)
            (root / "sub").mkdir()
            (root / "sub/main.syn").write_text('import "../types.syn"')
            with self.assertRaisesRegex(ValueError, "outside"):
                load_program(root / "sub/main.syn")

    def test_call_cycles_types_and_unresolved_imports(self):
        source = """record R { text: Text }
        workflow main(q: R) -> R {
          step x { let r: R = call main(q: q) return r }
        }"""
        with self.assertRaisesRegex(ValueError, "Recursive"):
            check(parse(source))
        with self.assertRaisesRegex(ValueError, "Unresolved imports"):
            check(parse('import "x.syn"'))
        source = """record R { text: Text }
        model m { input: R output: R }
        workflow child(q: R) -> R {
          step x { let r: R = use_model m(text: q.text) return r }
        }
        workflow main() -> R {
          step x { let r: R = call child(q: 7) return r }
        }"""
        with self.assertRaises(ValueError):
            check(parse(source))

    def test_nested_missing_binding_fails_before_any_model_call(self):
        root = Path(__file__).parents[1] / "examples/composed"
        with self.assertRaises(PermissionError):
            run(
                load_program(root / "main.syn"),
                "main",
                None,
                inputs={"question": {"text": "x"}},
            )
