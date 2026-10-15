package main

// The checker: a program whose types do not agree is refused before any of
// it runs. It follows the reference engine's kosika/checker.py rule for
// rule, message for message, and the test checker in kosika/testing.py.
//
// The reference checks a program after it has lowered nested functions into
// top-level ones that take what they capture as extra parameters. This
// engine does not lower them; it checks a nested function where it stands,
// seeing what the lowered one would see, and names it as the reference
// does: outer/inner.

import (
	"fmt"
	"math/big"
	"path/filepath"
	"sort"
	"strings"
)

type CheckError struct{ Message string }

func (e *CheckError) Error() string { return e.Message }

func checkFail(format string, args ...any) {
	panic(&CheckError{fmt.Sprintf(format, args...)})
}

// Env is what a name means where an expression stands.
type Env struct {
	types   map[string]string
	shown   map[string]string // a captured name, as the reference spells it
	mutable map[string]bool
	present map[string]bool // optional names a test has proved to hold a value
	returns string          // the result type of the function, "" outside one
	local   map[string]string
}

func newEnv() *Env {
	return &Env{types: map[string]string{}, shown: map[string]string{}, mutable: map[string]bool{}, present: map[string]bool{}, local: map[string]string{}}
}

func copyMap[V any](source map[string]V) map[string]V {
	out := make(map[string]V, len(source))
	for key, value := range source {
		out[key] = value
	}
	return out
}

func (e *Env) child() *Env {
	return &Env{copyMap(e.types), copyMap(e.shown), copyMap(e.mutable), copyMap(e.present), e.returns, e.local}
}

func (e *Env) narrowed(names []string) *Env {
	scope := e.child()
	scope.provePresent(names)
	return scope
}

func (e *Env) provePresent(names []string) {
	for _, name := range names {
		t := e.types[name]
		e.types[name] = t[:len(t)-1]
		e.present[name] = true
	}
}

// names are the bindings a "did you mean" may offer.
func (e *Env) names() []string {
	var out []string
	for name := range e.types {
		if shown, ok := e.shown[name]; ok {
			name = shown
		}
		out = append(out, name)
	}
	return out
}

type checker struct {
	program   *Program
	records   map[string]*RecordDecl
	functions map[string]*FuncDecl // by the reference's name: outer/inner
	order     []string
	locals    map[*FuncDecl]map[string]string
	captures  map[*FuncDecl][]capture
	models    map[string]*ModelDecl
	tools     map[string]*ToolDecl
	agents    map[string]*AgentDecl
	workflows map[string]*WorkflowDecl
}

// A capture is a name a nested function sees from the functions around it.
type capture struct {
	Name, Type, Shown string
}

func union(a, b []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, list := range [][]string{a, b} {
		for _, name := range list {
			if !seen[name] {
				seen[name] = true
				out = append(out, name)
			}
		}
	}
	return out
}

// provedPresent gives the names a condition proves hold a value: when it
// is true, and when it is false. Only a plain name that cannot be set again
// counts.
func provedPresent(condition Expr, env *Env) (whenTrue, whenFalse []string) {
	switch c := condition.(type) {
	case *MethodExpr:
		target, ok := c.Target.(*VarExpr)
		if c.Name != "exists" || len(c.Args) > 0 || !ok {
			return nil, nil
		}
		t, known := env.types[target.Name]
		if !env.mutable[target.Name] && !env.present[target.Name] && known && isOptional(t) {
			return []string{target.Name}, nil
		}
	case *UnaryExpr:
		if c.Op == "not" {
			t, f := provedPresent(c.Operand, env)
			return f, t
		}
	case *BinaryExpr:
		if c.Op == "and" || c.Op == "or" {
			leftTrue, leftFalse := provedPresent(c.Left, env)
			rightTrue, rightFalse := provedPresent(c.Right, env)
			if c.Op == "and" {
				return union(leftTrue, rightTrue), nil
			}
			return nil, union(leftFalse, rightFalse)
		}
	}
	return nil, nil
}

// alwaysLeaves: working the expression out never goes on to the next line.
func alwaysLeaves(expression Expr) bool {
	switch e := expression.(type) {
	case *ReturnExpr, *BreakExpr, *ContinueExpr, *FailExpr:
		return true
	case *BlockExpr:
		if e.Result != nil {
			return alwaysLeaves(e.Result)
		}
		return len(e.Stmts) > 0 && alwaysLeaves(e.Stmts[len(e.Stmts)-1])
	case *CondExpr:
		return alwaysLeaves(e.Then) && alwaysLeaves(e.Else)
	}
	return false
}

// presentAfter: `if not x.exists() { return ... }` lets the lines after it
// use x directly.
func presentAfter(statement Expr, env *Env) []string {
	conditional, ok := statement.(*CondExpr)
	if !ok {
		return nil
	}
	whenTrue, whenFalse := provedPresent(conditional.Cond, env)
	var names []string
	if alwaysLeaves(conditional.Then) {
		names = union(names, whenFalse)
	}
	if alwaysLeaves(conditional.Else) {
		names = union(names, whenTrue)
	}
	return names
}

func literalType(v Value) string {
	switch v.(type) {
	case nil:
		return "Null"
	case bool:
		return "Bool"
	case string:
		return "Text"
	case *big.Int, float64:
		return "Number"
	}
	checkFail("Unsupported literal value %v", v)
	return ""
}

func (c *checker) recordFields(name string) []Param {
	record := c.records[name]
	if record == nil {
		checkFail("Unknown record type %s", pyRepr(name))
	}
	return record.Fields
}

func fieldType(fields []Param, name string) (string, bool) {
	for _, field := range fields {
		if field.Name == name {
			return field.Type, true
		}
	}
	return "", false
}

func fieldNames(fields []Param) []string {
	names := make([]string, len(fields))
	for i, field := range fields {
		names[i] = field.Name
	}
	return names
}

func (c *checker) validateType(t string) {
	if params, out, ok := signature(t); ok {
		seen := map[string]bool{}
		for _, param := range params {
			if seen[param.Name] {
				checkFail("Duplicate parameter in function type")
			}
			seen[param.Name] = true
		}
		for _, param := range params {
			c.validateType(param.Type)
		}
		c.validateType(out)
		return
	}
	switch {
	case primitiveTypes[t]:
	case strings.HasSuffix(t, "[]"), strings.HasSuffix(t, "{}"):
		c.validateType(t[:len(t)-2])
	case strings.HasSuffix(t, "?"):
		if strings.HasSuffix(t, "??") || t == "Null?" {
			checkFail("Unknown field type %s", pyRepr(t))
		}
		c.validateType(t[:len(t)-1])
	case c.records[t] == nil && c.enumValues(t) == nil && c.shape(t) == nil:
		checkFail("Unknown field type %s", pyRepr(t))
	}
}

