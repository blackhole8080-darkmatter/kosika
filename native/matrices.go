package main

// Vectors, matrices, relations and graphs: the built-in methods behind
// Number[] and Number[][], doing what the reference engine does.
//
// A vector is a list of numbers and a matrix is a list of equal-length
// rows, so there is no separate kind of value: what a list holds decides
// which of these a method call means. Whole numbers stay exact at any size;
// a decimal answer is rounded to nine places, as the reference rounds it.

import (
	"fmt"
	"math"
	"math/big"
)

const (
	// maxMatrixCells is how many numbers a vector or matrix may hold.
	maxMatrixCells = 4_000_000
	// singularCutoff is how near zero a determinant may be, against the
	// largest entry, before the matrix is treated as having no inverse.
	singularCutoff = 1e-12
)

// ---------------------------------------------------------------- shapes

func cellsOf(row Value, name string) []Value {
	cells, ok := row.([]Value)
	if !ok {
		runFail(name + " holds a " + hostName(row) + " where a row was needed")
	}
	return cells
}

// shapeOf gives the rows and columns of a matrix, after checking that every
// row is a list of the same length.
func shapeOf(matrix []Value, name string) (int, int) {
	if len(matrix) == 0 {
		runFail(name + " has no rows")
	}
	columns := len(cellsOf(matrix[0], name))
	if columns == 0 {
		runFail(name + " has rows with nothing in them")
	}
	for index, row := range matrix {
		cells := cellsOf(row, name)
		if len(cells) != columns {
			runFail(fmt.Sprintf(
				"%s is not a rectangle: row 1 has %d numbers and row %d has %d",
				name, columns, index+1, len(cells)))
		}
	}
	if len(matrix)*columns > maxMatrixCells {
		runFail(fmt.Sprintf("%s is larger than %d numbers", name, maxMatrixCells))
	}
	return len(matrix), columns
}

func squareOf(matrix []Value, doing string) int {
	rows, columns := shapeOf(matrix, "the matrix")
	if rows != columns {
		runFail(fmt.Sprintf(
			"%s needs a square matrix, and this one is %d by %d", doing, rows, columns))
	}
	return rows
}

// isNested says whether a list holds lists: a matrix rather than a vector.
func isNested(items []Value) bool {
	if len(items) == 0 {
		return false
	}
	_, ok := items[0].([]Value)
	return ok
}

func allWhole(items []Value) bool {
	for _, item := range items {
		if _, ok := item.(*big.Int); !ok {
			return false
		}
	}
	return true
}

func allWholeMatrix(matrix []Value) bool {
	for _, row := range matrix {
		if !allWhole(cellsOf(row, "the matrix")) {
			return false
		}
	}
	return true
}

// ---------------------------------------------------------------- numbers

// tidyValue is how the reference keeps a decimal answer: rounded to nine
// places, and whole when it is one, so a trace replays to the same value.
func tidyValue(f float64) Value {
	if math.IsInf(f, 0) || math.IsNaN(f) {
		runFail("the answer is too large to be a number")
	}
	f = roundTo9(f)
	if f == math.Trunc(f) {
		out, _ := new(big.Float).SetFloat64(f).Int(nil)
		return out
	}
	return f
}

// floatSum adds decimals without losing a small term to a large one, as the
// reference's fsum does, so both engines round to the same nine places.
func floatSum(parts []float64) float64 {
	total := new(big.Float).SetPrec(256)
	term := new(big.Float).SetPrec(256)
	for _, part := range parts {
		total.Add(total, term.SetFloat64(part))
	}
	out, _ := total.Float64()
	return out
}

func truthOf(v Value) bool { return compareNumbers(v, bigInt(0)) != 0 }

// ---------------------------------------------------------------- vectors

func sameLength(a, b []Value, doing string) {
	if len(a) != len(b) {
		runFail(fmt.Sprintf(
			"%s needs two vectors of the same length, and these have %d and %d numbers",
			doing, len(a), len(b)))
	}
}

func pairSum(x, y Value, sign int) Value {
	xi, xWhole := x.(*big.Int)
	yi, yWhole := y.(*big.Int)
	if xWhole && yWhole {
		if sign < 0 {
			return new(big.Int).Sub(xi, yi)
		}
		return new(big.Int).Add(xi, yi)
	}
	if sign < 0 {
		return tidyValue(toFloat(x) - toFloat(y))
	}
	return tidyValue(toFloat(x) + toFloat(y))
}

