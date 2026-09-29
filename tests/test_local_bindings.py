import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kosika.checker import CheckError, check
from kosika.mindmap import build_mindmap
from kosika.modules import load_program
from kosika.parser import ParseError, parse
from kosika.runtime import replay, run

SOURCE = '''
record Result { value: Number accepted: Bool }
fn double(value: Number) -> Number { return value * 2 }
fn score(value: Number) -> Result {
  let weighted: Number = double(value: value)
  let adjusted: Number = weighted + 1
  let result: Result = {value: adjusted, accepted: adjusted >= 5}
  return result
}
workflow main() -> Result {
  step calculate { let result: Result = score(value: 2) return result }
}
'''


class LocalBindingTests(unittest.TestCase):
    def test_ordered_bindings_execute_and_replay_without_evaluation(self):
        program = parse(SOURCE)
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / 'run.jsonl'
            result = run(program, 'main', trace)
            self.assertEqual(result, {'value': 5, 'accepted': True})
            events = [json.loads(line) for line in trace.read_text().splitlines()]
            self.assertEqual(events[2]['arguments'], {'value': 2})
            self.assertEqual(events[3]['events'][0]['function'], 'double')
            with patch('kosika.runtime._execute_function', side_effect=AssertionError):
                self.assertEqual(replay(trace), result)
        graph = build_mindmap(program)
        node = next(node for node in graph['nodes'] if node['id'] == 'function:score')
        self.assertEqual([item['name'] for item in node['attributes']['bindings']],
                         ['weighted', 'adjusted', 'result'])
        self.assertTrue(any(edge['source'] == 'function:score' and
                            edge['target'] == 'function:double' for edge in graph['edges']))

    def test_invalid_bindings_fail_before_execution(self):
        cases = [
            ('let adjusted: Number = weighted + 1', 'let weighted: Number = 1', 'cannot rebind'),
            ('let weighted: Number', 'let value: Number', 'cannot rebind'),
            ('double(value: value)', 'adjusted', 'unknown variable'),
            ('let weighted: Number', 'let weighted: Text', 'expects'),
            ('let weighted: Number', 'let weighted: Missing', 'Unknown field type'),
            ('let weighted: Number', 'let weighted: Result', 'expects'),
        ]
        for old, new, message in cases:
            with self.subTest(new=new):
                with self.assertRaisesRegex(CheckError, message):
                    check(parse(SOURCE.replace(old, new)))

    def test_reassignment_and_untyped_locals_are_rejected(self):
        for statement in ('weighted = 3', 'let other = 3'):
            with self.subTest(statement=statement), self.assertRaises(ParseError):
                parse(SOURCE.replace('let adjusted: Number = weighted + 1', statement))

    def test_initializer_calls_participate_in_cycle_detection(self):
        source = 'fn cycle(value: Number) -> Number { let x: Number = cycle(value: value) return x }'
        with self.assertRaisesRegex(CheckError, 'Recursive function'):
            check(parse(source))

    def test_locals_do_not_escape_to_other_functions(self):
        with self.assertRaisesRegex(CheckError, 'unknown variable'):
            check(parse(SOURCE + 'fn leak() -> Number { return adjusted }'))

    def test_private_initializer_calls_and_types_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'lib.kos').write_text(
                'record Secret { value: Number } fn hidden() -> Number { return 1 }')
            main = root / 'main.kos'
            for binding in ('let x: Number = hidden()', 'let x: Secret = {value: 1}'):
                main.write_text('import "lib.kos" fn main() -> Number { ' + binding + ' return 1 }')
                with self.subTest(binding=binding), self.assertRaisesRegex(CheckError, 'private'):
                    load_program(main)