func absentHint(t string) string {
	if isMap(t) {
		return "a map is read with .get(key: ...), .has(key: ...), or .keys()"
	}
	return "the value may be absent; use .or(default: ...) to supply a fallback, .exists() to test it, or .value() to require it"
}

// functionNamed finds the function a call names where it is written: one
// declared in the function around it first, then one at the top.
func (c *checker) functionNamed(name string, env *Env) (string, *FuncDecl) {
	if qualified, ok := env.local[name]; ok {
		return qualified, c.functions[qualified]
	}
	return name, c.functions[name]
}

// publicParams are a function's parameters, without what it captures.
func publicParams(decl *FuncDecl) []string {
	if decl == nil || len(decl.Params) == 0 {
		return nil
	}
	return paramNames(decl)
}

func (c *checker) typeOf(expression Expr, env *Env, step, expected string) string {
	// A literal written where T? is wanted is typed as the plain T; the
	// caller's assignability check then accepts it.
	if isOptional(expected) {
		expected = expected[:len(expected)-1]
	}
	switch e := expression.(type) {
	case *Assign:
		target, known := env.types[e.Target]
		if !known {
			checkFail("Step %s assigns to unknown variable %s", pyRepr(step), pyRepr(e.Target))
		}
		if !env.mutable[e.Target] {
			checkFail("Step %s cannot assign to %s: declare it with 'var' to make it assignable", pyRepr(step), pyRepr(e.Target))
		}
		assigned := c.typeOf(e.Value, env, step, target)
		if !assignable(assigned, target) {
			checkFail("Step %s assigns %s to %s of type %s", pyRepr(step), pyRepr(assigned), pyRepr(e.Target), pyRepr(target))
		}
		return target
	case *BlockExpr:
		inner := env.child()
		for _, statement := range e.Stmts {
			if binding, ok := statement.(*Binding); ok {
				if _, taken := env.types[binding.Name]; taken {
					checkFail("Step %s local %s shadows an enclosing binding", pyRepr(step), pyRepr(binding.Name))
				}
				if binding.Type != "" {
					c.validateType(binding.Type)
				}
				declared := c.typeOf(binding.Value, inner, step, binding.Type)
				if binding.Type != "" && declared != binding.Type {
					checkFail("Step %s local %s expects %s, got %s", pyRepr(step), pyRepr(binding.Name), pyRepr(binding.Type), pyRepr(declared))
				}
				inner.types[binding.Name] = declared
				delete(inner.shown, binding.Name)
				if binding.Mutable {
					inner.mutable[binding.Name] = true
				}
				continue
			}
			c.typeOf(statement, inner, step, "")
			inner.provePresent(presentAfter(statement, inner))
		}
		if e.Result == nil {
			return "Null"
		}
		return c.typeOf(e.Result, inner, step, expected)
	case *MatchExpr:
		subject := c.typeOf(e.Subject, env, step, "")
		values := c.enumValues(subject)
		if !primitiveTypes[subject] && values == nil {
			checkFail("Match subjects must be scalar values")
		}
		var seen []Value
		exhaustive := false
		result := expected
		for _, arm := range e.Arms {
			if exhaustive {
				checkFail("Unreachable match arm")
			}
			if arm.Any {
				exhaustive = true
			} else {
				if values != nil {
					// An enum is matched by the text of its values.
					text, isText := arm.Pattern.(string)
					if !isText || !hasName(values, text) {
						checkFail("Enum %s has no value %s; it has: %s", pyRepr(subject), pyReprAny(arm.Pattern), strings.Join(values, ", "))
					}
				} else if literalType(arm.Pattern) != subject {
					checkFail("Match pattern type differs from subject")
				}
				for _, earlier := range seen {
					if valueEqual(earlier, arm.Pattern) {
						checkFail("Duplicate match pattern")
					}
				}
				seen = append(seen, arm.Pattern)
				exhaustive = subject == "Null" || (subject == "Bool" && len(seen) == 2) || (values != nil && len(seen) == len(values))
			}
			armType := c.typeOf(arm.Value, env, step, result)
			merged := armType
			if result != "" {
				merged = unify(result, armType)
			}
			if merged == "" {
				checkFail("Match branches must have the same result type")
			}
			result = merged
		}
		if !exhaustive || result == "" {
			checkFail("Non-exhaustive match: add a final case _")
		}
		return result
	case *Literal:
		return literalType(e.Value)
	case *VarExpr:
		t, known := env.types[e.Name]
		if !known {
			checkFail("Step %s references unknown variable %s.%s", pyRepr(step), pyRepr(e.Name), didYouMean(e.Name, env.names()))
		}
		return t
	case *BreakExpr, *ContinueExpr:
		return never
	case *ReturnExpr:
		if env.returns == "" {
			checkFail("Step %s uses return outside a function body", pyRepr(step))
		}
		returned := c.typeOf(e.Value, env, step, env.returns)
		if !assignable(returned, env.returns) {
			checkFail("Step %s early return expects %s, got %s", pyRepr(step), pyRepr(env.returns), pyRepr(returned))
		}
		return never
	case *RepeatExpr:
		count := c.typeOf(e.Count, env, step, "")
		if count != "Number" {
			checkFail("Step %s repeat count expects 'Number', got %s", pyRepr(step), pyRepr(count))
		}
		repeated := c.typeOf(e.Body, env, step, "")
		if repeated == never {
			return "Null"
		}
		return repeated
	case *ForExpr:
		sequence := c.typeOf(e.Seq, env, step, "")
		if !strings.HasSuffix(sequence, "[]") || isFunctionType(sequence) {
			checkFail("Step %s for loop requires an array, got %s", pyRepr(step), pyRepr(sequence))
		}
		item := env.child()
		item.types[e.Item] = sequence[:len(sequence)-2]
		delete(item.shown, e.Item)
		if e.Where != nil {
			condition := c.typeOf(e.Where, item, step, "")
			if condition != "Bool" {
				checkFail("Step %s for loop where condition expects 'Bool', got %s", pyRepr(step), pyRepr(condition))
			}
			whenTrue, _ := provedPresent(e.Where, item)
			item.provePresent(whenTrue)
		}
		bodyExpected := ""
		if strings.HasSuffix(expected, "[]") {
			bodyExpected = expected[:len(expected)-2]
		}
		body := c.typeOf(e.Body, item, step, bodyExpected)
		if body == never {
			return "Null[]"
		}
		return body + "[]"
	case *FailExpr:
		reason := c.typeOf(e.Reason, env, step, "")
		if reason != "Text" {
			checkFail("Step %s fail expects a Text reason, got %s", pyRepr(step), pyRepr(reason))
		}
		return never
	case *TryExpr:
		body := c.typeOf(e.Body, env.child(), step, expected)
		if e.Otherwise == nil {
			return body
		}
		rescue := env.child()
		if e.ErrorName != "" {
			rescue.types[e.ErrorName] = "Text"
			delete(rescue.shown, e.ErrorName)
		}
		fallbackExpected := body
		if body == never {
			fallbackExpected = expected
		}
		fallback := c.typeOf(e.Otherwise, rescue, step, fallbackExpected)
		if body == never {
			return fallback
		}
		if fallback != never && fallback != body {
			checkFail("Step %s try gives %s but its otherwise gives %s; both must give the same type", pyRepr(step), pyRepr(body), pyRepr(fallback))
		}
		return body
	case *FuncExpr:
		// A function written as a value sees only its own parameters.
		scope := newEnv()
		scope.local = env.local
		for _, param := range e.Params {
			if _, taken := scope.types[param.Name]; taken {
				checkFail("Step %s function has duplicate parameter %s", pyRepr(step), pyRepr(param.Name))
			}
			c.validateType(param.Type)
			scope.types[param.Name] = param.Type
		}
		body := c.typeOf(e.Body, scope, step, e.Out)
		out := e.Out
		if out == "" {
			out = body
		}
		if out != body {
			checkFail("Step %s function body returns %s, but declared output is %s", pyRepr(step), pyRepr(body), pyRepr(out))
		}
		return functionType(e.Params, out)
	case *MethodExpr:
		return c.methodType(e, env, step)
	case *FieldExpr:
		if enum := c.enumOf(e); enum != "" {
			values := c.enumValues(enum)
			if !hasName(values, e.Name) {
				checkFail("Enum %s has no value %s; it has: %s", pyRepr(enum), pyRepr(e.Name), strings.Join(values, ", "))
			}
			return enum
		}
		base := c.typeOf(e.Target, env, step, "")
		if strings.HasSuffix(base, "[]") {
			checkFail("Step %s cannot access field %s on array type %s", pyRepr(step), pyRepr(e.Name), pyRepr(base))
		}
		if isOptional(base) || isMap(base) {
			checkFail("Step %s cannot access field %s on %s: %s", pyRepr(step), pyRepr(e.Name), pyRepr(base), absentHint(base))
		}
		if c.shape(base) != nil {
			checkFail("Step %s cannot read field %s of shape %s; a shape has only its functions", pyRepr(step), pyRepr(e.Name), pyRepr(base))
		}
		fields := c.recordFields(base)
		t, known := fieldType(fields, e.Name)
		if !known {
			checkFail("Step %s references unknown field %s on %s.%s", pyRepr(step), pyRepr(e.Name), pyRepr(base), didYouMean(e.Name, fieldNames(fields)))
		}
		return t
	case *IndexExpr:
		base := c.typeOf(e.Target, env, step, "")
		if !strings.HasSuffix(base, "[]") || isFunctionType(base) {
			checkFail("Step %s cannot index non-array type %s", pyRepr(step), pyRepr(base))
		}
		index := c.typeOf(e.Index, env, step, "")
		if index != "Number" {
			checkFail("Step %s array index expects 'Number', got %s", pyRepr(step), pyRepr(index))
		}
		return base[:len(base)-2]
	case *ArrayExpr:
		if expected == "" {
			// No type from around it: the items decide when they agree.
			inferred := ""
			for i, item := range e.Items {
				t := c.typeOf(item, env, step, "")
				if i == 0 {
					inferred = t
				} else {
					inferred = unify(inferred, t)
				}
				if inferred == "" {
					break
				}
			}
			if inferred == "" || inferred == never || inferred == "Null" {
				checkFail("Step %s cannot infer the type of an array literal; annotate the binding with an array type", pyRepr(step))
			}
			return inferred + "[]"
		}
		if !strings.HasSuffix(expected, "[]") || isFunctionType(expected) {
			checkFail("Step %s array literal requires an array-typed field", pyRepr(step))
		}
		elementType := expected[:len(expected)-2]
		for _, item := range e.Items {
			actual := c.typeOf(item, env, step, elementType)
			if !assignable(actual, elementType) {
				checkFail("Step %s array item expects %s, got %s", pyRepr(step), pyRepr(elementType), pyRepr(actual))
			}
		}
		return expected
	case *RecordExpr:
		return c.recordType(e, env, step, expected)
	case *UnaryExpr:
		operand := c.typeOf(e.Operand, env, step, "")
		required := "Number"
		if e.Op == "not" {
			required = "Bool"
		}
		if operand != required {
			checkFail("Step %s operator %s expects %s, got %s", pyRepr(step), pyRepr(e.Op), pyRepr(required), pyRepr(operand))
		}
		return required
	case *BinaryExpr:
		return c.binaryType(e, env, step)
	case *CondExpr:
		condition := c.typeOf(e.Cond, env, step, "")
		if condition != "Bool" {
			checkFail("Step %s condition expects 'Bool', got %s", pyRepr(step), pyRepr(condition))
		}
		ifTrue, ifFalse := provedPresent(e.Cond, env)
		trueEnv, falseEnv := env, env
		if len(ifTrue) > 0 {
			trueEnv = env.narrowed(ifTrue)
		}
		if len(ifFalse) > 0 {
			falseEnv = env.narrowed(ifFalse)
		}
		whenTrue := c.typeOf(e.Then, trueEnv, step, expected)
		falseExpected := expected
		if falseExpected == "" {
			falseExpected = whenTrue
		}
		whenFalse := c.typeOf(e.Else, falseEnv, step, falseExpected)
		branch := unify(whenTrue, whenFalse)
		if branch == "" {
			checkFail("Step %s conditional branches differ: %s and %s", pyRepr(step), pyRepr(whenTrue), pyRepr(whenFalse))
		}
		return branch
	case *FuncRefExpr:
		_, decl := c.functionNamed(e.Name, env)
		if decl == nil {
			checkFail("Unknown function reference %s", pyRepr(e.Name))
		}
		return functionType(decl.Params, decl.Out)
	case *CallExpr:
		qualified, decl := c.functionNamed(e.Name, env)
		_, nested := env.local[e.Name]
		args := e.Args
		// The reference passes what a nested function captures as named
		// arguments beside the written ones, so a call of one that captures
		// anything is no longer all unnamed, and its names are not filled in.
		if !nested || len(c.captures[decl]) == 0 {
			args = resolveUnnamed(e.Args, publicParams(decl))
		}
		if !nested {
			if t, bound := env.types[e.Name]; bound {
				params, out, ok := signature(t)
				if !ok {
					checkFail("%s is not a function value", pyRepr(e.Name))
				}
				c.checkArguments(args, params, env, step)
				return out
			}
		}
		if decl == nil {
			checkFail("Step %s uses unknown function %s", pyRepr(step), pyRepr(qualified))
		}
		c.checkArguments(args, decl.Params, env, step)
		return decl.Out
	}
	checkFail("Step %s contains an unsupported expression", pyRepr(step))
	return ""
}