func vectorSum(a, b []Value, sign int, doing string) Value {
	sameLength(a, b, doing)
	out := make([]Value, len(a))
	for i := range a {
		out[i] = pairSum(a[i], b[i], sign)
	}
	return out
}

func scaleItems(items []Value, by Value) []Value {
	out := make([]Value, len(items))
	for i, item := range items {
		if x, xWhole := item.(*big.Int); xWhole {
			if y, yWhole := by.(*big.Int); yWhole {
				out[i] = new(big.Int).Mul(x, y)
				continue
			}
		}
		out[i] = tidyValue(toFloat(item) * toFloat(by))
	}
	return out
}

func dotOf(a, b []Value) Value {
	sameLength(a, b, "a dot product")
	if allWhole(a) && allWhole(b) {
		total := new(big.Int)
		for i := range a {
			total.Add(total, new(big.Int).Mul(a[i].(*big.Int), b[i].(*big.Int)))
		}
		return total
	}
	parts := make([]float64, len(a))
	for i := range a {
		parts[i] = toFloat(a[i]) * toFloat(b[i])
	}
	return tidyValue(floatSum(parts))
}

func normOf(a []Value) Value {
	parts := make([]float64, len(a))
	for i := range a {
		f := toFloat(a[i])
		parts[i] = f * f
	}
	return tidyValue(math.Sqrt(floatSum(parts)))
}

func meanOf(a []Value) Value {
	if len(a) == 0 {
		runFail("the mean of no numbers does not exist")
	}
	parts := make([]float64, len(a))
	for i := range a {
		parts[i] = toFloat(a[i])
	}
	return tidyValue(floatSum(parts) / float64(len(a)))
}

func crossOf(a, b []Value) Value {
	if len(a) != 3 || len(b) != 3 {
		runFail(fmt.Sprintf(
			"a cross product needs two vectors of 3 numbers, and these have %d and %d",
			len(a), len(b)))
	}
	part := func(i, j int) Value {
		return arithmetic("-", arithmetic("*", a[i], b[j]), arithmetic("*", a[j], b[i]))
	}
	made := []Value{part(1, 2), part(2, 0), part(0, 1)}
	if allWhole(a) && allWhole(b) {
		return made
	}
	out := make([]Value, 3)
	for i, item := range made {
		out[i] = tidyValue(toFloat(item))
	}
	return out
}

// ---------------------------------------------------------------- matrices

func identityMatrix(size int) []Value {
	out := make([]Value, size)
	for row := 0; row < size; row++ {
		cells := make([]Value, size)
		for column := 0; column < size; column++ {
			if row == column {
				cells[column] = bigInt(1)
			} else {
				cells[column] = bigInt(0)
			}
		}
		out[row] = cells
	}
	return out
}

func identityFloats(size int) [][]float64 {
	out := make([][]float64, size)
	for row := 0; row < size; row++ {
		out[row] = make([]float64, size)
		out[row][row] = 1
	}
	return out
}

func zerosMatrix(rows, columns int) []Value {
	out := make([]Value, rows)
	for row := 0; row < rows; row++ {
		cells := make([]Value, columns)
		for column := 0; column < columns; column++ {
			cells[column] = bigInt(0)
		}
		out[row] = cells
	}
	return out
}

func transposeOf(matrix []Value) Value {
	rows, columns := shapeOf(matrix, "the matrix")
	out := make([]Value, columns)
	for column := 0; column < columns; column++ {
		cells := make([]Value, rows)
		for row := 0; row < rows; row++ {
			cells[row] = cellsOf(matrix[row], "the matrix")[column]
		}
		out[column] = cells
	}
	return out
}

func matrixSum(a, b []Value, sign int, doing string) Value {
	firstRows, firstColumns := shapeOf(a, "the first matrix")
	secondRows, secondColumns := shapeOf(b, "the second matrix")
	if firstRows != secondRows || firstColumns != secondColumns {
		runFail(fmt.Sprintf(
			"%s needs two matrices of the same shape, and these are %d by %d and %d by %d",
			doing, firstRows, firstColumns, secondRows, secondColumns))
	}
	out := make([]Value, firstRows)
	for row := 0; row < firstRows; row++ {
		left := cellsOf(a[row], "the first matrix")
		right := cellsOf(b[row], "the second matrix")
		cells := make([]Value, firstColumns)
		for column := range left {
			cells[column] = pairSum(left[column], right[column], sign)
		}
		out[row] = cells
	}
	return out
}

