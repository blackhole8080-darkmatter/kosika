"""Vectors, matrices, relations and graphs for Kosika's built-in methods.

A vector is a `Number[]` and a matrix is a `Number[][]`: plain lists, so
they are stored in traces and replayed like any other value. This module is
the arithmetic behind the methods on them.

Two engines give the same answers:

- Whole numbers stay whole and exact. Sums and products of integers are done
  with Python's own integers, which do not overflow, unless the matrices are
  large and the numbers small enough that NumPy's 64-bit integers are proven
  safe, in which case NumPy does it.
- Anything with a decimal is done by NumPy when it is installed, and by plain
  Python otherwise. Such answers are rounded to 9 decimal places, like the
  other numeric built-ins, so a trace replays to the same value on another
  machine.

Every shape is checked before any arithmetic, and the message says what the
shapes were.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Any

_np: Any
try:  # NumPy is an optional engine, never a requirement.
    import numpy as _numpy

    _np = _numpy
except Exception:  # noqa: BLE001 - any failure to load means "not available"
    _np = None

Matrix = list[list[Any]]
Vector = list[Any]

#: A matrix or vector may not hold more numbers than this.
MAX_CELLS = 4_000_000
#: Above this many multiply-adds, NumPy is used when it can be.
FAST_ABOVE = 20_000
#: Decimal answers are rounded to this many places.
PLACES = 9
#: A matrix whose determinant is nearer to zero than this, compared with its
#: largest entry, is treated as having no inverse.
SINGULAR = 1e-12


class MatrixError(RuntimeError):
    """Raised when shapes do not fit or an answer does not exist."""


def engine() -> str:
    """Which engine does decimal arithmetic on this computer."""

    return f"numpy {_np.__version__}" if _np is not None else "plain Python"


# ------------------------------------------------------------------ shapes


def shape(matrix: Matrix, name: str = "the matrix") -> tuple[int, int]:
    """Rows and columns, after checking that every row has the same length."""

    rows = len(matrix)
    if rows == 0:
        raise MatrixError(f"{name} has no rows")
    columns = len(matrix[0])
    if columns == 0:
        raise MatrixError(f"{name} has rows with nothing in them")
    for index, row in enumerate(matrix):
        if len(row) != columns:
            raise MatrixError(
                f"{name} is not a rectangle: row 1 has {columns} numbers and "
                f"row {index + 1} has {len(row)}"
            )
    if rows * columns > MAX_CELLS:
        raise MatrixError(f"{name} is larger than {MAX_CELLS} numbers")
    return rows, columns


def _square(matrix: Matrix, doing: str) -> int:
    rows, columns = shape(matrix)
    if rows != columns:
        raise MatrixError(
            f"{doing} needs a square matrix, and this one is {rows} by {columns}"
        )
    return rows


def _whole(values: Any) -> bool:
    return all(
        isinstance(number, int) and not isinstance(number, bool)
        for row in values
        for number in (row if isinstance(row, list) else (row,))
    )


def _tidy(number: Any) -> Any:
    """A decimal answer as Kosika keeps it: rounded, and whole when it is."""

    value = float(number)
    if not math.isfinite(value):
        raise MatrixError("the answer is too large to be a number")
    value = round(value, PLACES)
    return int(value) if value.is_integer() else value


def _tidy_matrix(rows: Any) -> Matrix:
    return [[_tidy(number) for number in row] for row in rows]


def _largest(values: Any) -> int:
    return max(
        (
            abs(number)
            for row in values
            for number in (row if isinstance(row, list) else (row,))
        ),
        default=0,
    )


# ------------------------------------------------------------------ vectors


def _same_length(a: Vector, b: Vector, doing: str) -> None:
    if len(a) != len(b):
        raise MatrixError(
            f"{doing} needs two vectors of the same length, and these have "
            f"{len(a)} and {len(b)} numbers"
        )


def vector_plus(a: Vector, b: Vector) -> Vector:
    _same_length(a, b, "adding")
    return [_tidy(x + y) if not _whole([x, y]) else x + y for x, y in zip(a, b)]


def vector_minus(a: Vector, b: Vector) -> Vector:
    _same_length(a, b, "subtracting")
    return [_tidy(x - y) if not _whole([x, y]) else x - y for x, y in zip(a, b)]


def vector_scaled(a: Vector, by: Any) -> Vector:
    return [x * by if _whole([x, by]) else _tidy(x * by) for x in a]


def dot(a: Vector, b: Vector) -> Any:
    _same_length(a, b, "a dot product")
    if _whole(a) and _whole(b):
        return sum(x * y for x, y in zip(a, b))
    if _np is not None and len(a) > FAST_ABOVE:
        return _tidy(_np.dot(_np.asarray(a, dtype=float), _np.asarray(b, dtype=float)))
    return _tidy(math.fsum(x * y for x, y in zip(a, b)))


def norm(a: Vector) -> Any:
    return _tidy(math.sqrt(math.fsum(float(x) * float(x) for x in a)))


def cross(a: Vector, b: Vector) -> Vector:
    if len(a) != 3 or len(b) != 3:
        raise MatrixError(
            "a cross product needs two vectors of 3 numbers, and these have "
            f"{len(a)} and {len(b)}"
        )
    made = [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
    return made if _whole(a) and _whole(b) else [_tidy(x) for x in made]


def total(a: Vector) -> Any:
    return sum(a) if _whole(a) else _tidy(math.fsum(a))


def mean(a: Vector) -> Any:
    if not a:
        raise MatrixError("the mean of no numbers does not exist")
    return _tidy(math.fsum(a) / len(a))


# ------------------------------------------------------------------ matrices


def identity(size: Any) -> Matrix:
    if not isinstance(size, int) or isinstance(size, bool) or size < 1:
        raise MatrixError("an identity matrix needs a whole size of 1 or more")
    if size * size > MAX_CELLS:
        raise MatrixError(f"that identity matrix is larger than {MAX_CELLS} numbers")
    return [
        [1 if row == column else 0 for column in range(size)] for row in range(size)
    ]


def zeros(rows: Any, columns: Any) -> Matrix:
    for count in (rows, columns):
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise MatrixError(
                "a matrix needs whole numbers of rows and columns, 1 or more"
            )
    if rows * columns > MAX_CELLS:
        raise MatrixError(f"that matrix is larger than {MAX_CELLS} numbers")
    return [[0] * columns for _ in range(rows)]


def transpose(matrix: Matrix) -> Matrix:
    shape(matrix)
    return [list(column) for column in zip(*matrix)]


def _elementwise(a: Matrix, b: Matrix, doing: str, sign: int) -> Matrix:
    first, second = shape(a, "the first matrix"), shape(b, "the second matrix")
    if first != second:
        raise MatrixError(
            f"{doing} needs two matrices of the same shape, and these are "
            f"{first[0]} by {first[1]} and {second[0]} by {second[1]}"
        )
    if _whole(a) and _whole(b):
        return [[x + sign * y for x, y in zip(row, other)] for row, other in zip(a, b)]
    return [
        [_tidy(x + sign * y) for x, y in zip(row, other)] for row, other in zip(a, b)
    ]


def plus(a: Matrix, b: Matrix) -> Matrix:
    return _elementwise(a, b, "adding", 1)


def minus(a: Matrix, b: Matrix) -> Matrix:
    return _elementwise(a, b, "subtracting", -1)


def scaled(matrix: Matrix, by: Any) -> Matrix:
    shape(matrix)
    if _whole(matrix) and _whole([by]):
        return [[x * by for x in row] for row in matrix]
    return [[_tidy(x * by) for x in row] for row in matrix]


def times(a: Matrix, b: Matrix) -> Matrix:
    """The matrix product. Whole numbers in, exact whole numbers out."""

    (rows, inner), (other_rows, columns) = (
        shape(a, "the first matrix"),
        shape(b, "the second matrix"),
    )
    if inner != other_rows:
        raise MatrixError(
            f"a {rows} by {inner} matrix cannot be multiplied by a "
            f"{other_rows} by {columns} one: the first has {inner} columns and "
            f"the second has {other_rows} rows, and those must be equal"
        )
    work = rows * inner * columns
    whole = _whole(a) and _whole(b)
    if _np is not None and work > FAST_ABOVE:
        if whole:
            # 64-bit integers are exact only while no sum can pass 2**63.
            if _largest(a) * _largest(b) * inner < 2**62:
                made = _np.asarray(a, dtype=_np.int64) @ _np.asarray(b, dtype=_np.int64)
                return [[int(number) for number in row] for row in made.tolist()]
        else:
            return _tidy_matrix(
                (_np.asarray(a, dtype=float) @ _np.asarray(b, dtype=float)).tolist()
            )
    turned = list(zip(*b))
    if whole:
        return [
            [sum(x * y for x, y in zip(row, column)) for column in turned] for row in a
        ]
    return [
        [_tidy(math.fsum(x * y for x, y in zip(row, column))) for column in turned]
        for row in a
    ]


def apply(matrix: Matrix, vector: Vector) -> Vector:
    """The matrix times a vector written as a column."""

    rows, columns = shape(matrix)
    if columns != len(vector):
        raise MatrixError(
            f"a {rows} by {columns} matrix cannot be applied to a vector of "
            f"{len(vector)} numbers: it needs {columns}"
        )
    return [row[0] for row in times(matrix, [[number] for number in vector])]


def power(matrix: Matrix, exponent: Any) -> Matrix:
    size = _square(matrix, "a power")
    if not isinstance(exponent, int) or isinstance(exponent, bool) or exponent < 0:
        raise MatrixError("a matrix power needs a whole exponent of 0 or more")
    result, base = identity(size), matrix
    while exponent:
        if exponent & 1:
            result = times(result, base)
        exponent >>= 1
        if exponent:
            base = times(base, base)
    return result


def trace(matrix: Matrix) -> Any:
    size = _square(matrix, "a trace")
    made = sum(matrix[index][index] for index in range(size))
    return made if _whole(matrix) else _tidy(made)


def _whole_determinant(matrix: Matrix) -> int:
    """Exact, by Bareiss elimination: integer division that never leaves a
    remainder, so no decimals appear."""

    work = [list(row) for row in matrix]
    size, sign, previous = len(work), 1, 1
    for step in range(size - 1):
        if work[step][step] == 0:
            swap = next((r for r in range(step + 1, size) if work[r][step] != 0), None)
            if swap is None:
                return 0
            work[step], work[swap] = work[swap], work[step]
            sign = -sign
        for row in range(step + 1, size):
            for column in range(step + 1, size):
                work[row][column] = (
                    work[row][column] * work[step][step]
                    - work[row][step] * work[step][column]
                ) // previous
        previous = work[step][step]
    return sign * work[-1][-1]


def _eliminate(matrix: Matrix, right: Matrix) -> tuple[Matrix, float]:
    """Solve matrix * X = right by elimination with pivoting, in plain
    Python. Gives X and the determinant."""

    size = len(matrix)
    work = [
        [float(x) for x in row] + [float(x) for x in other]
        for row, other in zip(matrix, right)
    ]
    scale = max((abs(x) for row in matrix for x in row), default=0.0) or 1.0
    determinant = 1.0
    for step in range(size):
        pivot = max(range(step, size), key=lambda r: abs(work[r][step]))
        if abs(work[pivot][step]) <= SINGULAR * scale:
            return [], 0.0
        if pivot != step:
            work[step], work[pivot] = work[pivot], work[step]
            determinant = -determinant
        determinant *= work[step][step]
        lead = work[step][step]
        work[step] = [x / lead for x in work[step]]
        for row in range(size):
            if row != step and work[row][step]:
                factor = work[row][step]
                work[row] = [x - factor * y for x, y in zip(work[row], work[step])]
    return [row[size:] for row in work], determinant


def determinant(matrix: Matrix) -> Any:
    size = _square(matrix, "a determinant")
    if _whole(matrix):
        return _whole_determinant(matrix)
    if _np is not None and size > 8:
        return _tidy(_np.linalg.det(_np.asarray(matrix, dtype=float)))
    return _tidy(_eliminate(matrix, identity(size))[1])


def _no_inverse(matrix: Matrix) -> bool:
    if _whole(matrix):
        return _whole_determinant(matrix) == 0
    return not _eliminate(matrix, identity(len(matrix)))[0]


def inverse(matrix: Matrix) -> Matrix:
    size = _square(matrix, "an inverse")
    if (size <= 60 or _np is None) and _no_inverse(matrix):
        raise MatrixError(
            "this matrix has no inverse: its determinant is 0, so its rows are "
            "not independent"
        )
    if _np is not None and size > 8:
        try:
            array: Any = _np.asarray(matrix, dtype=float)
            if _np.linalg.cond(array) > 1 / SINGULAR:
                raise _np.linalg.LinAlgError
            return _tidy_matrix(_np.linalg.inv(array).tolist())
        except _np.linalg.LinAlgError:
            raise MatrixError(
                "this matrix has no inverse, or is so close to having none "
                "that an answer could not be trusted"
            ) from None
    return _tidy_matrix(_eliminate(matrix, identity(size))[0])


def solve(matrix: Matrix, right: Vector) -> Vector:
    """The x with matrix * x = right."""

    size = _square(matrix, "solving")
    if len(right) != size:
        raise MatrixError(
            f"solving with a {size} by {size} matrix needs {size} numbers on "
            f"the right-hand side, and there are {len(right)}"
        )
    if _np is not None and size > 8:
        try:
            array: Any = _np.asarray(matrix, dtype=float)
            if _np.linalg.cond(array) > 1 / SINGULAR:
                raise _np.linalg.LinAlgError
            return [
                _tidy(x)
                for x in _np.linalg.solve(
                    array, _np.asarray(right, dtype=float)
                ).tolist()
            ]
        except _np.linalg.LinAlgError:
            raise MatrixError(
                "these equations have no single answer: the matrix has no inverse"
            ) from None
    found, _ = _eliminate(matrix, [[x] for x in right])
    if not found:
        raise MatrixError(
            "these equations have no single answer: the matrix has no inverse"
        )
    return [_tidy(row[0]) for row in found]


# ------------------------------------------------------------------ relations
# A relation on n things is an n by n matrix of 0s and 1s: a 1 in row i,
# column j says "i is related to j". A graph's adjacency matrix is the same.


def _relation(matrix: Matrix, doing: str) -> int:
    size = _square(matrix, doing)
    for row in matrix:
        for number in row:
            if number not in (0, 1) or isinstance(number, bool):
                raise MatrixError(
                    f"{doing} needs a matrix of 0s and 1s, and this one holds {number}"
                )
    return size


def is_reflexive(matrix: Matrix) -> bool:
    size = _relation(matrix, "a relation")
    return all(matrix[i][i] == 1 for i in range(size))


def is_symmetric(matrix: Matrix) -> bool:
    size = _square(matrix, "a symmetry check")
    return all(matrix[i][j] == matrix[j][i] for i in range(size) for j in range(i))


def is_antisymmetric(matrix: Matrix) -> bool:
    size = _relation(matrix, "a relation")
    return not any(matrix[i][j] and matrix[j][i] for i in range(size) for j in range(i))


def is_transitive(matrix: Matrix) -> bool:
    size = _relation(matrix, "a relation")
    reach = [{j for j in range(size) if row[j]} for row in matrix]
    return all(reach[j] <= reach[i] for i in range(size) for j in reach[i])


def bool_times(a: Matrix, b: Matrix) -> Matrix:
    """The Boolean product: "and" for times, "or" for plus. It composes two
    relations."""

    _relation(a, "a Boolean product")
    _relation(b, "a Boolean product")
    if len(a) != len(b):
        raise MatrixError(
            f"a Boolean product needs two relations on the same things, and "
            f"these are {len(a)} by {len(a)} and {len(b)} by {len(b)}"
        )
    return [[1 if number else 0 for number in row] for row in times(a, b)]


def closure(matrix: Matrix) -> Matrix:
    """The transitive closure, by Warshall's algorithm: a 1 wherever one
    thing can be reached from another in one or more steps."""

    size = _relation(matrix, "a transitive closure")
    reach = [list(row) for row in matrix]
    for through in range(size):
        via = reach[through]
        for start in range(size):
            if reach[start][through]:
                row = reach[start]
                reach[start] = [1 if a or b else 0 for a, b in zip(row, via)]
    return reach


# ------------------------------------------------------------------ graphs


def _node(value: Any, size: int, what: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < size:
        raise MatrixError(
            f"{what} must be a node from 0 to {size - 1}, and {value} is not"
        )
    return value


def adjacency(edges: Matrix, nodes: Any, directed: bool = False) -> Matrix:
    """The adjacency matrix of a graph, from its edges written as pairs of
    node numbers starting at 0."""

    made = zeros(nodes, nodes)
    for index, edge in enumerate(edges):
        if len(edge) != 2:
            raise MatrixError(
                f"edge {index + 1} must be a pair of nodes, and it has {len(edge)} numbers"
            )
        start, end = (
            _node(edge[0], nodes, "an edge's start"),
            _node(edge[1], nodes, "an edge's end"),
        )
        made[start][end] = 1
        if not directed:
            made[end][start] = 1
    return made


def degrees(matrix: Matrix) -> Vector:
    """How many edges leave each node."""

    _square(matrix, "degrees")
    return [sum(1 for number in row if number) for row in matrix]


def neighbours(matrix: Matrix, node: Any) -> Vector:
    size = _square(matrix, "neighbours")
    return [
        j for j, number in enumerate(matrix[_node(node, size, "the node")]) if number
    ]


def shortest_path(matrix: Matrix, start: Any, end: Any) -> Vector:
    """The nodes along a path with the fewest edges, both ends included, or
    an empty list when there is none. Found breadth first."""

    size = _square(matrix, "a path")
    start, end = _node(start, size, "the start"), _node(end, size, "the end")
    came_from: dict[int, int] = {start: start}
    waiting = deque([start])
    while waiting:
        here = waiting.popleft()
        if here == end:
            break
        for there, number in enumerate(matrix[here]):
            if number and there not in came_from:
                came_from[there] = here
                waiting.append(there)
    if end not in came_from:
        return []
    path = [end]
    while path[-1] != start:
        path.append(came_from[path[-1]])
    return path[::-1]


def components(matrix: Matrix) -> Matrix:
    """The connected parts of a graph, each as a sorted list of its nodes.
    Edges are followed in both directions."""

    size = _square(matrix, "connected parts")
    seen: set[int] = set()
    parts: Matrix = []
    for first in range(size):
        if first in seen:
            continue
        part, waiting = [], deque([first])
        seen.add(first)
        while waiting:
            here = waiting.popleft()
            part.append(here)
            for there in range(size):
                if there not in seen and (matrix[here][there] or matrix[there][here]):
                    seen.add(there)
                    waiting.append(there)
        parts.append(sorted(part))
    return parts