func (c *checker) methodType(e *MethodExpr, env *Env, step string) string {
	receiverType := c.typeOf(e.Target, env, step, "")
	// A record with a function of that name: the call is that function,
	// with the record as self.
	if record := c.records[receiverType]; record != nil && hasName(record.Methods, e.Name) {
		decl := c.functions[receiverType+"$"+e.Name]
		rest := resolveUnnamed(e.Args, paramNames(decl)[1:])
		args := append([]Arg{{"self", e.Target}}, rest...)
		c.checkArguments(args, decl.Params, env, step)
		return decl.Out
	}
	if shape := c.shape(receiverType); shape != nil {
		// A shape's function: the one of whichever record the value is.
		return c.shapeCall(shape, e, env, step)
	}
	if target, ok := e.Target.(*VarExpr); ok && env.present[target.Name] &&
		(e.Name == "exists" || e.Name == "value" || e.Name == "or") {
		receiverType += "?"
	}
	method, problem := resolveMethod(receiverType, e.Name)
	if problem != "" {
		checkFail("Step %s unknown method: %s", pyRepr(step), problem)
	}
	expected := make([]Param, len(method.Params))
	wantedNames := make([]string, len(method.Params))
	for i, param := range method.Params {
		expected[i] = Param{param.Name, concrete(param.Type, receiverType)}
		wantedNames[i] = param.Name
	}
	args := resolveUnnamed(e.Args, wantedNames)
	supplied := make([]string, len(args))
	for i, arg := range args {
		supplied[i] = arg.Name
	}
	if !sameNames(supplied, wantedNames) {
		described := make([]string, len(expected))
		for i, param := range expected {
			kind := param.Type
			if strings.HasPrefix(kind, "$field") {
				kind = "Text"
			}
			described[i] = param.Name + ": " + kind
		}
		hint := ""
		if len(supplied) > 0 {
			hint = didYouMean(supplied[0], wantedNames)
		}
		checkFail("Step %s method %s takes (%s), got (%s).%s", pyRepr(step), pyRepr(e.Name), strings.Join(described, ", "), strings.Join(supplied, ", "), hint)
	}
	for _, arg := range args {
		wanted, _ := fieldType(expected, arg.Name)
		if strings.HasPrefix(wanted, "$field") {
			allowed := strings.Split(strings.SplitN(wanted, ":", 2)[1], "|")
			c.checkFieldArgument(arg.Value, receiverType[:len(receiverType)-2], allowed, step, e.Name)
			continue
		}
		given := c.typeOf(arg.Value, env, step, wanted)
		if !assignable(given, wanted) {
			checkFail("Step %s method %s argument %s expects %s, got %s", pyRepr(step), pyRepr(e.Name), pyRepr(arg.Name), pyRepr(wanted), pyRepr(given))
		}
	}
	return concrete(method.Returns, receiverType)
}