func timesOf(a, b []Value) Value {
	rows, inner := shapeOf(a, "the first matrix")
	otherRows, columns := shapeOf(b, "the second matrix")
	if inner != otherRows {
		runFail(fmt.Sprintf(
			"a %d by %d matrix cannot be multiplied by a %d by %d one: the first has %d columns and the second has %d rows, and those must be equal",
			rows, inner, otherRows, columns, inner, otherRows))
	}
	whole := allWholeMatrix(a) && allWholeMatrix(b)
	turned := cellsOf(transposeOf(b), "the second matrix")
	out := make([]Value, rows)
	for row := 0; row < rows; row++ {
		left := cellsOf(a[row], "the first matrix")
		cells := make([]Value, columns)
		for column := 0; column < columns; column++ {
			right := cellsOf(turned[column], "the second matrix")
			if whole {
				total := new(big.Int)
				for step := 0; step < inner; step++ {
					total.Add(total, new(big.Int).Mul(left[step].(*big.Int), right[step].(*big.Int)))
				}
				cells[column] = total
				continue
			}
			parts := make([]float64, inner)
			for step := 0; step < inner; step++ {
				parts[step] = toFloat(left[step]) * toFloat(right[step])
			}
			cells[column] = tidyValue(floatSum(parts))
		}
		out[row] = cells
	}
	return out
}

func applyTo(matrix []Value, vector []Value) Value {
	rows, columns := shapeOf(matrix, "the matrix")
	if columns != len(vector) {
		runFail(fmt.Sprintf(
			"a %d by %d matrix cannot be applied to a vector of %d numbers: it needs %d",
			rows, columns, len(vector), columns))
	}
	whole := allWholeMatrix(matrix) && allWhole(vector)
	out := make([]Value, rows)
	for row := 0; row < rows; row++ {
		cells := cellsOf(matrix[row], "the matrix")
		if whole {
			total := new(big.Int)
			for column := 0; column < columns; column++ {
				total.Add(total, new(big.Int).Mul(cells[column].(*big.Int), vector[column].(*big.Int)))
			}
			out[row] = total
			continue
		}
		parts := make([]float64, columns)
		for column := 0; column < columns; column++ {
			parts[column] = toFloat(cells[column]) * toFloat(vector[column])
		}
		out[row] = tidyValue(floatSum(parts))
	}
	return out
}

func powerOf(matrix []Value, exponent Value) Value {
	size := squareOf(matrix, "a power")
	steps, ok := asWhole(exponent)
	if _, isBool := exponent.(bool); isBool || !ok || steps.Sign() < 0 {
		runFail("a matrix power needs a whole exponent of 0 or more")
	}
	result := identityMatrix(size)
	base := matrix
	for steps.Sign() > 0 {
		if steps.Bit(0) == 1 {
			result = cellsOf(timesOf(result, base), "a product")
		}
		steps = new(big.Int).Rsh(steps, 1)
		if steps.Sign() > 0 {
			base = cellsOf(timesOf(base, base), "a product")
		}
	}
	return result
}

func traceOf(matrix []Value) Value {
	size := squareOf(matrix, "a trace")
	var total Value = bigInt(0)
	whole := allWholeMatrix(matrix)
	parts := make([]float64, size)
	for step := 0; step < size; step++ {
		cell := cellsOf(matrix[step], "the matrix")[step]
		if whole {
			total = arithmetic("+", total, cell)
			continue
		}
		parts[step] = toFloat(cell)
	}
	if whole {
		return total
	}
	return tidyValue(floatSum(parts))
}

