package main

// What the checker knows about types: how a type is written, which may be
// stored where, the types of the built-in methods, and how a near-miss name
// is suggested. Each rule is the reference engine's, in
// kosika/function_types.py and kosika/builtin_methods.py.

import (
	"fmt"
	"sort"
	"strings"
	"unicode"
)

// never is the type of break, continue, return and fail, which give no
// value and so agree with whatever the other branch of a choice gives.
const never = "Never"

var primitiveTypes = map[string]bool{"Text": true, "Number": true, "Bool": true, "Null": true}

func isFunctionType(t string) bool { return strings.HasPrefix(t, "fn(") }

// signature splits fn(a:Number,b:Text)->Text into its parameters and result.
func signature(t string) ([]Param, string, bool) {
	if !isFunctionType(t) {
		return nil, "", false
	}
	depth, start := 1, 3
	var parts []string
	for index := 3; index < len(t); index++ {
		switch t[index] {
		case '(':
			depth++
		case ')':
			depth--
			if depth == 0 {
				if t[start:index] != "" {
					parts = append(parts, t[start:index])
				}
				if !strings.HasPrefix(t[index+1:], "->") {
					checkFail("Invalid function type")
				}
				params := make([]Param, len(parts))
				for i, part := range parts {
					name, typeName, _ := strings.Cut(part, ":")
					params[i] = Param{name, typeName}
				}
				return params, t[index+3:], true
			}
		case ',':
			if depth == 1 {
				parts = append(parts, t[start:index])
				start = index + 1
			}
		}
	}
	checkFail("Invalid function type")
	return nil, "", false
}

func functionType(params []Param, out string) string {
	parts := make([]string, len(params))
	for i, param := range params {
		parts[i] = param.Name + ":" + param.Type
	}
	return "fn(" + strings.Join(parts, ",") + ")->" + out
}

func isOptional(t string) bool { return strings.HasSuffix(t, "?") && !isFunctionType(t) }
func isMap(t string) bool      { return strings.HasSuffix(t, "{}") && !isFunctionType(t) }
func isArray(t string) bool    { return strings.HasSuffix(t, "[]") && !isFunctionType(t) }

// assignable: types match exactly, except that T? also takes T and null.
func assignable(actual, expected string) bool {
	if actual == expected {
		return true
	}
	// A shape accepts every record that is it, alone, in a list or
	// optional.
	if shapeRecords[expected][actual] {
		return true
	}
	if strings.HasSuffix(actual, "[]") && strings.HasSuffix(expected, "[]") && shapeRecords[expected[:len(expected)-2]] != nil &&
		assignable(actual[:len(actual)-2], expected[:len(expected)-2]) {
		return true
	}
	return isOptional(expected) && (actual == "Null" || actual == expected[:len(expected)-1] || assignable(actual, expected[:len(expected)-1]))
}

// unify is the one type both branches of a choice give, or "".
func unify(left, right string) string {
	if left == right {
		return left
	}
	if left == never {
		return right
	}
	if right == never {
		return left
	}
	for _, pair := range [][2]string{{left, right}, {right, left}} {
		first, second := pair[0], pair[1]
		if assignable(first, second) {
			return second
		}
		if first == "Null" && second != "Null" && !isFunctionType(second) {
			if isOptional(second) {
				return second
			}
			return second + "?"
		}
	}
	return ""
}

// baseType is the named type left after every list, map and optional suffix.
func baseType(t string) string {
	for {
		switch {
		case strings.HasSuffix(t, "?"):
			t = t[:len(t)-1]
		case strings.HasSuffix(t, "[]"), strings.HasSuffix(t, "{}"):
			t = t[:len(t)-2]
		default:
			return t
		}
	}
}

func referencedTypes(t string, into map[string]bool) {
	if params, out, ok := signature(t); ok {
		referencedTypes(out, into)
		for _, param := range params {
			referencedTypes(param.Type, into)
		}
		return
	}
	into[baseType(t)] = true
}