func sameNames(a, b []string) bool {
	x := append([]string{}, a...)
	y := append([]string{}, b...)
	sort.Strings(x)
	sort.Strings(y)
	return strings.Join(x, "\x00") == strings.Join(y, "\x00") && len(x) == len(y)
}

// checkFieldArgument: a field: argument names a field of the record each
// item is, written in quotes, so the checker can prove it is there.
func (c *checker) checkFieldArgument(argument Expr, elementType string, allowed []string, step, method string) {
	if c.records[elementType] == nil {
		checkFail("Step %s unknown method: %s needs an array of records, got %s", pyRepr(step), pyRepr(method), pyRepr(elementType+"[]"))
	}
	literal, ok := argument.(*Literal)
	name, isText := "", false
	if ok {
		name, isText = literal.Value.(string)
	}
	if !isText {
		checkFail("Step %s method %s takes (field: Text), got a computed value; write the field name in quotes", pyRepr(step), pyRepr(method))
	}
	fields := c.recordFields(elementType)
	t, known := fieldType(fields, name)
	if !known {
		checkFail("Step %s references unknown field %s on %s.%s", pyRepr(step), pyRepr(name), pyRepr(elementType), didYouMean(name, fieldNames(fields)))
	}
	if !hasName(allowed, t) {
		checkFail("Step %s method %s argument 'field' expects %s, got %s", pyRepr(step), pyRepr(method), pyRepr(strings.Join(allowed, " or ")), pyRepr(t))
	}
}