// wholeDeterminant is exact, by Bareiss elimination: the division at each
// step leaves no remainder, so no decimals appear however large the numbers.
func wholeDeterminant(matrix []Value) *big.Int {
	size := len(matrix)
	work := make([][]*big.Int, size)
	for row := 0; row < size; row++ {
		cells := cellsOf(matrix[row], "the matrix")
		work[row] = make([]*big.Int, size)
		for column := 0; column < size; column++ {
			work[row][column] = new(big.Int).Set(cells[column].(*big.Int))
		}
	}
	sign, previous := 1, big.NewInt(1)
	for step := 0; step < size-1; step++ {
		if work[step][step].Sign() == 0 {
			swap := -1
			for row := step + 1; row < size; row++ {
				if work[row][step].Sign() != 0 {
					swap = row
					break
				}
			}
			if swap < 0 {
				return big.NewInt(0)
			}
			work[step], work[swap] = work[swap], work[step]
			sign = -sign
		}
		for row := step + 1; row < size; row++ {
			for column := step + 1; column < size; column++ {
				left := new(big.Int).Mul(work[row][column], work[step][step])
				right := new(big.Int).Mul(work[row][step], work[step][column])
				work[row][column] = new(big.Int).Div(new(big.Int).Sub(left, right), previous)
			}
		}
		previous = work[step][step]
	}
	last := new(big.Int).Set(work[size-1][size-1])
	if sign < 0 {
		last.Neg(last)
	}
	return last
}

// eliminate solves matrix * X = right by elimination with pivoting, giving X
// and the determinant, or nothing when the matrix has no inverse.
func eliminate(matrix []Value, right [][]float64) ([][]float64, float64, bool) {
	size := len(matrix)
	width := len(right[0])
	work := make([][]float64, size)
	scale := 0.0
	for row := 0; row < size; row++ {
		cells := cellsOf(matrix[row], "the matrix")
		work[row] = make([]float64, len(cells)+width)
		for column, cell := range cells {
			work[row][column] = toFloat(cell)
			if near := math.Abs(work[row][column]); near > scale {
				scale = near
			}
		}
		for column, cell := range right[row] {
			work[row][len(cells)+column] = cell
		}
	}
	if scale == 0 {
		scale = 1
	}
	determinant := 1.0
	for step := 0; step < size; step++ {
		pivot, best := step, math.Abs(work[step][step])
		for row := step + 1; row < size; row++ {
			if near := math.Abs(work[row][step]); near > best {
				best, pivot = near, row
			}
		}
		if best <= singularCutoff*scale {
			return nil, 0, false
		}
		if pivot != step {
			work[step], work[pivot] = work[pivot], work[step]
			determinant = -determinant
		}
		determinant *= work[step][step]
		lead := work[step][step]
		for column := range work[step] {
			work[step][column] /= lead
		}
		for row := 0; row < size; row++ {
			if row == step || work[row][step] == 0 {
				continue
			}
			factor := work[row][step]
			for column := range work[row] {
				work[row][column] -= factor * work[step][column]
			}
		}
	}
	out := make([][]float64, size)
	for row := range work {
		out[row] = work[row][size:]
	}
	return out, determinant, true
}

func determinantOf(matrix []Value) Value {
	squareOf(matrix, "a determinant")
	if allWholeMatrix(matrix) {
		return wholeDeterminant(matrix)
	}
	size := len(matrix)
	_, determinant, ok := eliminate(matrix, identityFloats(size))
	if !ok {
		return bigInt(0)
	}
	return tidyValue(determinant)
}

func hasNoInverse(matrix []Value) bool {
	if allWholeMatrix(matrix) {
		return wholeDeterminant(matrix).Sign() == 0
	}
	_, _, ok := eliminate(matrix, identityFloats(len(matrix)))
	return !ok
}

func inverseOf(matrix []Value) Value {
	size := squareOf(matrix, "an inverse")
	if hasNoInverse(matrix) {
		runFail("this matrix has no inverse: its determinant is 0, so its rows are not independent")
	}
	found, _, ok := eliminate(matrix, identityFloats(size))
	if !ok {
		runFail("this matrix has no inverse: its determinant is 0, so its rows are not independent")
	}
	out := make([]Value, size)
	for row := 0; row < size; row++ {
		cells := make([]Value, size)
		for column := 0; column < size; column++ {
			cells[column] = tidyValue(found[row][column])
		}
		out[row] = cells
	}
	return out
}

func solveOf(matrix []Value, right []Value) Value {
	size := squareOf(matrix, "solving")
	if len(right) != size {
		runFail(fmt.Sprintf(
			"solving with a %d by %d matrix needs %d numbers on the right-hand side, and there are %d",
			size, size, size, len(right)))
	}
	column := make([][]float64, size)
	for row := 0; row < size; row++ {
		column[row] = []float64{toFloat(right[row])}
	}
	found, _, ok := eliminate(matrix, column)
	if !ok {
		runFail("these equations have no single answer: the matrix has no inverse")
	}
	out := make([]Value, size)
	for row := 0; row < size; row++ {
		out[row] = tidyValue(found[row][0])
	}
	return out
}