// pyRepr writes text as Python's repr does, which is how the reference
// quotes a name in a message.
func pyRepr(text string) string {
	quote := '\''
	if strings.ContainsRune(text, '\'') && !strings.ContainsRune(text, '"') {
		quote = '"'
	}
	var out strings.Builder
	out.WriteRune(quote)
	for _, r := range text {
		switch {
		case r == '\\':
			out.WriteString(`\\`)
		case r == quote:
			out.WriteRune('\\')
			out.WriteRune(r)
		case r == '\n':
			out.WriteString(`\n`)
		case r == '\r':
			out.WriteString(`\r`)
		case r == '\t':
			out.WriteString(`\t`)
		case r < 0x20 || r == 0x7f:
			fmt.Fprintf(&out, `\x%02x`, r)
		case r > 0x7f && !unicode.IsPrint(r):
			switch {
			case r <= 0xff:
				fmt.Fprintf(&out, `\x%02x`, r)
			case r <= 0xffff:
				fmt.Fprintf(&out, `\u%04x`, r)
			default:
				fmt.Fprintf(&out, `\U%08x`, r)
			}
		default:
			out.WriteRune(r)
		}
	}
	out.WriteRune(quote)
	return out.String()
}

// pyList writes a list of names as Python's repr does: ['a', 'b'].
func pyList(names []string) string {
	quoted := make([]string, len(names))
	for i, name := range names {
		quoted[i] = pyRepr(name)
	}
	return "[" + strings.Join(quoted, ", ") + "]"
}

// similarity is difflib's SequenceMatcher(None, a, b).ratio().
func similarity(a, b []rune) float64 {
	total := len(a) + len(b)
	if total == 0 {
		return 1
	}
	where := map[rune][]int{}
	for j, r := range b {
		where[r] = append(where[r], j)
	}
	var matched func(alo, ahi, blo, bhi int) int
	matched = func(alo, ahi, blo, bhi int) int {
		besti, bestj, bestsize := alo, blo, 0
		lengths := map[int]int{}
		for i := alo; i < ahi; i++ {
			next := map[int]int{}
			for _, j := range where[a[i]] {
				if j < blo {
					continue
				}
				if j >= bhi {
					break
				}
				k := lengths[j-1] + 1
				next[j] = k
				if k > bestsize {
					besti, bestj, bestsize = i-k+1, j-k+1, k
				}
			}
			lengths = next
		}
		for besti > alo && bestj > blo && a[besti-1] == b[bestj-1] {
			besti, bestj, bestsize = besti-1, bestj-1, bestsize+1
		}
		for besti+bestsize < ahi && bestj+bestsize < bhi && a[besti+bestsize] == b[bestj+bestsize] {
			bestsize++
		}
		if bestsize == 0 {
			return 0
		}
		found := bestsize
		if alo < besti && blo < bestj {
			found += matched(alo, besti, blo, bestj)
		}
		if besti+bestsize < ahi && bestj+bestsize < bhi {
			found += matched(besti+bestsize, ahi, bestj+bestsize, bhi)
		}
		return found
	}
	return 2 * float64(matched(0, len(a), 0, len(b))) / float64(total)
}

// didYouMean is a trailing hint naming the closest known spelling, or "".
// It is difflib.get_close_matches(name, candidates, n=1, cutoff=0.7).
func didYouMean(name string, candidates []string) string {
	seen := map[string]bool{}
	var unique []string
	for _, candidate := range candidates {
		if !seen[candidate] {
			seen[candidate] = true
			unique = append(unique, candidate)
		}
	}
	sort.Strings(unique)
	word := []rune(name)
	best, bestScore := "", -1.0
	for _, candidate := range unique {
		score := similarity([]rune(candidate), word)
		if score < 0.7 {
			continue
		}
		if score > bestScore || (score == bestScore && candidate > best) {
			best, bestScore = candidate, score
		}
	}
	if bestScore < 0 || best == name {
		return ""
	}
	return " Did you mean " + pyRepr(best) + "?"
}