func (c *checker) recordType(e *RecordExpr, env *Env, step, expected string) string {
	names := make([]string, len(e.Fields))
	for i, field := range e.Fields {
		names[i] = field.Name
	}
	duplicated := len(uniqueNames(names)) != len(names)
	if e.Base != nil {
		// {..task, done: true}: the type is the base's own.
		base := c.typeOf(e.Base, env, step, "")
		if c.records[base] == nil {
			checkFail("Step %s record update needs a record after '..', got %s", pyRepr(step), pyRepr(base))
		}
		known := c.recordFields(base)
		if duplicated {
			checkFail("Step %s record update names a field more than once", pyRepr(step))
		}
		for _, field := range e.Fields {
			wanted, ok := fieldType(known, field.Name)
			if !ok {
				checkFail("Step %s record update names %s, which is not a field of %s.%s", pyRepr(step), pyRepr(field.Name), pyRepr(base), didYouMean(field.Name, fieldNames(known)))
			}
			given := c.typeOf(field.Value, env, step, wanted)
			if !assignable(given, wanted) {
				checkFail("Step %s record update field %s expects %s, got %s", pyRepr(step), pyRepr(field.Name), pyRepr(wanted), pyRepr(given))
			}
		}
		return base
	}
	if expected == "" {
		checkFail("Step %s cannot infer the type of a record literal; annotate the binding with a record type", pyRepr(step))
	}
	if isMap(expected) {
		if duplicated {
			checkFail("Step %s has duplicate record literal fields", pyRepr(step))
		}
		valueType := expected[:len(expected)-2]
		for _, field := range e.Fields {
			given := c.typeOf(field.Value, env, step, valueType)
			if !assignable(given, valueType) {
				checkFail("Step %s record field %s expects %s, got %s", pyRepr(step), pyRepr(field.Name), pyRepr(valueType), pyRepr(given))
			}
		}
		return expected
	}
	if strings.HasSuffix(expected, "[]") || strings.HasSuffix(expected, "{}") {
		checkFail("Step %s record literal requires a record-typed field", pyRepr(step))
	}
	fields := c.recordFields(expected)
	if duplicated {
		checkFail("Step %s has duplicate record literal fields", pyRepr(step))
	}
	var unknown, missing []string
	for _, name := range uniqueNames(names) {
		if _, ok := fieldType(fields, name); !ok {
			unknown = append(unknown, name)
		}
	}
	for _, field := range fields {
		if !hasName(names, field.Name) {
			missing = append(missing, field.Name)
		}
	}
	sort.Strings(unknown)
	sort.Strings(missing)
	if len(unknown) > 0 {
		checkFail("Step %s has unknown record fields %s.%s", pyRepr(step), pyList(unknown), didYouMean(unknown[0], fieldNames(fields)))
	}
	if len(missing) > 0 {
		checkFail("Step %s is missing record fields %s", pyRepr(step), pyList(missing))
	}
	for _, field := range e.Fields {
		wanted, _ := fieldType(fields, field.Name)
		given := c.typeOf(field.Value, env, step, wanted)
		if !assignable(given, wanted) {
			checkFail("Step %s record field %s expects %s, got %s", pyRepr(step), pyRepr(field.Name), pyRepr(wanted), pyRepr(given))
		}
	}
	return expected
}

func uniqueNames(names []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, name := range names {
		if !seen[name] {
			seen[name] = true
			out = append(out, name)
		}
	}
	return out
}

var arithmeticOps = map[string]bool{"+": true, "-": true, "*": true, "/": true, "%": true, "<": true, "<=": true, ">": true, ">=": true}
var orderingOps = map[string]bool{"<": true, "<=": true, ">": true, ">=": true}

func (c *checker) binaryType(e *BinaryExpr, env *Env, step string) string {
	left := c.typeOf(e.Left, env, step, "")
	// The right of `and` runs only when the left is true, and of `or` only
	// when it is false, so what the left proves holds on the right.
	rightEnv := env
	if e.Op == "and" || e.Op == "or" {
		whenTrue, whenFalse := provedPresent(e.Left, env)
		proved := whenTrue
		if e.Op == "or" {
			proved = whenFalse
		}
		if len(proved) > 0 {
			rightEnv = env.narrowed(proved)
		}
	}
	right := c.typeOf(e.Right, rightEnv, step, left)
	if e.Op == "+" {
		// An enum's value is text, and may be joined to text.
		if c.enumValues(left) != nil && right == "Text" {
			left = "Text"
		}
		if c.enumValues(right) != nil && left == "Text" {
			right = "Text"
		}
	}
	switch {
	case arithmeticOps[e.Op]:
		if left == "Number" && right == "Number" {
			if orderingOps[e.Op] {
				return "Bool"
			}
			return "Number"
		}
		if left == "Text" && (right == "Text" || right == "Number") && e.Op == "+" {
			return "Text"
		}
		checkFail("Step %s operator %s expects Number operands, got %s and %s", pyRepr(step), pyRepr(e.Op), pyRepr(left), pyRepr(right))
	case e.Op == "and" || e.Op == "or":
		if left != "Bool" || right != "Bool" {
			checkFail("Step %s operator %s expects Bool operands, got %s and %s", pyRepr(step), pyRepr(e.Op), pyRepr(left), pyRepr(right))
		}
		return "Bool"
	case e.Op == "==" || e.Op == "!=":
		if unify(left, right) == "" {
			checkFail("Step %s operator %s requires matching types, got %s and %s", pyRepr(step), pyRepr(e.Op), pyRepr(left), pyRepr(right))
		}
		return "Bool"
	}
	checkFail("Step %s has unknown operator %s", pyRepr(step), pyRepr(e.Op))
	return ""
}

// checkArguments holds a call's named arguments to the parameters it takes.
func (c *checker) checkArguments(args []Arg, params []Param, env *Env, step string) {
	var names []string
	for _, arg := range args {
		if arg.Name == "" {
			checkFail("Step %s argument needs a name; write name: value", pyRepr(step))
		}
		names = append(names, arg.Name)
	}
	if len(uniqueNames(names)) != len(names) {
		checkFail("Step %s has duplicate tool arguments", pyRepr(step))
	}
	wanted := make([]string, len(params))
	for i, param := range params {
		wanted[i] = param.Name
	}
	var unknown, missing []string
	for _, name := range names {
		if !hasName(wanted, name) {
			unknown = append(unknown, name)
		}
	}
	for _, name := range wanted {
		if !hasName(names, name) {
			missing = append(missing, name)
		}
	}
	sort.Strings(unknown)
	sort.Strings(missing)
	if len(unknown) > 0 {
		checkFail("Step %s has unknown tool arguments %s.%s", pyRepr(step), pyList(unknown), didYouMean(unknown[0], wanted))
	}
	if len(missing) > 0 {
		checkFail("Step %s is missing tool arguments %s", pyRepr(step), pyList(missing))
	}
	for _, arg := range args {
		expected, _ := fieldType(params, arg.Name)
		actual := c.typeOf(arg.Value, env, step, expected)
		if !assignable(actual, expected) {
			checkFail("Step %s argument %s expects %s, got %s", pyRepr(step), pyRepr(arg.Name), pyRepr(expected), pyRepr(actual))
		}
	}
}