// ---------------------------------------------------------------- relations
// A relation on n things is an n by n matrix of 0s and 1s: a 1 in row i,
// column j says "i is related to j". A graph's adjacency matrix is the same.

func relationOf(matrix []Value, doing string) int {
	size := squareOf(matrix, doing)
	for row := 0; row < size; row++ {
		for _, cell := range cellsOf(matrix[row], doing) {
			if !isBinary(cell) {
				runFail(fmt.Sprintf(
					"%s needs a matrix of 0s and 1s, and this one holds %s",
					doing, formatNumber(cell)))
			}
		}
	}
	return size
}

func isBinary(v Value) bool {
	whole, ok := asWhole(v)
	if !ok {
		return false
	}
	return whole.Sign() == 0 || whole.Cmp(big.NewInt(1)) == 0
}

func isReflexiveOf(matrix []Value) Value {
	size := relationOf(matrix, "a relation")
	for step := 0; step < size; step++ {
		if !truthOf(cellsOf(matrix[step], "a relation")[step]) {
			return false
		}
	}
	return true
}

func isSymmetricOf(matrix []Value) Value {
	size := squareOf(matrix, "a symmetry check")
	for row := 0; row < size; row++ {
		for column := 0; column < row; column++ {
			if !valueEqual(
				cellsOf(matrix[row], "a symmetry check")[column],
				cellsOf(matrix[column], "a symmetry check")[row]) {
				return false
			}
		}
	}
	return true
}

func isAntisymmetricOf(matrix []Value) Value {
	size := relationOf(matrix, "a relation")
	for row := 0; row < size; row++ {
		for column := 0; column < row; column++ {
			if truthOf(cellsOf(matrix[row], "a relation")[column]) &&
				truthOf(cellsOf(matrix[column], "a relation")[row]) {
				return false
			}
		}
	}
	return true
}

func isTransitiveOf(matrix []Value) Value {
	size := relationOf(matrix, "a relation")
	reach := make([]map[int]bool, size)
	for row := 0; row < size; row++ {
		reach[row] = map[int]bool{}
		for column, cell := range cellsOf(matrix[row], "a relation") {
			if truthOf(cell) {
				reach[row][column] = true
			}
		}
	}
	for start := 0; start < size; start++ {
		for middle := range reach[start] {
			for end := range reach[middle] {
				if !reach[start][end] {
					return false
				}
			}
		}
	}
	return true
}

// boolTimesOf is the Boolean product: "and" for times, "or" for plus. It
// composes two relations.
func boolTimesOf(a, b []Value) Value {
	first := relationOf(a, "a Boolean product")
	relationOf(b, "a Boolean product")
	other := len(b)
	if first != other {
		runFail(fmt.Sprintf(
			"a Boolean product needs two relations on the same things, and these are %d by %d and %d by %d",
			first, first, other, other))
	}
	out := cellsOf(timesOf(a, b), "a Boolean product")
	for row := range out {
		cells := cellsOf(out[row], "a Boolean product")
		made := make([]Value, len(cells))
		for column, cell := range cells {
			if truthOf(cell) {
				made[column] = bigInt(1)
			} else {
				made[column] = bigInt(0)
			}
		}
		out[row] = made
	}
	return out
}

// closureOf is the transitive closure, by Warshall's algorithm: a 1
// wherever one thing can be reached from another.
func closureOf(matrix []Value) Value {
	size := relationOf(matrix, "a transitive closure")
	reach := make([][]bool, size)
	for row := 0; row < size; row++ {
		reach[row] = make([]bool, size)
		for column, cell := range cellsOf(matrix[row], "a transitive closure") {
			reach[row][column] = truthOf(cell)
		}
	}
	for through := 0; through < size; through++ {
		for start := 0; start < size; start++ {
			if !reach[start][through] {
				continue
			}
			for end := 0; end < size; end++ {
				if reach[through][end] {
					reach[start][end] = true
				}
			}
		}
	}
	out := make([]Value, size)
	for row := 0; row < size; row++ {
		cells := make([]Value, size)
		for column := 0; column < size; column++ {
			if reach[row][column] {
				cells[column] = bigInt(1)
			} else {
				cells[column] = bigInt(0)
			}
		}
		out[row] = cells
	}
	return out
}

// ---------------------------------------------------------------- graphs

