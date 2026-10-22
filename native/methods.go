package main

// The built-in methods on Text, Number, lists, maps and optional values.
// Each does what the reference engine's method of the same name does,
// limits and failure words included. Vectors and matrices are not here yet.

import (
	"fmt"
	"math"
	"math/big"
	"sort"
	"strings"
	"unicode"
)

const (
	maxArrayLength = 1_000_000
	maxSplitParts  = 100_000
	maxTextLength  = 10_000_000
	maxMapEntries  = 100_000
	maxRangeLength = 1_000_000
	maxPower       = 1000
)

// A method names its parameters, so unnamed arguments can be placed.
type method struct {
	params []string
	apply  func(receiver Value, args []Value) Value
}

func textOf(v Value) string {
	text, ok := v.(string)
	if !ok {
		runFail("a text was needed, not " + hostName(v))
	}
	return text
}

func listOf(v Value) []Value {
	items, ok := v.([]Value)
	if !ok {
		runFail("a list was needed, not " + hostName(v))
	}
	return items
}

func bounded(items []Value) []Value {
	if len(items) > maxArrayLength {
		runFail(fmt.Sprintf("array would exceed %d elements", maxArrayLength))
	}
	return items
}

// count reads a whole number that is not negative, for take and drop.
func count(v Value, name string) int {
	whole, ok := asWhole(v)
	if _, isBool := v.(bool); isBool || !ok || whole.Sign() < 0 {
		runFail(name + " needs a whole number that is not negative")
	}
	if !whole.IsInt64() || whole.Int64() > math.MaxInt32 {
		return math.MaxInt32
	}
	return int(whole.Int64())
}

func slicePositions(start, end Value, length int) (int, int) {
	first, firstOK := asWhole(start)
	last, lastOK := asWhole(end)
	if !firstOK || !lastOK {
		runFail("slice positions must be whole numbers")
	}
	if first.Sign() < 0 || first.Cmp(last) > 0 || last.Cmp(bigInt(length)) > 0 {
		runFail(fmt.Sprintf("slice %s to %s is outside a value of length %d", first, last, length))
	}
	return int(first.Int64()), int(last.Int64())
}

func stringsToValues(parts []string) []Value {
	out := make([]Value, len(parts))
	for i, part := range parts {
		out[i] = part
	}
	return out
}

// splitLines breaks a text at every kind of line end the reference does.
func splitLines(text string) []string {
	lines := []string{}
	runes := []rune(text)
	start := 0
	for i := 0; i < len(runes); i++ {
		r := runes[i]
		isBreak := r == '\n' || r == '\r' || r == '\v' || r == '\f' || r == 0x1c || r == 0x1d || r == 0x1e || r == 0x85 || r == 0x2028 || r == 0x2029
		if !isBreak {
			continue
		}
		lines = append(lines, string(runes[start:i]))
		if r == '\r' && i+1 < len(runes) && runes[i+1] == '\n' {
			i++
		}
		start = i + 1
	}
	if start < len(runes) {
		lines = append(lines, string(runes[start:]))
	}
	return lines
}

func isSpace(r rune) bool {
	return unicode.IsSpace(r) || r == 0x1c || r == 0x1d || r == 0x1e || r == 0x1f
}

func containsValue(items []Value, item Value) bool {
	for _, candidate := range items {
		if valueEqual(candidate, item) {
			return true
		}
	}
	return false
}

func whole(v Value, name string) Value {
	if n, ok := v.(*big.Int); ok {
		return n
	}
	f := v.(float64)
	switch name {
	case "floor":
		f = math.Floor(f)
	case "ceil":
		f = math.Ceil(f)
	default:
		// Half up: 2.5 rounds to 3 and -2.5 to -2.
		f = math.Floor(f + 0.5)
	}
	out, _ := new(big.Float).SetFloat64(f).Int(nil)
	return out
}

// settle gives a decimal result as a whole number when it is one and is
// small enough to be exact, as power and its kin do.
func settle(f float64) Value {
	if f == math.Trunc(f) && math.Abs(f) < 1e15 {
		return big.NewInt(int64(f))
	}
	return f
}