// walkCalls visits every call, function reference and function written as
// a value inside an expression, in the reference's order.
func walkCalls(expression Expr, visit func(Expr)) {
	var walk func(Expr)
	walk = func(value Expr) {
		switch e := value.(type) {
		case *CallExpr:
			visit(e)
			for _, arg := range e.Args {
				walk(arg.Value)
			}
		case *FuncRefExpr:
			visit(e)
		case *FuncExpr:
			visit(e)
			walk(e.Body)
		case *FieldExpr:
			walk(e.Target)
		case *IndexExpr:
			walk(e.Target)
			walk(e.Index)
		case *MethodExpr:
			walk(e.Target)
			for _, arg := range e.Args {
				walk(arg.Value)
			}
		case *ArrayExpr:
			for _, item := range e.Items {
				walk(item)
			}
		case *RecordExpr:
			if e.Base != nil {
				walk(e.Base)
			}
			for _, field := range e.Fields {
				walk(field.Value)
			}
		case *ReturnExpr:
			walk(e.Value)
		case *UnaryExpr:
			walk(e.Operand)
		case *BinaryExpr:
			walk(e.Left)
			walk(e.Right)
		case *CondExpr:
			walk(e.Cond)
			walk(e.Then)
			walk(e.Else)
		case *MatchExpr:
			walk(e.Subject)
			for _, arm := range e.Arms {
				walk(arm.Value)
			}
		case *ForExpr:
			walk(e.Seq)
			if e.Where != nil {
				walk(e.Where)
			}
			walk(e.Body)
		case *RepeatExpr:
			walk(e.Count)
			walk(e.Body)
		case *TryExpr:
			walk(e.Body)
			if e.Otherwise != nil {
				walk(e.Otherwise)
			}
		case *FailExpr:
			walk(e.Reason)
		case *Assign:
			walk(e.Value)
		case *Binding:
			walk(e.Value)
		case *BlockExpr:
			for _, statement := range e.Stmts {
				walk(statement)
			}
			if e.Result != nil {
				walk(e.Result)
			}
		}
	}
	walk(expression)
}

// staticCalls are the functions a function's body names, as the reference
// finds them for its depth and recursion checks: a call of a parameter or
// of a local declared above it is not one, a reference always is.
func (c *checker) staticCalls(decl *FuncDecl, locals map[string]string) []string {
	bound := map[string]bool{}
	for _, param := range decl.Params {
		bound[param.Name] = true
	}
	// What a nested function captures is a parameter of it once lowered,
	// so calling a captured function value names no declaration.
	for _, captured := range c.captures[decl] {
		if _, local := locals[captured.Name]; !local {
			bound[captured.Name] = true
		}
	}
	name := func(written string) string {
		if qualified, ok := locals[written]; ok {
			return qualified
		}
		return written
	}
	var found []string
	var fromExpr func(Expr, map[string]bool)
	fromExpr = func(expression Expr, bound map[string]bool) {
		walkCalls(expression, func(call Expr) {
			switch e := call.(type) {
			case *FuncRefExpr:
				found = append(found, name(e.Name))
			case *CallExpr:
				if !bound[name(e.Name)] {
					found = append(found, name(e.Name))
				}
			}
		})
		walkCalls(expression, func(call Expr) {
			if inner, ok := call.(*FuncExpr); ok {
				own := map[string]bool{}
				for _, param := range inner.Params {
					own[param.Name] = true
				}
				walkCalls(inner.Body, func(nested Expr) {
					switch e := nested.(type) {
					case *FuncRefExpr:
						found = append(found, name(e.Name))
					case *CallExpr:
						if !own[name(e.Name)] {
							found = append(found, name(e.Name))
						}
					}
				})
			}
		})
	}
	for _, item := range decl.Body {
		fromExpr(item, bound)
		if binding, ok := item.(*Binding); ok {
			bound[binding.Name] = true
		}
	}
	fromExpr(decl.Return, bound)
	return found
}

// builtInRecords are names a program may use without declaring them; the
// reference adds them when it lowers thinking steps.
var builtInRecords = map[string]bool{
	"Choice": true, "ClassifyRequest": true, "DecideRequest": true, "Decision": true,
	"ExtractRequest": true, "Prompt": true, "Reply": true, "SummarizeRequest": true, "Summary": true,
}

// gather names every function, nested ones as outer/inner, and works out
// what each nested one captures and which local functions each one sees.
func (c *checker) gather() {
	var visit func(decl *FuncDecl, name string, captures []capture, locals map[string]string)
	visit = func(decl *FuncDecl, name string, captures []capture, locals map[string]string) {
		if _, taken := c.functions[name]; !taken {
			c.functions[name] = decl
		}
		c.order = append(c.order, name)
		c.captures[decl] = captures
		own := append([]capture{}, captures...)
		for _, param := range decl.Params {
			own = append(own, capture{param.Name, param.Type, "$capture:" + name + "/" + param.Name})
		}
		for _, item := range decl.Body {
			if binding, ok := item.(*Binding); ok {
				t := binding.Type
				if t == "" {
					t = "Any"
				}
				own = append(own, capture{binding.Name, t, "$capture:" + name + "/" + binding.Name})
			}
		}
		seeing := copyMap(locals)
		for _, child := range decl.Nested {
			seeing[child.Name] = name + "/" + child.Name
		}
		c.locals[decl] = seeing
		for _, child := range decl.Nested {
			visit(child, name+"/"+child.Name, own, seeing)
		}
	}
	for _, decl := range c.program.Functions {
		visit(decl, decl.Name, nil, map[string]string{})
	}
}

// usedByCall adds the names a step's call uses.
func usedByCall(call *StepCall, bound, used map[string]bool) {
	for _, arg := range call.Args {
		walkCalls(arg.Value, func(found Expr) {
			switch e := found.(type) {
			case *FuncRefExpr:
				used[e.Name] = true
			case *CallExpr:
				if !bound[e.Name] {
					used[e.Name] = true
				}
			}
		})
	}
	switch call.Kind {
	case "workflow":
		used[call.Name] = true
	case "function":
		if !bound[call.Name] && call.Name != valueCall {
			used[call.Name] = true
		}
	case "tool":
		used[call.Agent] = true
		used[call.Name] = true
	case "model", "agent":
		used[call.Name] = true
	case "think":
		if call.Model != "" {
			used[call.Model] = true
		}
		if call.Record != "" {
			used[call.Record] = true
		}
	}
}

