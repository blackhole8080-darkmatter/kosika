package main

// What a model learns with, a whole batch in one call: kosika/learning_ops.py
// in Go, giving the same numbers (every answer rounded to nine places).

import (
	"fmt"
	"math"
	"math/big"
	"sort"
	"strings"
)

func floatsOf(matrix []Value, name string) [][]float64 {
	rows, columns := shapeOf(matrix, name)
	out := make([][]float64, rows)
	for i, row := range matrix {
		cells := cellsOf(row, name)
		out[i] = make([]float64, columns)
		for j, cell := range cells {
			out[i][j] = toFloat(cell)
		}
	}
	return out
}

func tidyRows(rows [][]float64) Value {
	out := make([]Value, len(rows))
	for i, row := range rows {
		cells := make([]Value, len(row))
		for j, f := range row {
			cells[j] = tidyValue(f)
		}
		out[i] = cells
	}
	return out
}

func eachOf(matrix []Value, change func(float64) float64) Value {
	rows := floatsOf(matrix, "the matrix")
	for _, row := range rows {
		for j := range row {
			row[j] = change(row[j])
		}
	}
	return tidyRows(rows)
}

func sigmoidOf(x float64) float64 {
	if x >= 0 {
		return 1 / (1 + math.Exp(-x))
	}
	e := math.Exp(x)
	return e / (1 + e)
}

// wholeClass is a class number from 0 to count-1, or the run fails.
func wholeClass(v Value, count int) int {
	n, ok := asWhole(v)
	if _, isBool := v.(bool); isBool || !ok || n.Sign() < 0 || n.Cmp(big.NewInt(int64(count))) >= 0 {
		runFail(fmt.Sprintf("class %s is not a whole number from 0 to %d", pyReprAny(v), count-1))
	}
	return int(n.Int64())
}

var learningMethods = map[string]method{
	"each_times": {[]string{"other"}, func(r Value, a []Value) Value {
		x, y := listOf(r), listOf(a[0])
		xr, xc := shapeOf(x, "the first matrix")
		yr, yc := shapeOf(y, "the second matrix")
		if xr != yr || xc != yc {
			runFail(fmt.Sprintf("multiplying number by number needs two matrices of the same shape, and these are %d by %d and %d by %d", xr, xc, yr, yc))
		}
		p, q := floatsOf(x, "the first matrix"), floatsOf(y, "the second matrix")
		for i := range p {
			for j := range p[i] {
				p[i][j] *= q[i][j]
			}
		}
		return tidyRows(p)
	}},
	"tanh":       {nil, func(r Value, a []Value) Value { return eachOf(listOf(r), math.Tanh) }},
	"tanh_slope": {nil, func(r Value, a []Value) Value { return eachOf(listOf(r), func(t float64) float64 { return 1 - t*t }) }},
	"sigmoid":    {nil, func(r Value, a []Value) Value { return eachOf(listOf(r), sigmoidOf) }},
	"relu": {nil, func(r Value, a []Value) Value {
		return eachOf(listOf(r), func(x float64) float64 {
			if x > 0 {
				return x
			}
			return 0
		})
	}},
	"relu_slope": {nil, func(r Value, a []Value) Value {
		return eachOf(listOf(r), func(x float64) float64 {
			if x > 0 {
				return 1
			}
			return 0
		})
	}},
	"exp": {nil, func(r Value, a []Value) Value {
		return eachOf(listOf(r), func(x float64) float64 {
			if x > 700 {
				runFail("exp of a number above 700 is too large to be a number")
			}
			return math.Exp(x)
		})
	}},
	"softmax": {nil, func(r Value, a []Value) Value {
		rows := floatsOf(listOf(r), "the matrix")
		for _, row := range rows {
			top := row[0]
			for _, x := range row {
				top = math.Max(top, x)
			}
			weights := make([]float64, len(row))
			for j, x := range row {
				weights[j] = math.Exp(x - top)
			}
			total := floatSum(weights)
			for j := range row {
				row[j] = weights[j] / total
			}
		}
		return tidyRows(rows)
	}},
	"plus_row": {[]string{"row"}, func(r Value, a []Value) Value {
		m := listOf(r)
		_, columns := shapeOf(m, "the matrix")
		add := listOf(a[0])
		if len(add) != columns {
			runFail(fmt.Sprintf("adding a row of %d numbers to every row needs a matrix with %d columns, and this one has %d", len(add), len(add), columns))
		}
		rows := floatsOf(m, "the matrix")
		for _, row := range rows {
			for j := range row {
				row[j] += toFloat(add[j])
			}
		}
		return tidyRows(rows)
	}},
	"column_sums": {nil, func(r Value, a []Value) Value {
		rows := floatsOf(listOf(r), "the matrix")
		out := make([]Value, len(rows[0]))
		for j := range rows[0] {
			column := make([]float64, len(rows))
			for i := range rows {
				column[i] = rows[i][j]
			}
			out[j] = tidyValue(floatSum(column))
		}
		return out
	}},
	"row_argmax": {nil, func(r Value, a []Value) Value {
		m := listOf(r)
		shapeOf(m, "the matrix")
		out := make([]Value, len(m))
		for i, row := range m {
			cells := cellsOf(row, "the matrix")
			best := 0
			for j := range cells {
				if compareNumbers(cells[j], cells[best]) > 0 {
					best = j
				}
			}
			out[i] = bigInt(best)
		}
		return out
	}},
	"cross_entropy": {[]string{"classes"}, func(r Value, a []Value) Value {
		m := listOf(r)
		rows, columns := shapeOf(m, "the probabilities")
		classes := listOf(a[0])
		if len(classes) != rows {
			runFail(fmt.Sprintf("%d rows of probabilities need %d classes, and %d were given", rows, rows, len(classes)))
		}
		p := floatsOf(m, "the probabilities")
		terms := make([]float64, rows)
		for i := range p {
			terms[i] = -math.Log(math.Max(p[i][wholeClass(classes[i], columns)], 1e-12))
		}
		return tidyValue(floatSum(terms) / float64(rows))
	}},
	"one_hot": {[]string{"classes"}, func(r Value, a []Value) Value {
		count, ok := asWhole(a[0])
		if _, isBool := a[0].(bool); isBool || !ok || count.Sign() < 1 {
			runFail("one_hot needs a whole number of classes, 1 or more")
		}
		n := int(count.Int64())
		labels := listOf(r)
		if len(labels)*n > maxMatrixCells {
			runFail(fmt.Sprintf("that matrix is larger than %d numbers", maxMatrixCells))
		}
		out := make([]Value, len(labels))
		for i, label := range labels {
			at := wholeClass(label, n)
			row := make([]Value, n)
			for j := range row {
				row[j] = bigInt(0)
			}
			row[at] = bigInt(1)
			out[i] = row
		}
		return out
	}},
}