func nodeOf(v Value, size int, what string) int {
	whole, ok := asWhole(v)
	if _, isBool := v.(bool); isBool || !ok || whole.Sign() < 0 || whole.Cmp(bigInt(size)) >= 0 {
		runFail(fmt.Sprintf(
			"%s must be a node from 0 to %d, and %s is not",
			what, size-1, formatNumber(v)))
	}
	return int(whole.Int64())
}

func adjacencyOf(edges Value, nodes Value, directed bool) Value {
	count, ok := asWhole(nodes)
	if _, isBool := nodes.(bool); isBool || !ok || count.Sign() < 1 {
		runFail("a matrix needs whole numbers of rows and columns, 1 or more")
	}
	size := int(count.Int64())
	if size*size > maxMatrixCells {
		runFail(fmt.Sprintf("that matrix is larger than %d numbers", maxMatrixCells))
	}
	made := zerosMatrix(size, size)
	for index, edge := range listOf(edges) {
		pair := cellsOf(edge, "an edge")
		if len(pair) != 2 {
			runFail(fmt.Sprintf(
				"edge %d must be a pair of nodes, and it has %d numbers", index+1, len(pair)))
		}
		start := nodeOf(pair[0], size, "an edge's start")
		end := nodeOf(pair[1], size, "an edge's end")
		cellsOf(made[start], "the adjacency matrix")[end] = bigInt(1)
		if !directed {
			cellsOf(made[end], "the adjacency matrix")[start] = bigInt(1)
		}
	}
	return made
}

func degreesOf(matrix []Value) Value {
	size := squareOf(matrix, "degrees")
	out := make([]Value, size)
	for row := 0; row < size; row++ {
		leaving := 0
		for _, cell := range cellsOf(matrix[row], "degrees") {
			if truthOf(cell) {
				leaving++
			}
		}
		out[row] = bigInt(leaving)
	}
	return out
}

func neighboursOf(matrix []Value, node Value) Value {
	size := squareOf(matrix, "neighbours")
	at := nodeOf(node, size, "the node")
	out := []Value{}
	for column, cell := range cellsOf(matrix[at], "neighbours") {
		if truthOf(cell) {
			out = append(out, bigInt(column))
		}
	}
	return out
}

func shortestPathOf(matrix []Value, start, end Value) Value {
	size := squareOf(matrix, "a path")
	from := nodeOf(start, size, "the start")
	to := nodeOf(end, size, "the end")
	cameFrom := map[int]int{from: from}
	waiting := []int{from}
	for len(waiting) > 0 {
		here := waiting[0]
		waiting = waiting[1:]
		if here == to {
			break
		}
		for there, cell := range cellsOf(matrix[here], "a path") {
			if !truthOf(cell) {
				continue
			}
			if _, seen := cameFrom[there]; seen {
				continue
			}
			cameFrom[there] = here
			waiting = append(waiting, there)
		}
	}
	if _, found := cameFrom[to]; !found {
		return []Value{}
	}
	path := []int{to}
	for path[len(path)-1] != from {
		path = append(path, cameFrom[path[len(path)-1]])
	}
	out := make([]Value, len(path))
	for index, node := range path {
		out[len(path)-1-index] = bigInt(node)
	}
	return out
}

func componentsOf(matrix []Value) Value {
	size := squareOf(matrix, "connected parts")
	seen := make([]bool, size)
	parts := []Value{}
	for first := 0; first < size; first++ {
		if seen[first] {
			continue
		}
		seen[first] = true
		part := []int{}
		waiting := []int{first}
		for len(waiting) > 0 {
			here := waiting[0]
			waiting = waiting[1:]
			part = append(part, here)
			for there := 0; there < size; there++ {
				if seen[there] {
					continue
				}
				if truthOf(cellsOf(matrix[here], "connected parts")[there]) ||
					truthOf(cellsOf(matrix[there], "connected parts")[here]) {
					seen[there] = true
					waiting = append(waiting, there)
				}
			}
		}
		sorted := sortValues(intValues(part), func(v Value) Value { return v })
		parts = append(parts, sorted)
	}
	return parts
}

func intValues(numbers []int) []Value {
	out := make([]Value, len(numbers))
	for index, number := range numbers {
		out[index] = bigInt(number)
	}
	return out
}

// ---------------------------------------------------------------- methods
// A vector and a matrix are both lists, so plus, minus and scaled are one
// entry each and decide by what the list holds.