func power(base, exponent Value) Value {
	times, isWhole := asWhole(exponent)
	if isWhole {
		if new(big.Int).Abs(times).Cmp(bigInt(maxPower)) > 0 {
			runFail(fmt.Sprintf("power needs an exponent between -%d and %d", maxPower, maxPower))
		}
		n := int(times.Int64())
		if n < 0 && compareNumbers(base, bigInt(0)) == 0 {
			runFail("0 cannot be raised to a negative power")
		}
		if b, baseWhole := base.(*big.Int); baseWhole {
			if n >= 0 {
				return new(big.Int).Exp(b, bigInt(n), nil)
			}
			denominator, _ := new(big.Float).SetInt(new(big.Int).Exp(b, bigInt(-n), nil)).Float64()
			return settle(roundTo9(1 / denominator))
		}
		result := math.Pow(base.(float64), float64(n))
		if math.IsInf(result, 0) || math.IsNaN(result) {
			runFail("power gives a number too large to hold")
		}
		return settle(roundTo9(result))
	}
	if compareNumbers(base, bigInt(0)) < 0 {
		runFail("power needs a whole exponent when the number is negative")
	}
	result := math.Pow(toFloat(base), toFloat(exponent))
	if math.IsInf(result, 0) || math.IsNaN(result) {
		runFail("power gives a number too large to hold")
	}
	return settle(roundTo9(result))
}

func numberRange(start, end Value, inclusive bool) Value {
	first, firstOK := asWhole(start)
	last, lastOK := asWhole(end)
	if !firstOK || !lastOK {
		runFail("a range needs whole numbers")
	}
	stop := new(big.Int).Set(last)
	if inclusive {
		stop.Add(stop, bigInt(1))
	}
	if new(big.Int).Sub(stop, first).Cmp(bigInt(maxRangeLength)) > 0 {
		runFail(fmt.Sprintf("range would exceed %d numbers", maxRangeLength))
	}
	out := []Value{}
	for n := new(big.Int).Set(first); n.Cmp(stop) < 0; n = new(big.Int).Add(n, bigInt(1)) {
		out = append(out, n)
	}
	return out
}

func sortValues(items []Value, key func(Value) Value) []Value {
	out := append([]Value{}, items...)
	sort.SliceStable(out, func(i, j int) bool {
		a, b := key(out[i]), key(out[j])
		if at, ok := a.(string); ok {
			return compareText(at, textOf(b)) < 0
		}
		return compareNumbers(a, b) < 0
	})
	return out
}

func sum(items []Value) Value {
	var total Value = bigInt(0)
	for _, item := range items {
		total = arithmetic("+", total, item)
	}
	return total
}

func field(item Value, name string) Value {
	record, ok := item.(*Record)
	if !ok {
		runFail("a record was needed, not " + hostName(item))
	}
	return record.Fields[name]
}

func runeIndex(text, part string) int {
	at := strings.Index(text, part)
	if at < 0 {
		return -1
	}
	return len([]rune(text[:at]))
}

