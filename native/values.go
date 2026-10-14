package main

// Kosika's values and how numbers behave.
//
// A value is nil (null), a bool, a Number, a string (Text), a []Value
// (list), a *Record (a record or a map) or a *Closure (a function).
// A Number is a *big.Int when it is whole and came from whole arithmetic,
// and a float64 otherwise. Whole arithmetic is exact at any size; dividing
// always gives a decimal. That is the reference engine's rule, and programs
// can see it: 9 / 3 is 3.0, which prints as "3" and equals 3.

import (
	"math"
	"math/big"
	"strconv"
	"strings"
)

type Value interface{}

// A Record keeps its fields in the order they were written.
type Record struct {
	Keys   []string
	Fields map[string]Value
}

func newRecord() *Record { return &Record{Fields: map[string]Value{}} }

func (r *Record) set(key string, value Value) {
	if _, has := r.Fields[key]; !has {
		r.Keys = append(r.Keys, key)
	}
	r.Fields[key] = value
}

func (r *Record) copy() *Record {
	out := newRecord()
	for _, key := range r.Keys {
		out.set(key, r.Fields[key])
	}
	return out
}

// A Closure is a function with the values it could see where it was made.
type Closure struct {
	Decl     *FuncDecl
	Captured map[string]Value
	Funcs    map[string]*FuncDecl
}

// A RunError is a failure of the program being run, not of the engine.
type RunError struct{ Message string }

func (e *RunError) Error() string { return e.Message }

func runFail(message string) { panic(&RunError{message}) }

func isNumber(v Value) bool {
	switch v.(type) {
	case *big.Int, float64:
		return true
	}
	return false
}

func toFloat(v Value) float64 {
	switch n := v.(type) {
	case *big.Int:
		f, _ := new(big.Float).SetInt(n).Float64()
		return f
	case float64:
		return n
	}
	runFail("a number was needed")
	return 0
}

// compareNumbers gives -1, 0 or 1, exactly, also between a whole number
// too large for a decimal and a decimal.
func compareNumbers(a, b Value) int {
	ai, aWhole := a.(*big.Int)
	bi, bWhole := b.(*big.Int)
	if aWhole && bWhole {
		return ai.Cmp(bi)
	}
	af, bf := toFloat(a), toFloat(b)
	if aWhole != bWhole && !math.IsInf(af, 0) && !math.IsInf(bf, 0) && !math.IsNaN(af) && !math.IsNaN(bf) {
		left, right := new(big.Float), new(big.Float)
		if aWhole {
			left.SetInt(ai)
			right.SetFloat64(bf)
		} else {
			left.SetFloat64(af)
			right.SetInt(bi)
		}
		return left.Cmp(right)
	}
	switch {
	case af < bf:
		return -1
	case af > bf:
		return 1
	}
	return 0
}

// valueEqual is Kosika's ==: by value, through lists and records, and true
// is never equal to 1.
func valueEqual(a, b Value) bool {
	_, aBool := a.(bool)
	_, bBool := b.(bool)
	if aBool || bBool {
		return aBool && bBool && a.(bool) == b.(bool)
	}
	if a == nil || b == nil {
		return a == nil && b == nil
	}
	if isNumber(a) && isNumber(b) {
		af, aFloat := a.(float64)
		bf, bFloat := b.(float64)
		if (aFloat && math.IsNaN(af)) || (bFloat && math.IsNaN(bf)) {
			return false
		}
		return compareNumbers(a, b) == 0
	}
	switch left := a.(type) {
	case string:
		right, ok := b.(string)
		return ok && left == right
	case []Value:
		right, ok := b.([]Value)
		if !ok || len(left) != len(right) {
			return false
		}
		for i := range left {
			if !valueEqual(left[i], right[i]) {
				return false
			}
		}
		return true
	case *Record:
		right, ok := b.(*Record)
		if !ok || len(left.Fields) != len(right.Fields) {
			return false
		}
		for key, value := range left.Fields {
			other, has := right.Fields[key]
			if !has || !valueEqual(value, other) {
				return false
			}
		}
		return true
	case *Closure:
		right, ok := b.(*Closure)
		return ok && left == right
	}
	return false
}

// truthy is how the reference engine reads a value used as a condition.
// The checker only lets a Bool stand there; this covers what it cannot see.
func truthy(v Value) bool {
	switch x := v.(type) {
	case nil:
		return false
	case bool:
		return x
	case *big.Int:
		return x.Sign() != 0
	case float64:
		return x != 0
	case string:
		return x != ""
	case []Value:
		return len(x) > 0
	case *Record:
		return len(x.Fields) > 0
	}
	return true
}

