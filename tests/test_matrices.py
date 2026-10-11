"""Vectors, matrices, relations and graphs: the arithmetic, the two engines
giving the same answers, and the methods as written in Kosika."""

from __future__ import annotations

import random
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from kosika import matrices
from kosika.checker import CheckError
from kosika.matrices import MatrixError
from kosika.modules import load_program
from kosika.testing import run_tests

A = [[1, 2], [3, 4]]
B = [[5, 6], [7, 8]]


class VectorTests(unittest.TestCase):
    def test_the_usual_operations(self) -> None:
        self.assertEqual(matrices.vector_plus([1, 2, 3], [4, 5, 6]), [5, 7, 9])
        self.assertEqual(matrices.vector_minus([4, 5], [1, 7]), [3, -2])
        self.assertEqual(matrices.vector_scaled([1, -2], 3), [3, -6])
        self.assertEqual(matrices.dot([1, 2, 3], [4, 5, 6]), 32)
        self.assertEqual(matrices.norm([3, 4]), 5)
        self.assertEqual(matrices.cross([1, 0, 0], [0, 1, 0]), [0, 0, 1])
        self.assertEqual(matrices.mean([1, 2, 3, 4]), 2.5)

    def test_lengths_must_agree_and_the_message_says_what_they_were(self) -> None:
        with self.assertRaisesRegex(MatrixError, "these have 2 and 3 numbers"):
            matrices.dot([1, 2], [1, 2, 3])
        with self.assertRaisesRegex(MatrixError, "vectors of 3 numbers"):
            matrices.cross([1, 2], [3, 4])

    def test_decimals_are_rounded_like_the_other_numeric_methods(self) -> None:
        self.assertEqual(matrices.vector_plus([0.1], [0.2]), [0.3])
        self.assertEqual(matrices.dot([0.1, 0.2], [3, 4]), 1.1)


class MatrixTests(unittest.TestCase):
    def test_the_product_is_the_textbook_one(self) -> None:
        self.assertEqual(matrices.times(A, B), [[19, 22], [43, 50]])
        self.assertEqual(matrices.times([[1, 2, 3]], [[1], [2], [3]]), [[14]])

    def test_shapes_that_do_not_fit_are_explained(self) -> None:
        with self.assertRaisesRegex(
            MatrixError, "a 2 by 2 matrix cannot be multiplied by a 3 by 1 one"
        ):
            matrices.times(A, [[1], [2], [3]])
        with self.assertRaisesRegex(MatrixError, "row 2 has 1"):
            matrices.transpose([[1, 2], [3]])
        with self.assertRaisesRegex(MatrixError, "these are 2 by 2 and 1 by 2"):
            matrices.plus(A, [[1, 2]])
        with self.assertRaisesRegex(MatrixError, "needs a square matrix"):
            matrices.determinant([[1, 2, 3], [4, 5, 6]])

    def test_whole_numbers_stay_exact_however_large(self) -> None:
        big = [[10**20, 1], [1, 10**20]]
        self.assertEqual(matrices.times(big, big)[0][0], 10**40 + 1)
        self.assertEqual(matrices.power([[2]], 200), [[2**200]])
        self.assertEqual(matrices.determinant([[10**15, 1], [1, 10**15]]), 10**30 - 1)

    def test_the_rest_of_the_basics(self) -> None:
        self.assertEqual(
            matrices.transpose([[1, 2, 3], [4, 5, 6]]), [[1, 4], [2, 5], [3, 6]]
        )
        self.assertEqual(matrices.plus(A, B), [[6, 8], [10, 12]])
        self.assertEqual(matrices.minus(B, A), [[4, 4], [4, 4]])
        self.assertEqual(matrices.scaled(A, 2), [[2, 4], [6, 8]])
        self.assertEqual(matrices.identity(2), [[1, 0], [0, 1]])
        self.assertEqual(matrices.zeros(1, 3), [[0, 0, 0]])
        self.assertEqual(matrices.apply(A, [1, 1]), [3, 7])
        self.assertEqual(matrices.power(A, 0), [[1, 0], [0, 1]])
        self.assertEqual(matrices.power(A, 3), [[37, 54], [81, 118]])
        self.assertEqual(matrices.trace(A), 5)
        self.assertEqual(matrices.determinant(A), -2)
        self.assertEqual(
            matrices.determinant([[2, 0, 1], [1, 3, 2], [1, 1, 1]]), 2 * 1 - 0 + 1 * -2
        )

    def test_inverse_and_solve(self) -> None:
        self.assertEqual(matrices.inverse(A), [[-2, 1], [1.5, -0.5]])
        self.assertEqual(matrices.times(A, matrices.inverse(A)), [[1, 0], [0, 1]])
        self.assertEqual(matrices.solve([[2, 1], [1, 3]], [3, 5]), [0.8, 1.4])

    def test_a_matrix_with_no_inverse_says_so(self) -> None:
        for doing in (matrices.inverse, lambda m: matrices.solve(m, [1, 2])):
            with self.assertRaisesRegex(MatrixError, "no inverse"):
                doing([[1, 2], [2, 4]])