var textMethods = map[string]method{
	"length": {nil, func(r Value, a []Value) Value { return bigInt(len([]rune(textOf(r)))) }},
	"upper":  {nil, func(r Value, a []Value) Value { return strings.ToUpper(textOf(r)) }},
	"lower":  {nil, func(r Value, a []Value) Value { return strings.ToLower(textOf(r)) }},
	"trim":   {nil, func(r Value, a []Value) Value { return strings.TrimFunc(textOf(r), isSpace) }},
	"contains": {[]string{"part"}, func(r Value, a []Value) Value {
		return strings.Contains(textOf(r), textOf(a[0]))
	}},
	"starts_with": {[]string{"prefix"}, func(r Value, a []Value) Value {
		return strings.HasPrefix(textOf(r), textOf(a[0]))
	}},
	"ends_with": {[]string{"suffix"}, func(r Value, a []Value) Value {
		return strings.HasSuffix(textOf(r), textOf(a[0]))
	}},
	"split": {[]string{"separator"}, func(r Value, a []Value) Value {
		separator := textOf(a[0])
		if separator == "" {
			runFail("split needs a non-empty separator")
		}
		parts := strings.Split(textOf(r), separator)
		if len(parts) > maxSplitParts {
			runFail(fmt.Sprintf("split produced more than %d parts", maxSplitParts))
		}
		return stringsToValues(parts)
	}},
	"slice": {[]string{"start", "end"}, func(r Value, a []Value) Value {
		runes := []rune(textOf(r))
		first, last := slicePositions(a[0], a[1], len(runes))
		return string(runes[first:last])
	}},
	"to_number": {nil, func(r Value, a []Value) Value {
		trimmed := strings.TrimFunc(textOf(r), isSpace)
		digits := strings.TrimPrefix(trimmed, "-")
		wholePart, decimals, hasPoint := strings.Cut(digits, ".")
		if !isDigits(wholePart) || (hasPoint && !isDigits(decimals)) {
			return nil
		}
		return numberLiteral(digits, strings.HasPrefix(trimmed, "-"))
	}},
	"index_of": {[]string{"part"}, func(r Value, a []Value) Value {
		part := textOf(a[0])
		if part == "" {
			runFail("index_of needs a non-empty text to look for")
		}
		at := runeIndex(textOf(r), part)
		if at < 0 {
			return nil
		}
		return bigInt(at)
	}},
	"chars": {nil, func(r Value, a []Value) Value {
		runes := []rune(textOf(r))
		if len(runes) > maxSplitParts {
			runFail(fmt.Sprintf("chars produced more than %d parts", maxSplitParts))
		}
		out := make([]Value, len(runes))
		for i, c := range runes {
			out[i] = string(c)
		}
		return out
	}},
	"lines": {nil, func(r Value, a []Value) Value {
		return bounded(stringsToValues(splitLines(textOf(r))))
	}},
	"pad_left":  {[]string{"width", "fill"}, func(r Value, a []Value) Value { return pad(textOf(r), a[0], a[1], true) }},
	"pad_right": {[]string{"width", "fill"}, func(r Value, a []Value) Value { return pad(textOf(r), a[0], a[1], false) }},
	"is_empty":  {nil, func(r Value, a []Value) Value { return textOf(r) == "" }},
	"take": {[]string{"count"}, func(r Value, a []Value) Value {
		runes := []rune(textOf(r))
		n := count(a[0], "take")
		if n > len(runes) {
			n = len(runes)
		}
		return string(runes[:n])
	}},
	"drop": {[]string{"count"}, func(r Value, a []Value) Value {
		runes := []rune(textOf(r))
		n := count(a[0], "drop")
		if n > len(runes) {
			n = len(runes)
		}
		return string(runes[n:])
	}},
	"words": {nil, func(r Value, a []Value) Value {
		parts := strings.FieldsFunc(textOf(r), isSpace)
		if len(parts) > maxSplitParts {
			runFail(fmt.Sprintf("words produced more than %d parts", maxSplitParts))
		}
		return stringsToValues(parts)
	}},
	"replace": {[]string{"old", "new"}, func(r Value, a []Value) Value {
		old := textOf(a[0])
		if old == "" {
			runFail("replace needs a non-empty text to look for")
		}
		replaced := strings.ReplaceAll(textOf(r), old, textOf(a[1]))
		if len([]rune(replaced)) > maxTextLength {
			runFail(fmt.Sprintf("replace would exceed %d characters", maxTextLength))
		}
		return replaced
	}},
	"trim_marks": {[]string{"marks"}, func(r Value, a []Value) Value {
		return strings.Trim(textOf(r), textOf(a[0])+" \t\r\n")
	}},
}

func pad(text string, width, fill Value, left bool) Value {
	name := "pad_right"
	if left {
		name = "pad_left"
	}
	size, ok := asWhole(width)
	if !ok || size.Sign() < 0 {
		runFail(name + " needs a whole width that is not negative")
	}
	with := []rune(textOf(fill))
	if len(with) != 1 {
		runFail(name + " needs exactly one character to fill with")
	}
	if size.Cmp(bigInt(maxTextLength)) > 0 {
		runFail(fmt.Sprintf("%s would exceed %d characters", name, maxTextLength))
	}
	missing := int(size.Int64()) - len([]rune(text))
	if missing <= 0 {
		return text
	}
	padding := strings.Repeat(string(with), missing)
	if left {
		return padding + text
	}
	return text + padding
}