func noiseOf(r Value, a []Value) Value {
	rows, rowsOK := asWhole(r)
	columns, columnsOK := asWhole(a[0])
	_, rBool := r.(bool)
	_, cBool := a[0].(bool)
	if rBool || cBool || !rowsOK || !columnsOK || rows.Sign() < 1 || columns.Sign() < 1 {
		runFail("noise needs whole numbers of rows and columns, 1 or more")
	}
	nr, nc := int(rows.Int64()), int(columns.Int64())
	if nr*nc > maxMatrixCells {
		runFail(fmt.Sprintf("that matrix is larger than %d numbers", maxMatrixCells))
	}
	seed, ok := asWhole(a[1])
	if !ok {
		seed = big.NewInt(int64(toFloat(a[1])))
	}
	s := new(big.Int).Abs(seed)
	s.Mul(s, big.NewInt(1103515245)).Add(s, big.NewInt(12345)).Mod(s, big.NewInt(2147483648))
	state := s.Int64()
	out := make([]Value, nr)
	for i := range out {
		row := make([]Value, nc)
		for j := range row {
			state = (state*1103515245 + 12345) % 2147483648
			row[j] = tidyValue(float64(state)/1073741824 - 1)
		}
		out[i] = row
	}
	return out
}

func init() {
	for name, m := range learningMethods {
		matrixMethods[name] = m
	}
	numberMethods["noise"] = method{[]string{"columns", "seed"}, noiseOf}
}

func textWordsOf(text string) []string { return strings.FieldsFunc(text, isSpace) }

func topWordsOf(r Value, a []Value) Value {
	count, ok := asWhole(a[0])
	if _, isBool := a[0].(bool); isBool || !ok || count.Sign() < 1 {
		runFail("top_words needs a whole number of words, 1 or more")
	}
	found := map[string]int{}
	for _, t := range listOf(r) {
		seen := map[string]bool{}
		for _, w := range textWordsOf(textOf(t)) {
			if !seen[w] {
				seen[w] = true
				found[w]++
			}
		}
	}
	words := make([]string, 0, len(found))
	for w := range found {
		words = append(words, w)
	}
	sort.Slice(words, func(i, j int) bool {
		if found[words[i]] != found[words[j]] {
			return found[words[i]] > found[words[j]]
		}
		return words[i] < words[j]
	})
	if n := int(count.Int64()); n < len(words) {
		words = words[:n]
	}
	return stringsToValues(words)
}

func bagOfWordsOf(r Value, a []Value) Value {
	texts, vocabulary := listOf(r), listOf(a[0])
	if len(vocabulary) == 0 {
		runFail("bag_of_words needs a vocabulary of at least one word")
	}
	if len(texts)*len(vocabulary) > maxMatrixCells {
		runFail(fmt.Sprintf("that matrix is larger than %d numbers", maxMatrixCells))
	}
	out := make([]Value, len(texts))
	for i, t := range texts {
		has := map[string]bool{}
		for _, w := range textWordsOf(textOf(t)) {
			has[w] = true
		}
		row := make([]Value, len(vocabulary))
		for j, w := range vocabulary {
			if has[textOf(w)] {
				row[j] = bigInt(1)
			} else {
				row[j] = bigInt(0)
			}
		}
		out[i] = row
	}
	return out
}

func init() {
	matrixMethods["top_words"] = method{[]string{"count"}, topWordsOf}
	matrixMethods["bag_of_words"] = method{[]string{"vocabulary"}, bagOfWordsOf}
}
