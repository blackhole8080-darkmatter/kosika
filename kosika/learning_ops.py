"""The arithmetic a model learns with, as built-in methods on matrices.

A Kosika program writes its model, and how it learns, in Kosika
(std/learn.kos). What is slow in any interpreted language, a loop over every
number of a matrix, is done here instead, one call for a whole batch:

    h = x.times(w1).plus_row(b1).tanh()          # a layer, for every example at once
    p = h.times(w2).plus_row(b2).softmax()       # probabilities, row by row
    loss = p.cross_entropy(labels)               # how wrong, on average

and back again, for the gradients:

    dz = p.minus(labels.one_hot(classes)).scaled(1 / n)
    dw2 = h.transpose().times(dz)
    dh = dz.times(w2.transpose()).each_times(h.tanh_slope())

Every answer is rounded to nine places, as the other matrix methods are, so
both engines give the same numbers (native/learning_ops.go). Shapes that do
not fit fail the run with a message naming them.
"""

from __future__ import annotations

import math
from typing import Any

from .matrices import MAX_CELLS, MatrixError, _tidy, shape

Matrix = list[list[Any]]
Vector = list[Any]


def _same_shape(a: Matrix, b: Matrix, doing: str) -> None:
    first, second = shape(a, "the first matrix"), shape(b, "the second matrix")
    if first != second:
        raise MatrixError(
            f"{doing} needs two matrices of the same shape, and these are "
            f"{first[0]} by {first[1]} and {second[0]} by {second[1]}"
        )


def each_times(a: Matrix, b: Matrix) -> Matrix:
    """Number by number: the first row's first number by the second's, and so on."""
    _same_shape(a, b, "multiplying number by number")
    return [[_tidy(x * y) for x, y in zip(row, other)] for row, other in zip(a, b)]


def _each(matrix: Matrix, change) -> Matrix:
    shape(matrix)
    return [[_tidy(change(float(x))) for x in row] for row in matrix]


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def tanh(matrix: Matrix) -> Matrix:
    return _each(matrix, math.tanh)


def tanh_slope(matrix: Matrix) -> Matrix:
    """1 - t², for a matrix t that tanh gave: how fast tanh changed there."""
    return _each(matrix, lambda t: 1.0 - t * t)


def sigmoid(matrix: Matrix) -> Matrix:
    return _each(matrix, _sigmoid)


def relu(matrix: Matrix) -> Matrix:
    return _each(matrix, lambda x: x if x > 0 else 0.0)


def relu_slope(matrix: Matrix) -> Matrix:
    return _each(matrix, lambda x: 1.0 if x > 0 else 0.0)


def exp(matrix: Matrix) -> Matrix:
    def safe(x: float) -> float:
        if x > 700:
            raise MatrixError("exp of a number above 700 is too large to be a number")
        return math.exp(x)

    return _each(matrix, safe)


def softmax(matrix: Matrix) -> Matrix:
    """Each row as probabilities that add up to 1."""
    shape(matrix)
    out = []
    for row in matrix:
        top = max(float(x) for x in row)
        weights = [math.exp(float(x) - top) for x in row]
        total = math.fsum(weights)
        out.append([_tidy(w / total) for w in weights])
    return out


def plus_row(matrix: Matrix, row: Vector) -> Matrix:
    """The same row added to every row: a layer's biases, for every example."""
    rows, columns = shape(matrix)
    if len(row) != columns:
        raise MatrixError(
            f"adding a row of {len(row)} numbers to every row needs a matrix with "
            f"{len(row)} columns, and this one has {columns}"
        )
    return [[_tidy(x + y) for x, y in zip(r, row)] for r in matrix]


def column_sums(matrix: Matrix) -> Vector:
    shape(matrix)
    return [_tidy(math.fsum(float(x) for x in column)) for column in zip(*matrix)]


def row_argmax(matrix: Matrix) -> Vector:
    """For each row, where its largest number is, counted from 0 (the first, on a tie)."""
    shape(matrix)
    return [max(range(len(row)), key=lambda i: (row[i], -i)) for row in matrix]


def cross_entropy(probabilities: Matrix, classes: Vector) -> Any:
    """How surprised the probabilities are by the right classes, on average:
    the mean of -log p[class]; 0 when every right class had probability 1."""
    rows, columns = shape(probabilities, "the probabilities")
    if len(classes) != rows:
        raise MatrixError(f"{rows} rows of probabilities need {rows} classes, and {len(classes)} were given")
    terms = []
    for row, label in zip(probabilities, classes):
        if not isinstance(label, int) or isinstance(label, bool) or not 0 <= label < columns:
            raise MatrixError(f"class {label!r} is not a whole number from 0 to {columns - 1}")
        terms.append(-math.log(max(float(row[label]), 1e-12)))
    return _tidy(math.fsum(terms) / rows)


def one_hot(classes: Vector, count: Any) -> Matrix:
    """Each class as a row of 0s with a 1 where the class is."""
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise MatrixError("one_hot needs a whole number of classes, 1 or more")
    if len(classes) * count > MAX_CELLS:
        raise MatrixError(f"that matrix is larger than {MAX_CELLS} numbers")
    out = []
    for label in classes:
        if not isinstance(label, int) or isinstance(label, bool) or not 0 <= label < count:
            raise MatrixError(f"class {label!r} is not a whole number from 0 to {count - 1}")
        out.append([1 if i == label else 0 for i in range(count)])
    return out


def noise(rows: Any, columns: Any, seed: Any) -> Matrix:
    """A matrix of numbers between -1 and 1 that depend on the seed alone:
    a model's starting weights, the same on every computer and engine."""
    for count in (rows, columns):
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise MatrixError("noise needs whole numbers of rows and columns, 1 or more")
    if rows * columns > MAX_CELLS:
        raise MatrixError(f"that matrix is larger than {MAX_CELLS} numbers")
    state = (abs(int(seed)) * 1103515245 + 12345) % 2147483648
    out = []
    for _ in range(rows):
        row = []
        for _ in range(columns):
            state = (state * 1103515245 + 12345) % 2147483648
            row.append(_tidy(state / 1073741824 - 1))
        out.append(row)
    return out


def _text_words(text: str) -> list[str]:
    return text.split()


def top_words(texts: list[str], count: Any) -> list[str]:
    """The `count` words found in the most texts (a text counts a word once),
    most first, and in alphabetical order among words found equally often:
    the vocabulary a model reads texts by."""
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise MatrixError("top_words needs a whole number of words, 1 or more")
    found: dict[str, int] = {}
    for text in texts:
        for word in set(_text_words(text)):
            found[word] = found.get(word, 0) + 1
    return sorted(found, key=lambda w: (-found[w], w))[:count]


def bag_of_words(texts: list[str], vocabulary: list[str]) -> Matrix:
    """One row for each text, one column for each word of the vocabulary:
    1 where the text has that word, else 0."""
    if not vocabulary:
        raise MatrixError("bag_of_words needs a vocabulary of at least one word")
    if len(texts) * len(vocabulary) > MAX_CELLS:
        raise MatrixError(f"that matrix is larger than {MAX_CELLS} numbers")
    return [[1 if w in words else 0 for w in vocabulary] for words in (set(_text_words(t)) for t in texts)]