var numberMethods = map[string]method{
	"floor":   {nil, func(r Value, a []Value) Value { return whole(r, "floor") }},
	"ceil":    {nil, func(r Value, a []Value) Value { return whole(r, "ceil") }},
	"round":   {nil, func(r Value, a []Value) Value { return whole(r, "round") }},
	"to_text": {nil, func(r Value, a []Value) Value { return formatNumber(r) }},
	"to":      {[]string{"end"}, func(r Value, a []Value) Value { return numberRange(r, a[0], true) }},
	"until":   {[]string{"end"}, func(r Value, a []Value) Value { return numberRange(r, a[0], false) }},
	"min": {[]string{"other"}, func(r Value, a []Value) Value {
		if compareNumbers(a[0], r) < 0 {
			return a[0]
		}
		return r
	}},
	"max": {[]string{"other"}, func(r Value, a []Value) Value {
		if compareNumbers(a[0], r) > 0 {
			return a[0]
		}
		return r
	}},
	"sin": {nil, func(r Value, a []Value) Value { return roundTo9(math.Sin(toFloat(r)*math.Pi/180)) + 0 }},
	"cos": {nil, func(r Value, a []Value) Value { return roundTo9(math.Cos(toFloat(r)*math.Pi/180)) + 0 }},
	"sqrt": {nil, func(r Value, a []Value) Value {
		if compareNumbers(r, bigInt(0)) < 0 {
			runFail("sqrt needs a number that is not negative")
		}
		return roundTo9(math.Sqrt(toFloat(r)))
	}},
	"abs": {nil, func(r Value, a []Value) Value {
		if n, ok := r.(*big.Int); ok {
			return new(big.Int).Abs(n)
		}
		return math.Abs(r.(float64))
	}},
	"power": {[]string{"exponent"}, func(r Value, a []Value) Value { return power(r, a[0]) }},
	"is_whole": {nil, func(r Value, a []Value) Value {
		_, ok := asWhole(r)
		return ok
	}},
}