// formatFloat writes a decimal as the reference engine does: the shortest
// digits that read back as the same number, and no ".0" on a whole one.
func formatFloat(f float64) string {
	if math.IsNaN(f) {
		return "nan"
	}
	if math.IsInf(f, 1) {
		return "inf"
	}
	if math.IsInf(f, -1) {
		return "-inf"
	}
	if f == math.Trunc(f) {
		whole, _ := new(big.Float).SetFloat64(f).Int(nil)
		return whole.String()
	}
	// Shortest digits, as d.ddddde±xx.
	scientific := strconv.FormatFloat(f, 'e', -1, 64)
	sign := ""
	if strings.HasPrefix(scientific, "-") {
		sign, scientific = "-", scientific[1:]
	}
	parts := strings.SplitN(scientific, "e", 2)
	digits := strings.Replace(parts[0], ".", "", 1)
	exponent, _ := strconv.Atoi(parts[1])
	if exponent < -4 || exponent >= 16 {
		mantissa := digits[:1]
		if len(digits) > 1 {
			mantissa += "." + digits[1:]
		}
		mark := "+"
		if exponent < 0 {
			mark, exponent = "-", -exponent
		}
		text := strconv.Itoa(exponent)
		if len(text) < 2 {
			text = "0" + text
		}
		return sign + mantissa + "e" + mark + text
	}
	if exponent < 0 {
		return sign + "0." + strings.Repeat("0", -exponent-1) + digits
	}
	if len(digits) <= exponent+1 {
		return sign + digits + strings.Repeat("0", exponent+1-len(digits)) + ".0"
	}
	return sign + digits[:exponent+1] + "." + digits[exponent+1:]
}

func formatNumber(v Value) string {
	switch n := v.(type) {
	case *big.Int:
		return n.String()
	case float64:
		return formatFloat(n)
	}
	return ""
}

// asWhole gives a number as a whole one when it is whole, as int(x) does.
func asWhole(v Value) (*big.Int, bool) {
	switch n := v.(type) {
	case *big.Int:
		return n, true
	case float64:
		if math.IsInf(n, 0) || math.IsNaN(n) || n != math.Trunc(n) {
			return nil, false
		}
		whole, _ := new(big.Float).SetFloat64(n).Int(nil)
		return whole, true
	}
	return nil, false
}

func bigInt(n int) *big.Int { return big.NewInt(int64(n)) }

// roundTo9 rounds a decimal to nine places, to the nearest even on a tie,
// as the reference engine's round(x, 9) does.
func roundTo9(f float64) float64 {
	if math.IsInf(f, 0) || math.IsNaN(f) {
		return f
	}
	text := strconv.FormatFloat(f, 'f', 9, 64)
	rounded, _ := strconv.ParseFloat(text, 64)
	return rounded
}

func arithmetic(op string, a, b Value) Value {
	ai, aWhole := a.(*big.Int)
	bi, bWhole := b.(*big.Int)
	if op == "/" {
		if compareNumbers(b, bigInt(0)) == 0 {
			runFail("Division by zero")
		}
		if aWhole && bWhole {
			f, _ := new(big.Rat).SetFrac(ai, bi).Float64()
			return f
		}
		return toFloat(a) / toFloat(b)
	}
	if op == "%" && compareNumbers(b, bigInt(0)) == 0 {
		runFail("Remainder by zero")
	}
	if aWhole && bWhole {
		out := new(big.Int)
		switch op {
		case "+":
			return out.Add(ai, bi)
		case "-":
			return out.Sub(ai, bi)
		case "*":
			return out.Mul(ai, bi)
		case "%":
			// Floored: the result takes the sign of the divisor.
			out.Mod(ai, bi)
			if out.Sign() != 0 && bi.Sign() < 0 {
				out.Add(out, bi)
			}
			return out
		}
	}
	af, bf := toFloat(a), toFloat(b)
	switch op {
	case "+":
		return af + bf
	case "-":
		return af - bf
	case "*":
		return af * bf
	case "%":
		r := math.Mod(af, bf)
		if r != 0 && (r < 0) != (bf < 0) {
			r += bf
		}
		return r
	}
	runFail("Unknown binary operator '" + op + "'")
	return nil
}