class EngineTests(unittest.TestCase):
    """NumPy and plain Python must agree, since a trace made with one may be
    replayed with the other."""

    def both(self, work: Any) -> tuple[Any, Any]:
        fast = work()
        with mock.patch.object(matrices, "_np", None):
            self.assertEqual(matrices.engine(), "plain Python")
            plain = work()
        return fast, plain

    def test_a_large_whole_product_is_the_same_and_exact(self) -> None:
        if matrices._np is None:
            self.skipTest("NumPy is not installed")
        rng = random.Random(7)
        a = [[rng.randint(-99, 99) for _ in range(40)] for _ in range(40)]
        b = [[rng.randint(-99, 99) for _ in range(40)] for _ in range(40)]
        fast, plain = self.both(lambda: matrices.times(a, b))
        self.assertEqual(fast, plain)
        self.assertTrue(all(isinstance(x, int) for row in fast for x in row))

    def test_whole_numbers_too_large_for_numpy_are_still_exact(self) -> None:
        a = [[10**12 + i + j for j in range(40)] for i in range(40)]
        fast, plain = self.both(lambda: matrices.times(a, a))
        self.assertEqual(fast, plain)
        self.assertEqual(fast[0][0], sum(a[0][k] * a[k][0] for k in range(40)))

    def test_decimal_work_agrees_to_the_places_kept(self) -> None:
        rng = random.Random(11)
        a = [[rng.uniform(-5, 5) for _ in range(30)] for _ in range(30)]
        b = [[rng.uniform(-5, 5) for _ in range(30)] for _ in range(30)]
        fast, plain = self.both(lambda: matrices.times(a, b))
        worst = max(abs(x - y) for r, s in zip(fast, plain) for x, y in zip(r, s))
        self.assertLess(worst, 1e-6)

    def test_solving_agrees(self) -> None:
        rng = random.Random(3)
        a = [
            [rng.uniform(1, 9) + (20 if i == j else 0) for j in range(12)]
            for i in range(12)
        ]
        right = [rng.uniform(-3, 3) for _ in range(12)]
        fast, plain = self.both(lambda: matrices.solve(a, right))
        self.assertLess(max(abs(x - y) for x, y in zip(fast, plain)), 1e-6)
        back = matrices.apply(a, fast)
        self.assertLess(max(abs(x - y) for x, y in zip(back, right)), 1e-6)

    def test_numpy_makes_a_large_product_fast(self) -> None:
        if matrices._np is None:
            self.skipTest("NumPy is not installed")
        import time

        rng = random.Random(1)
        a = [[rng.uniform(-1, 1) for _ in range(300)] for _ in range(300)]
        started = time.perf_counter()
        made = matrices.times(a, a)
        self.assertEqual(len(made), 300)
        self.assertLess(time.perf_counter() - started, 5.0)


class RelationTests(unittest.TestCase):
    LESS_OR_EQUAL = [[1, 1, 1], [0, 1, 1], [0, 0, 1]]
    STEPS = [[0, 1, 0], [0, 0, 1], [0, 0, 0]]

    def test_the_properties_of_a_relation(self) -> None:
        r = self.LESS_OR_EQUAL
        self.assertTrue(matrices.is_reflexive(r))
        self.assertTrue(matrices.is_antisymmetric(r))
        self.assertTrue(matrices.is_transitive(r))
        self.assertFalse(matrices.is_symmetric(r))
        self.assertFalse(matrices.is_reflexive(self.STEPS))
        self.assertFalse(matrices.is_transitive(self.STEPS))
        self.assertTrue(matrices.is_symmetric([[0, 1], [1, 0]]))

    def test_composing_and_closing(self) -> None:
        self.assertEqual(
            matrices.bool_times(self.STEPS, self.STEPS),
            [[0, 0, 1], [0, 0, 0], [0, 0, 0]],
        )
        self.assertEqual(
            matrices.closure(self.STEPS), [[0, 1, 1], [0, 0, 1], [0, 0, 0]]
        )
        self.assertTrue(matrices.is_transitive(matrices.closure(self.STEPS)))

    def test_a_relation_holds_only_zeros_and_ones(self) -> None:
        with self.assertRaisesRegex(MatrixError, "0s and 1s, and this one holds 2"):
            matrices.closure([[0, 2], [0, 0]])