var matrixMethods = map[string]method{
	"plus": {[]string{"other"}, func(r Value, a []Value) Value {
		if isNested(listOf(r)) {
			return matrixSum(listOf(r), listOf(a[0]), 1, "adding")
		}
		return vectorSum(listOf(r), listOf(a[0]), 1, "adding")
	}},
	"minus": {[]string{"other"}, func(r Value, a []Value) Value {
		if isNested(listOf(r)) {
			return matrixSum(listOf(r), listOf(a[0]), -1, "subtracting")
		}
		return vectorSum(listOf(r), listOf(a[0]), -1, "subtracting")
	}},
	"scaled": {[]string{"by"}, func(r Value, a []Value) Value {
		items := listOf(r)
		if isNested(items) {
			rows, _ := shapeOf(items, "the matrix")
			out := make([]Value, rows)
			for row := 0; row < rows; row++ {
				out[row] = scaleItems(cellsOf(items[row], "the matrix"), a[0])
			}
			return out
		}
		return scaleItems(items, a[0])
	}},
	"dot": {[]string{"other"}, func(r Value, a []Value) Value {
		return dotOf(listOf(r), listOf(a[0]))
	}},
	"cross": {[]string{"other"}, func(r Value, a []Value) Value {
		return crossOf(listOf(r), listOf(a[0]))
	}},
	"norm": {nil, func(r Value, a []Value) Value { return normOf(listOf(r)) }},
	"mean": {nil, func(r Value, a []Value) Value { return meanOf(listOf(r)) }},
	"rows": {nil, func(r Value, a []Value) Value {
		rows, _ := shapeOf(listOf(r), "the matrix")
		return bigInt(rows)
	}},
	"columns": {nil, func(r Value, a []Value) Value {
		_, columns := shapeOf(listOf(r), "the matrix")
		return bigInt(columns)
	}},
	"transpose": {nil, func(r Value, a []Value) Value { return transposeOf(listOf(r)) }},
	"times": {[]string{"other"}, func(r Value, a []Value) Value {
		return timesOf(listOf(r), listOf(a[0]))
	}},
	"apply": {[]string{"vector"}, func(r Value, a []Value) Value {
		return applyTo(listOf(r), listOf(a[0]))
	}},
	"power": {[]string{"exponent"}, func(r Value, a []Value) Value {
		return powerOf(listOf(r), a[0])
	}},
	"trace": {nil, func(r Value, a []Value) Value { return traceOf(listOf(r)) }},
	"determinant": {nil, func(r Value, a []Value) Value {
		return determinantOf(listOf(r))
	}},
	"inverse": {nil, func(r Value, a []Value) Value { return inverseOf(listOf(r)) }},
	"solve": {[]string{"right"}, func(r Value, a []Value) Value {
		return solveOf(listOf(r), listOf(a[0]))
	}},
	"is_reflexive": {nil, func(r Value, a []Value) Value {
		return isReflexiveOf(listOf(r))
	}},
	"is_symmetric": {nil, func(r Value, a []Value) Value {
		return isSymmetricOf(listOf(r))
	}},
	"is_antisymmetric": {nil, func(r Value, a []Value) Value {
		return isAntisymmetricOf(listOf(r))
	}},
	"is_transitive": {nil, func(r Value, a []Value) Value {
		return isTransitiveOf(listOf(r))
	}},
	"bool_times": {[]string{"other"}, func(r Value, a []Value) Value {
		return boolTimesOf(listOf(r), listOf(a[0]))
	}},
	"closure": {nil, func(r Value, a []Value) Value { return closureOf(listOf(r)) }},
	"adjacency": {[]string{"nodes"}, func(r Value, a []Value) Value {
		return adjacencyOf(r, a[0], false)
	}},
	"directed_adjacency": {[]string{"nodes"}, func(r Value, a []Value) Value {
		return adjacencyOf(r, a[0], true)
	}},
	"degrees": {nil, func(r Value, a []Value) Value { return degreesOf(listOf(r)) }},
	"neighbours": {[]string{"node"}, func(r Value, a []Value) Value {
		return neighboursOf(listOf(r), a[0])
	}},
	"shortest_path": {[]string{"start", "end"}, func(r Value, a []Value) Value {
		return shortestPathOf(listOf(r), a[0], a[1])
	}},
	"components": {nil, func(r Value, a []Value) Value { return componentsOf(listOf(r)) }},
}