// ModuleProblem is the reference's check, made as a file is read, that
// every name a declaration uses is declared. It is reported, like a fault
// in the text, with the file's path.
func ModuleProblem(program *Program, path string, visible map[string]bool) string {
	c := newChecker(program)
	local := map[string]bool{}
	used := map[string]bool{}
	for _, record := range program.Records {
		local[record.Name] = true
	}
	for _, enum := range program.Enums {
		local[enum.Name] = true
	}
	for _, shape := range program.Shapes {
		local[shape.Name] = true
	}
	for name := range c.functions {
		local[name] = true
	}
	for _, tool := range program.Tools {
		local[tool.Name] = true
	}
	for _, model := range program.Models {
		local[model.Name] = true
		used[model.Input] = true
		used[model.Output] = true
	}
	for _, agent := range program.Agents {
		local[agent.Name] = true
	}
	for _, workflow := range program.Workflows {
		local[workflow.Name] = true
	}
	for _, tool := range program.Tools {
		used[tool.Output] = true
		if tool.Input != "" {
			used[tool.Input] = true
		}
	}
	for _, agent := range program.Agents {
		for _, tool := range agent.Can {
			used[tool] = true
		}
		if agent.Model != "" {
			used[agent.Model] = true
		}
	}
	for _, workflow := range program.Workflows {
		used[workflow.Out] = true
		bound := map[string]bool{}
		for _, param := range workflow.Params {
			used[param.Type] = true
			bound[param.Name] = true
		}
		for _, step := range workflow.Steps {
			if step.BindingType != "" {
				referencedTypes(step.BindingType, used)
			}
			calls := []*StepCall{step.Call}
			if step.Fallback != nil {
				calls = append(calls, step.Fallback)
			}
			for _, branch := range step.Branches {
				calls = append(calls, branch.Call)
			}
			for _, call := range calls {
				usedByCall(call, bound, used)
			}
			bound[step.Binding] = true
		}
	}
	for _, record := range program.Records {
		for _, field := range record.Fields {
			used[baseType(field.Type)] = true
		}
	}
	for _, name := range c.order {
		decl := c.functions[name]
		referencedTypes(decl.Out, used)
		for _, param := range decl.Params {
			referencedTypes(param.Type, used)
		}
		for _, captured := range c.captures[decl] {
			referencedTypes(captured.Type, used)
		}
		for _, called := range c.staticCalls(decl, c.locals[decl]) {
			used[called] = true
		}
		for _, item := range decl.Body {
			if binding, ok := item.(*Binding); ok && binding.Type != "" {
				referencedTypes(binding.Type, used)
			}
		}
	}
	var hidden []string
	supplied := storeNames(program)
	for name := range used {
		if !local[name] && !visible[name] && !primitiveTypes[name] && !builtInRecords[name] && !supplied[name] {
			hidden = append(hidden, name)
		}
	}
	if len(hidden) == 0 {
		return ""
	}
	sort.Strings(hidden)
	candidates := []string{"Text", "Number", "Bool"}
	for name := range local {
		candidates = append(candidates, name)
	}
	for name := range visible {
		candidates = append(candidates, name)
	}
	if absolute, err := filepath.Abs(path); err == nil {
		path = absolute
	}
	return fmt.Sprintf("%s: unknown names, not declared here and not exported by an import (unavailable or private symbols %s).%s", path, pyList(hidden), didYouMean(hidden[0], candidates))
}

func newChecker(program *Program) *checker {
	c := &checker{
		program:   program,
		records:   map[string]*RecordDecl{},
		functions: map[string]*FuncDecl{},
		locals:    map[*FuncDecl]map[string]string{},
		captures:  map[*FuncDecl][]capture{},
	}
	for _, record := range program.Records {
		if c.records[record.Name] == nil {
			c.records[record.Name] = record
		}
	}
	c.tools, c.agents, c.workflows = map[string]*ToolDecl{}, map[string]*AgentDecl{}, map[string]*WorkflowDecl{}
	c.models = map[string]*ModelDecl{}
	for _, model := range program.Models {
		if c.models[model.Name] == nil {
			c.models[model.Name] = model
		}
	}
	for _, tool := range program.Tools {
		if c.tools[tool.Name] == nil {
			c.tools[tool.Name] = tool
		}
	}
	for _, agent := range program.Agents {
		if c.agents[agent.Name] == nil {
			c.agents[agent.Name] = agent
		}
	}
	for _, workflow := range program.Workflows {
		if c.workflows[workflow.Name] == nil {
			c.workflows[workflow.Name] = workflow
		}
	}
	c.gather()
	return c
}

// Check refuses a program whose types do not agree, with the reference's
// message. It runs nothing.
func Check(program *Program) (err error) {
	defer func() {
		if caught := recover(); caught != nil {
			if failure, ok := caught.(*CheckError); ok {
				err = &CheckError{plainNames(failure.Message)}
				return
			}
			panic(caught)
		}
	}()
	c := newChecker(program)
	c.checkDeclarations()
	c.checkTests()
	return nil
}

func (c *checker) checkDeclarations() {
	c.checkValueSteps()
	c.checkEnums()
	c.checkShapes()
	c.checkDefaults()
	c.checkRecordMethods()
	c.checkBudgets()
	for _, workflow := range c.program.Workflows {
		for _, step := range workflow.Steps {
			for _, branch := range step.Branches {
				if branch.Call != step.Call {
					c.checkBranches()
					return
				}
			}
		}
	}
	for _, workflow := range c.program.Workflows {
		for _, step := range workflow.Steps {
			if step.Fallback != nil {
				c.checkFallbacks()
				return
			}
		}
	}
	c.checkModels()
	if len(c.records) != len(c.program.Records) {
		checkFail("Duplicate record declaration")
	}
	if len(c.tools) != len(c.program.Tools) {
		checkFail("Duplicate tool declaration")
	}
	if len(c.agents) != len(c.program.Agents) {
		checkFail("Duplicate agent declaration")
	}
	if len(c.workflows) != len(c.program.Workflows) {
		checkFail("Duplicate workflow declaration")
	}
	if len(c.functions) != len(c.order) {
		checkFail("Duplicate function declaration")
	}
	c.checkWorkflowCalls()
	for _, record := range c.program.Records {
		if len(uniqueNames(fieldNames(record.Fields))) != len(record.Fields) {
			checkFail("Record %s has duplicate fields", pyRepr(record.Name))
		}
		for _, field := range record.Fields {
			if isFunctionType(field.Type) {
				checkFail("Function values are not allowed in external record schemas")
			}
			c.validateType(field.Type)
		}
	}

	graph := map[string][]string{}
	for _, name := range c.order {
		decl := c.functions[name]
		graph[name] = c.staticCalls(decl, c.locals[decl])
	}
	// Every function with no stated depth, even one a cycle hides.
	for _, name := range c.order {
		if c.functions[name].Recurse != 0 {
			continue
		}
		pending := append([]string{}, graph[name]...)
		reached := map[string]bool{}
		for len(pending) > 0 {
			target := pending[len(pending)-1]
			pending = pending[:len(pending)-1]
			if target == name {
				checkFail("Recursive function call involving %s requires recurse limits", pyRepr(name))
			}
			if !reached[target] {
				reached[target] = true
				pending = append(pending, graph[target]...)
			}
		}
	}
	heights := map[string]int{}
	var active []string
	var visit func(string) int
	visit = func(name string) int {
		for at, running := range active {
			if running == name {
				for _, item := range active[at:] {
					if c.functions[item].Recurse == 0 {
						checkFail("Recursive function call involving %s requires recurse limits", pyRepr(name))
					}
				}
				return 0
			}
		}
		if height, done := heights[name]; done {
			return height
		}
		if c.functions[name] == nil {
			checkFail("Unknown function %s", pyRepr(name))
		}
		if len(active) >= 32 {
			checkFail("Function call depth exceeds 32")
		}
		active = append(active, name)
		height := 1
		for _, called := range graph[name] {
			height = max(height, 1+visit(called))
		}
		active = active[:len(active)-1]
		if height > 32 {
			checkFail("Function call depth exceeds 32")
		}
		heights[name] = height
		return height
	}
	for _, name := range c.order {
		visit(name)
	}

	for _, name := range c.order {
		c.checkFunction(name, c.functions[name])
	}
	c.checkTools()
	c.checkAgents()
	c.checkWorkflows()
}