class GraphTests(unittest.TestCase):
    # 0 - 1 - 2   3 - 4
    EDGES = [[0, 1], [1, 2], [3, 4]]

    def setUp(self) -> None:
        self.graph = matrices.adjacency(self.EDGES, 5)

    def test_the_adjacency_matrix(self) -> None:
        self.assertEqual(self.graph[0], [0, 1, 0, 0, 0])
        self.assertTrue(matrices.is_symmetric(self.graph))
        one_way = matrices.adjacency(self.EDGES, 5, directed=True)
        self.assertEqual((one_way[0][1], one_way[1][0]), (1, 0))

    def test_degrees_neighbours_paths_and_parts(self) -> None:
        self.assertEqual(matrices.degrees(self.graph), [1, 2, 1, 1, 1])
        self.assertEqual(matrices.neighbours(self.graph, 1), [0, 2])
        self.assertEqual(matrices.shortest_path(self.graph, 0, 2), [0, 1, 2])
        self.assertEqual(matrices.shortest_path(self.graph, 0, 4), [])
        self.assertEqual(matrices.shortest_path(self.graph, 3, 3), [3])
        self.assertEqual(matrices.components(self.graph), [[0, 1, 2], [3, 4]])

    def test_a_power_of_the_adjacency_matrix_counts_walks(self) -> None:
        triangle = matrices.adjacency([[0, 1], [1, 2], [2, 0]], 3)
        # Two walks of length 2 from a corner back to itself, one to each other.
        self.assertEqual(matrices.power(triangle, 2), [[2, 1, 1], [1, 2, 1], [1, 1, 2]])

    def test_a_node_that_does_not_exist_is_named(self) -> None:
        with self.assertRaisesRegex(MatrixError, "from 0 to 4, and 7 is not"):
            matrices.adjacency([[0, 7]], 5)
        with self.assertRaisesRegex(MatrixError, "must be a pair of nodes"):
            matrices.adjacency([[0, 1, 2]], 5)


PROGRAM = """
record In { a: Number[][] b: Number[][] v: Number[] }
record Out { product: Number[][] det: Number moved: Number[] length: Number }
record Facts { transitive: Bool closed: Number[][] path: Number[] parts: Number[][] walks: Number[][] }

fn work(input: In) -> Out {
  return {
    product: input.a.times(input.b),
    det: input.a.determinant(),
    moved: input.a.apply(input.v).plus(input.v),
    length: input.v.scaled(2).norm()
  }
}

fn study(edges: In) -> Facts {
  let graph = edges.a.adjacency(4)
  let steps = edges.a.directed_adjacency(4)
  return {
    transitive: steps.is_transitive(),
    closed: steps.closure(),
    path: graph.shortest_path(0, 3),
    parts: graph.components(),
    walks: graph.power(2)
  }
}

workflow main(input: In) -> Out {
  out = work(input)
}

test "matrices and vectors read like the maths" {
  r = main({a: [[1, 2], [3, 4]], b: [[5, 6], [7, 8]], v: [3, 4]})
  expect r.product == [[19, 22], [43, 50]]
  expect r.det == -2
  expect r.moved == [14, 29]
  expect r.length == 10
  expect 3.identity() == [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
  expect [[2, 1], [1, 3]].solve([3, 5]) == [0.8, 1.4]
  expect [1, 2, 3].dot([4, 5, 6]) == 32
}

test "a graph from its edges" {
  f = study({a: [[0, 1], [1, 2], [2, 3]], b: [[0]], v: [0]})
  expect f.transitive == false
  expect f.closed == [[0, 1, 1, 1], [0, 0, 1, 1], [0, 0, 0, 1], [0, 0, 0, 0]]
  expect f.path == [0, 1, 2, 3]
  expect f.parts == [[0, 1, 2, 3]]
  expect f.walks[1] == [0, 2, 0, 1]
}
"""


class LanguageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "main.kos"

    def test_the_methods_work_as_written_in_kosika(self) -> None:
        self.path.write_text(PROGRAM, encoding="utf-8")
        results = run_tests(load_program(self.path))
        self.assertEqual(
            [(r.name, r.passed) for r in results],
            [
                ("matrices and vectors read like the maths", True),
                ("a graph from its edges", True),
            ],
            [r.message for r in results],
        )

    def test_a_shape_that_does_not_fit_fails_the_run_with_the_reason(self) -> None:
        self.path.write_text(
            PROGRAM
            + '\ntest "bad" {\n  r = main({a: [[1, 2]], b: [[1, 2]], v: [1, 2]})\n  expect r.det == 0\n}\n',
            encoding="utf-8",
        )
        bad = run_tests(load_program(self.path), only="bad")[0]
        self.assertFalse(bad.passed)
        self.assertIn("cannot be multiplied", bad.message)

    def test_the_checker_knows_the_types(self) -> None:
        for wrong, why in (
            ("input.a.times(input.v)", "times"),
            ("input.v.determinant()", "determinant"),
            ("input.a.dot(input.a)", "dot"),
        ):
            self.path.write_text(
                PROGRAM.replace("input.a.times(input.b)", wrong), encoding="utf-8"
            )
            with self.subTest(wrong=wrong), self.assertRaises(CheckError) as failed:
                load_program(self.path)
            self.assertIn(why, str(failed.exception))


if __name__ == "__main__":
    unittest.main()