// The built-in methods, as the checker sees them. The receiver is a type,
// or one of the kinds below; a parameter or result may name the receiver's
// own type or its element.
const (
	anyArray      = "$array"
	anyOptional   = "$optional"
	anyMap        = "$map"
	element       = "$element"
	receiver      = "$receiver"
	fieldSortable = "$field:Number|Text"
	fieldNumber   = "$field:Number"
	fieldText     = "$field:Text"
)

type methodType struct {
	Receiver string
	Name     string
	Params   []Param
	Returns  string
}

func m(receiverType, name, returns string, params ...string) methodType {
	var list []Param
	for i := 0; i+1 < len(params); i += 2 {
		list = append(list, Param{params[i], params[i+1]})
	}
	return methodType{receiverType, name, list, returns}
}

// methodTypes is in the reference's order, which decides which of two
// methods of one name a receiver finds first.
var methodTypes = []methodType{
	m("Text", "length", "Number"),
	m("Text", "upper", "Text"),
	m("Text", "lower", "Text"),
	m("Text", "trim", "Text"),
	m("Text", "contains", "Bool", "part", "Text"),
	m("Text", "starts_with", "Bool", "prefix", "Text"),
	m("Text", "ends_with", "Bool", "suffix", "Text"),
	m("Text", "split", "Text[]", "separator", "Text"),
	m("Number", "floor", "Number"),
	m("Number", "ceil", "Number"),
	m("Number", "round", "Number"),
	m(anyArray, "length", "Number"),
	m(anyArray, "append", receiver, "item", element),
	m(anyArray, "concat", receiver, "other", receiver),
	m(anyArray, "reverse", receiver),
	m(anyArray, "unique", receiver),
	m("Number[]", "sort", receiver),
	m("Text[]", "sort", receiver),
	m("Number[]", "sum", "Number"),
	m("Number[]", "min", "Number?"),
	m("Number[]", "max", "Number?"),
	m("Number[]", "average", "Number?"),
	m(anyArray, "sort_by", receiver, "field", fieldSortable),
	m(anyArray, "sum_by", "Number", "field", fieldNumber),
	m(anyArray, "min_by", element+"?", "field", fieldSortable),
	m(anyArray, "max_by", element+"?", "field", fieldSortable),
	m(anyArray, "group_by", receiver+"{}", "field", fieldText),
	m(anyArray, "slice", receiver, "start", "Number", "end", "Number"),
	m("Text", "slice", "Text", "start", "Number", "end", "Number"),
	m("Number", "to_text", "Text"),
	m("Number", "to", "Number[]", "end", "Number"),
	m("Number", "until", "Number[]", "end", "Number"),
	m(anyArray, "set", receiver, "key", "Number", "value", element),
	m("Number", "min", "Number", "other", "Number"),
	m("Number", "max", "Number", "other", "Number"),
	m("Text", "to_number", "Number?"),
	m(anyArray, "first", element+"?"),
	m(anyArray, "last", element+"?"),
	m(anyOptional, "or", element, "default", element),
	m(anyOptional, "exists", "Bool"),
	m(anyOptional, "value", element),
	m(anyMap, "get", element+"?", "key", "Text"),
	m(anyMap, "has", "Bool", "key", "Text"),
	m(anyMap, "set", receiver, "key", "Text", "value", element),
	m(anyMap, "remove", receiver, "key", "Text"),
	m(anyMap, "keys", "Text[]"),
	m(anyMap, "values", element+"[]"),
	m(anyMap, "length", "Number"),
	m(anyArray, "contains", "Bool", "item", element),
	m("Number", "sin", "Number"),
	m("Number", "cos", "Number"),
	m("Number", "sqrt", "Number"),
	m("Number", "abs", "Number"),
	m("Number", "power", "Number", "exponent", "Number"),
	m("Number", "is_whole", "Bool"),
	m(anyArray, "index_of", "Number?", "item", element),
	m("Text", "index_of", "Number?", "part", "Text"),
	m("Text", "chars", "Text[]"),
	m("Text", "lines", "Text[]"),
	m("Text", "pad_left", "Text", "width", "Number", "fill", "Text"),
	m("Text", "pad_right", "Text", "width", "Number", "fill", "Text"),
	m("Text", "is_empty", "Bool"),
	m(anyArray, "is_empty", "Bool"),
	m("Text", "take", "Text", "count", "Number"),
	m(anyArray, "take", receiver, "count", "Number"),
	m("Text", "drop", "Text", "count", "Number"),
	m(anyArray, "drop", receiver, "count", "Number"),
	m("Text", "words", "Text[]"),
	m("Text", "replace", "Text", "old", "Text", "new", "Text"),
	m("Text", "trim_marks", "Text", "marks", "Text"),
	m("Text[]", "join", "Text", "separator", "Text"),
	// Vectors and matrices. The checker knows them; this engine cannot run
	// them yet, and says so when one is called.
	m("Number[]", "plus", "Number[]", "other", "Number[]"),
	m("Number[]", "minus", "Number[]", "other", "Number[]"),
	m("Number[]", "scaled", "Number[]", "by", "Number"),
	m("Number[]", "dot", "Number", "other", "Number[]"),
	m("Number[]", "cross", "Number[]", "other", "Number[]"),
	m("Number[]", "norm", "Number"),
	m("Number[]", "mean", "Number"),
	m("Number", "identity", "Number[][]"),
	m("Number", "zeros", "Number[][]", "columns", "Number"),
	m("Number[][]", "rows", "Number"),
	m("Number[][]", "columns", "Number"),
	m("Number[][]", "transpose", "Number[][]"),
	m("Number[][]", "plus", "Number[][]", "other", "Number[][]"),
	m("Number[][]", "minus", "Number[][]", "other", "Number[][]"),
	m("Number[][]", "scaled", "Number[][]", "by", "Number"),
	m("Number[][]", "times", "Number[][]", "other", "Number[][]"),
	m("Number[][]", "apply", "Number[]", "vector", "Number[]"),
	m("Number[][]", "power", "Number[][]", "exponent", "Number"),
	m("Number[][]", "trace", "Number"),
	m("Number[][]", "determinant", "Number"),
	m("Number[][]", "inverse", "Number[][]"),
	m("Number[][]", "solve", "Number[]", "right", "Number[]"),
	m("Number[][]", "is_reflexive", "Bool"),
	m("Number[][]", "is_symmetric", "Bool"),
	m("Number[][]", "is_antisymmetric", "Bool"),
	m("Number[][]", "is_transitive", "Bool"),
	m("Number[][]", "bool_times", "Number[][]", "other", "Number[][]"),
	m("Number[][]", "closure", "Number[][]"),
	m("Number[][]", "adjacency", "Number[][]", "nodes", "Number"),
	m("Number[][]", "directed_adjacency", "Number[][]", "nodes", "Number"),
	m("Number[][]", "degrees", "Number[]"),
	m("Number[][]", "neighbours", "Number[]", "node", "Number"),
	m("Number[][]", "shortest_path", "Number[]", "start", "Number", "end", "Number"),
	m("Number[][]", "components", "Number[][]"),
	// Text, numbers, lists and dates, as people use them every day.
	m("Text", "capitalize", "Text"),
	m("Text", "title", "Text"),
	m("Text", "count", "Number", "part", "Text"),
	m("Text", "repeat", "Text", "times", "Number"),
	m("Text", "reverse", "Text"),
	m("Number", "clamp", "Number", "low", "Number", "high", "Number"),
	m("Number", "log", "Number"),
	m("Number", "exp", "Number"),
	m("Number", "to_fixed", "Text", "digits", "Number"),
	m(anyArray, "count", "Number", "item", element),
	m("Text", "add_days", "Text", "days", "Number"),
	m("Text", "days_until", "Number", "other", "Text"),
	m("Text", "weekday", "Text"),
}