var listMethods = map[string]method{
	"length": {nil, func(r Value, a []Value) Value { return bigInt(len(listOf(r))) }},
	"append": {[]string{"item"}, func(r Value, a []Value) Value {
		return bounded(append(append([]Value{}, listOf(r)...), a[0]))
	}},
	"concat": {[]string{"other"}, func(r Value, a []Value) Value {
		return bounded(append(append([]Value{}, listOf(r)...), listOf(a[0])...))
	}},
	"reverse": {nil, func(r Value, a []Value) Value {
		items := listOf(r)
		out := make([]Value, len(items))
		for i, item := range items {
			out[len(items)-1-i] = item
		}
		return out
	}},
	"unique": {nil, func(r Value, a []Value) Value {
		kept := []Value{}
		for _, item := range listOf(r) {
			if !containsValue(kept, item) {
				kept = append(kept, item)
			}
		}
		return kept
	}},
	"sort": {nil, func(r Value, a []Value) Value { return sortValues(listOf(r), func(v Value) Value { return v }) }},
	"sum":  {nil, func(r Value, a []Value) Value { return sum(listOf(r)) }},
	"min": {nil, func(r Value, a []Value) Value {
		var best Value
		for _, item := range listOf(r) {
			if best == nil || compareNumbers(item, best) < 0 {
				best = item
			}
		}
		return best
	}},
	"max": {nil, func(r Value, a []Value) Value {
		var best Value
		for _, item := range listOf(r) {
			if best == nil || compareNumbers(item, best) > 0 {
				best = item
			}
		}
		return best
	}},
	"average": {nil, func(r Value, a []Value) Value {
		items := listOf(r)
		if len(items) == 0 {
			return nil
		}
		return arithmetic("/", sum(items), bigInt(len(items)))
	}},
	"sort_by": {[]string{"field"}, func(r Value, a []Value) Value {
		name := textOf(a[0])
		return sortValues(listOf(r), func(v Value) Value { return field(v, name) })
	}},
	"sum_by": {[]string{"field"}, func(r Value, a []Value) Value {
		var total Value = bigInt(0)
		for _, item := range listOf(r) {
			total = arithmetic("+", total, field(item, textOf(a[0])))
		}
		return total
	}},
	"min_by": {[]string{"field"}, func(r Value, a []Value) Value { return best(listOf(r), textOf(a[0]), false) }},
	"max_by": {[]string{"field"}, func(r Value, a []Value) Value { return best(listOf(r), textOf(a[0]), true) }},
	"group_by": {[]string{"field"}, func(r Value, a []Value) Value {
		groups := newRecord()
		for _, item := range listOf(r) {
			key := textOf(field(item, textOf(a[0])))
			had, _ := groups.Fields[key].([]Value)
			groups.set(key, append(append([]Value{}, had...), item))
		}
		return groups
	}},
	"slice": {[]string{"start", "end"}, func(r Value, a []Value) Value {
		items := listOf(r)
		first, last := slicePositions(a[0], a[1], len(items))
		return append([]Value{}, items[first:last]...)
	}},
	"set": {[]string{"key", "value"}, func(r Value, a []Value) Value {
		items := listOf(r)
		key, ok := asWhole(a[0])
		if !ok || key.Sign() < 0 || key.Cmp(bigInt(len(items))) >= 0 {
			runFail(fmt.Sprintf("position %s is outside an array of length %d", formatNumber(a[0]), len(items)))
		}
		updated := append([]Value{}, items...)
		updated[int(key.Int64())] = a[1]
		return updated
	}},
	"first": {nil, func(r Value, a []Value) Value {
		if items := listOf(r); len(items) > 0 {
			return items[0]
		}
		return nil
	}},
	"last": {nil, func(r Value, a []Value) Value {
		if items := listOf(r); len(items) > 0 {
			return items[len(items)-1]
		}
		return nil
	}},
	"contains": {[]string{"item"}, func(r Value, a []Value) Value { return containsValue(listOf(r), a[0]) }},
	"index_of": {[]string{"item"}, func(r Value, a []Value) Value {
		for position, candidate := range listOf(r) {
			if valueEqual(candidate, a[0]) {
				return bigInt(position)
			}
		}
		return nil
	}},
	"is_empty": {nil, func(r Value, a []Value) Value { return len(listOf(r)) == 0 }},
	"take": {[]string{"count"}, func(r Value, a []Value) Value {
		items := listOf(r)
		n := count(a[0], "take")
		if n > len(items) {
			n = len(items)
		}
		return append([]Value{}, items[:n]...)
	}},
	"drop": {[]string{"count"}, func(r Value, a []Value) Value {
		items := listOf(r)
		n := count(a[0], "drop")
		if n > len(items) {
			n = len(items)
		}
		return append([]Value{}, items[n:]...)
	}},
	"join": {[]string{"separator"}, func(r Value, a []Value) Value {
		items := listOf(r)
		parts := make([]string, len(items))
		for i, item := range items {
			parts[i] = textOf(item)
		}
		return strings.Join(parts, textOf(a[0]))
	}},
}

// best gives the item whose field is smallest or largest, the first of equals.
func best(items []Value, name string, largest bool) Value {
	var pick Value
	for _, item := range items {
		if pick == nil {
			pick = item
			continue
		}
		a, b := field(item, name), field(pick, name)
		order := 0
		if text, ok := a.(string); ok {
			order = compareText(text, textOf(b))
		} else {
			order = compareNumbers(a, b)
		}
		if (largest && order > 0) || (!largest && order < 0) {
			pick = item
		}
	}
	return pick
}