func (c *checker) checkFunction(name string, decl *FuncDecl) {
	c.validateType(decl.Out)
	env := newEnv()
	env.returns = decl.Out
	env.local = c.locals[decl]
	for _, captured := range c.captures[decl] {
		env.types[captured.Name] = captured.Type
		env.shown[captured.Name] = captured.Shown
	}
	for _, param := range decl.Params {
		if _, taken := env.types[param.Name]; taken && env.shown[param.Name] == "" {
			checkFail("Function %s has duplicate parameter %s", pyRepr(name), pyRepr(param.Name))
		}
		c.validateType(param.Type)
		env.types[param.Name] = param.Type
		delete(env.shown, param.Name)
	}
	for _, item := range decl.Body {
		binding, ok := item.(*Binding)
		if !ok {
			// A statement sees only the names declared above it.
			c.typeOf(item, env, "function "+name+" statement", "")
			env.provePresent(presentAfter(item, env))
			continue
		}
		if _, taken := env.types[binding.Name]; taken && env.shown[binding.Name] == "" {
			checkFail("Function %s cannot rebind %s", pyRepr(name), pyRepr(binding.Name))
		}
		if binding.Type != "" {
			c.validateType(binding.Type)
		}
		step := "function " + name + " local " + binding.Name
		found := c.typeOf(binding.Value, env, step, binding.Type)
		if binding.Type != "" && !assignable(found, binding.Type) {
			checkFail("Step %s local %s expects %s, got %s", pyRepr(step), pyRepr(binding.Name), pyRepr(binding.Type), pyRepr(found))
		}
		// An annotation is the binding's type, and may be wider than what
		// it is given, as in `let x: Text? = "a"`.
		if binding.Type != "" {
			found = binding.Type
		}
		env.types[binding.Name] = found
		delete(env.shown, binding.Name)
		if binding.Mutable {
			env.mutable[binding.Name] = true
		}
	}
	actual := c.typeOf(decl.Return, env, "function "+name, decl.Out)
	if !assignable(actual, decl.Out) {
		checkFail("Function %s returns %s, got %s", pyRepr(name), pyRepr(decl.Out), pyRepr(actual))
	}
}

// recordMethodNamesTaken are the names a record's methods may not take: a record and a
// map look alike when a program runs.
var recordMethodNamesTaken = []string{"get", "has", "set", "remove", "keys", "values", "length"}

func (c *checker) checkRecordMethods() {
	shapes := map[string]string{}
	for _, record := range c.program.Records {
		fields := fieldNames(record.Fields)
		sorted := append([]string{}, uniqueNames(fields)...)
		sort.Strings(sorted)
		for _, method := range record.Methods {
			count := 0
			for _, other := range record.Methods {
				if other == method {
					count++
				}
			}
			if count > 1 {
				checkFail("Record %s method %s is declared more than once", pyRepr(record.Name), pyRepr(method))
			}
			if hasName(fields, method) {
				checkFail("Record %s method %s has the same name as one of its fields", pyRepr(record.Name), pyRepr(method))
			}
			if hasName(recordMethodNamesTaken, method) {
				checkFail("Record %s method %s cannot use that name; these belong to maps: %s", pyRepr(record.Name), pyRepr(method), strings.Join(recordMethodNamesTaken, ", "))
			}
			// A record is told apart by its fields when a program runs.
			key := method + "\x00" + strings.Join(sorted, "\x00")
			if owner, taken := shapes[key]; taken {
				checkFail("Record %s method %s is also on %s, which has exactly the same fields; give one of the methods another name", pyRepr(record.Name), pyRepr(method), pyRepr(owner))
			}
			shapes[key] = record.Name
		}
	}
}

// checkTests checks each test's names and types, as the reference does
// before it runs any test.
func (c *checker) checkTests() {
	for _, test := range c.program.Tests {
		line, problem := c.checkTest(test)
		if problem != "" {
			checkFail("Test %s line %d: %s", pyRepr(test.Name), line, problem)
		}
	}
}

func (c *checker) checkTest(test *TestDecl) (line int, problem string) {
	where := "test " + test.Name
	if problem := c.checkAnswers(test); problem != "" {
		return test.Line, problem
	}
	env := newEnv()
	for _, step := range test.Steps {
		line = step.Line
		problem = func() (problem string) {
			defer func() {
				if caught := recover(); caught != nil {
					failure, ok := caught.(*CheckError)
					if !ok {
						panic(caught)
					}
					problem = failure.Message
				}
			}()
			if step.Expect {
				kind := c.typeOf(step.Value, env, where, "")
				if kind != "Bool" {
					return fmt.Sprintf("expect needs a Bool, got %s: %s", pyRepr(kind), step.Text)
				}
				return ""
			}
			if _, taken := env.types[step.Target]; taken {
				return pyRepr(step.Target) + " is already bound"
			}
			if workflow, args := c.workflowRun(step); workflow != nil {
				if problem := workflowArguments(workflow, args); problem != "" {
					return problem
				}
				for _, arg := range args {
					wanted, _ := fieldType(workflow.Params, arg.Name)
					given := c.typeOf(arg.Value, env, where, wanted)
					if !assignable(given, wanted) {
						return fmt.Sprintf("workflow %s argument %s expects %s, got %s", pyRepr(workflow.Name), pyRepr(arg.Name), pyRepr(wanted), pyRepr(given))
					}
				}
				env.types[step.Target] = workflow.Out
				return ""
			}
			found := c.typeOf(step.Value, env, where, step.Type)
			if step.Type != "" && !assignable(found, step.Type) {
				return fmt.Sprintf("%s expects %s, got %s", pyRepr(step.Target), pyRepr(step.Type), pyRepr(found))
			}
			if step.Type != "" {
				found = step.Type
			}
			env.types[step.Target] = found
			return ""
		}()
		if problem != "" {
			return line, problem
		}
	}
	return 0, ""
}