func methodCandidates(receiverType string) []methodType {
	var found []methodType
	pick := func(kind string) {
		for _, method := range methodTypes {
			if method.Receiver == kind {
				found = append(found, method)
			}
		}
	}
	switch {
	case strings.HasSuffix(receiverType, "?"):
		pick(anyOptional)
	case strings.HasSuffix(receiverType, "{}"):
		pick(anyMap)
	default:
		pick(receiverType)
		if strings.HasSuffix(receiverType, "[]") {
			pick(anyArray)
		}
	}
	return found
}

// resolveMethod finds a receiver type's method, or says what exists instead.
func resolveMethod(receiverType, name string) (methodType, string) {
	candidates := methodCandidates(receiverType)
	for _, method := range candidates {
		if method.Name != name {
			continue
		}
		if (name == "contains" || name == "unique") && method.Receiver == anyArray &&
			!primitiveTypes[receiverType[:len(receiverType)-2]] {
			return method, fmt.Sprintf("%s works on arrays of Text, Number, or Bool, not %s", name, pyRepr(receiverType))
		}
		return method, ""
	}
	names := map[string]bool{}
	var available []string
	for _, method := range candidates {
		if !names[method.Name] {
			names[method.Name] = true
			available = append(available, method.Name)
		}
	}
	sort.Strings(available)
	if len(available) == 0 {
		return methodType{}, fmt.Sprintf("%s values have no methods; methods exist on Text, Number, and arrays", pyRepr(receiverType))
	}
	hint := didYouMean(name, available)
	if strings.HasSuffix(receiverType, "?") {
		hint += " The value may be absent, so reach its own methods through .or(default: ...) or .value() first."
	}
	return methodType{}, fmt.Sprintf("%s has no method %s; available: %s.%s", pyRepr(receiverType), pyRepr(name), strings.Join(available, ", "), hint)
}