var mapMethods = map[string]method{
	"get": {[]string{"key"}, func(r Value, a []Value) Value { return r.(*Record).Fields[textOf(a[0])] }},
	"has": {[]string{"key"}, func(r Value, a []Value) Value {
		_, has := r.(*Record).Fields[textOf(a[0])]
		return has
	}},
	"set": {[]string{"key", "value"}, func(r Value, a []Value) Value {
		updated := r.(*Record).copy()
		updated.set(textOf(a[0]), a[1])
		if len(updated.Keys) > maxMapEntries {
			runFail(fmt.Sprintf("map would exceed %d entries", maxMapEntries))
		}
		return updated
	}},
	"remove": {[]string{"key"}, func(r Value, a []Value) Value {
		out := newRecord()
		entries := r.(*Record)
		for _, key := range entries.Keys {
			if key != textOf(a[0]) {
				out.set(key, entries.Fields[key])
			}
		}
		return out
	}},
	"keys": {nil, func(r Value, a []Value) Value { return stringsToValues(r.(*Record).Keys) }},
	"values": {nil, func(r Value, a []Value) Value {
		entries := r.(*Record)
		out := make([]Value, len(entries.Keys))
		for i, key := range entries.Keys {
			out[i] = entries.Fields[key]
		}
		return out
	}},
	"length": {nil, func(r Value, a []Value) Value { return bigInt(len(r.(*Record).Keys)) }},
}

// On an optional value, which is any value or null.
var optionalMethods = map[string]method{
	"or": {[]string{"default"}, func(r Value, a []Value) Value {
		if r == nil {
			return a[0]
		}
		return r
	}},
	"exists": {nil, func(r Value, a []Value) Value { return r != nil }},
	"value": {nil, func(r Value, a []Value) Value {
		if r == nil {
			runFail("value() was called on an absent value")
		}
		return r
	}},
}

// methodFor finds a method by what the receiver is. A method of the
// optional kind is found on any value, as the checker allows it on T?.
func methodFor(receiver Value, name string) (method, bool) {
	var table map[string]method
	switch receiver.(type) {
	case string:
		table = textMethods
	case *big.Int, float64:
		table = numberMethods
	case []Value:
		table = listMethods
	case *Record:
		table = mapMethods
	}
	if found, has := table[name]; has {
		return found, true
	}
	found, has := optionalMethods[name]
	return found, has
}

func (m *Machine) callMethod(e *MethodExpr, at frame) Value {
	receiver := m.eval(e.Target, at)
	if record, ok := receiver.(*Record); ok {
		if _, isMapMethod := mapMethods[e.Name]; !isMapMethod {
			// A record's own function. Which record it is, is told from its fields.
			for _, decl := range m.records {
				if !hasName(decl.Methods, e.Name) || len(decl.Fields) != len(record.Fields) {
					continue
				}
				same := true
				for _, f := range decl.Fields {
					if _, has := record.Fields[f.Name]; !has {
						same = false
					}
				}
				if !same {
					continue
				}
				function := m.functions[decl.Name+"$"+e.Name]
				arguments := map[string]Value{"self": receiver}
				argumentEvents := m.collect(func() {
					for _, argument := range resolveUnnamed(e.Args, paramNames(function)[1:]) {
						arguments[argument.Name] = m.eval(argument.Value, at)
					}
				})
				return m.callTraced(function, arguments, map[string]Value{}, nil, at.depth+1, argumentEvents)
			}
		}
	}
	found, has := methodFor(receiver, e.Name)
	if !has {
		runFail(fmt.Sprintf("kosika-native has no method %s on %s yet (line %d)", quoted(e.Name), hostName(receiver), e.Line))
	}
	given := map[string]Value{}
	for _, argument := range resolveUnnamed(e.Args, found.params) {
		given[argument.Name] = m.eval(argument.Value, at)
	}
	if len(given) != len(found.params) {
		runFail(fmt.Sprintf("Method %s takes %d argument(s), and was given %d", quoted(e.Name), len(found.params), len(given)))
	}
	args := make([]Value, len(found.params))
	for i, name := range found.params {
		value, ok := given[name]
		if !ok {
			runFail(fmt.Sprintf("Method %s needs the argument %s", quoted(e.Name), quoted(name)))
		}
		args[i] = value
	}
	return found.apply(receiver, args)
}

func hasName(names []string, name string) bool {
	for _, candidate := range names {
		if candidate == name {
			return true
		}
	}
	return false
}