// concrete fills a method's receiver and element types in.
func concrete(typeName, receiverType string) string {
	var elementType string
	switch {
	case strings.HasSuffix(receiverType, "?"):
		elementType = receiverType[:len(receiverType)-1]
	case strings.HasSuffix(receiverType, "[]"), strings.HasSuffix(receiverType, "{}"):
		elementType = receiverType[:len(receiverType)-2]
	default:
		elementType = receiverType
	}
	filled := strings.ReplaceAll(typeName, receiver, receiverType)
	filled = strings.ReplaceAll(filled, element, elementType)
	// first() on a list of optionals is still optional once, not twice.
	if strings.HasSuffix(filled, "??") {
		return filled[:len(filled)-1]
	}
	return filled
}

// pyJSONString is Python's json.dumps of a text: every character outside
// ASCII written as \uXXXX, as the reference writes a failed expectation.
func pyJSONString(text string) string {
	var out strings.Builder
	out.WriteByte('"')
	for _, r := range text {
		switch {
		case r == '"':
			out.WriteString(`\"`)
		case r == '\\':
			out.WriteString(`\\`)
		case r == '\n':
			out.WriteString(`\n`)
		case r == '\r':
			out.WriteString(`\r`)
		case r == '\t':
			out.WriteString(`\t`)
		case r == '\b':
			out.WriteString(`\b`)
		case r == '\f':
			out.WriteString(`\f`)
		case r < 0x20 || (r > 0x7e && r <= 0xffff):
			fmt.Fprintf(&out, `\u%04x`, r)
		case r > 0xffff:
			r -= 0x10000
			fmt.Fprintf(&out, `\u%04x\u%04x`, 0xd800+(r>>10), 0xdc00+(r&0x3ff))
		default:
			out.WriteRune(r)
		}
	}
	out.WriteByte('"')
	return out.String()
}
