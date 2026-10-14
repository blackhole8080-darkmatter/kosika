package main

// The runner: works out expressions, calls functions, runs tests.
//
// It follows the reference engine's rules for what a program can see:
// which scope a name is found in, when a loop stops, what a failure says.
// It does not yet check types before running, as the reference checker
// does; a program the checker would refuse may here fail later or not at
// all. The conformance suite's refusal cases are how that gap will close.

import (
	"fmt"
	"math/big"
	"sort"
	"strings"
)

// The reference engine's limits on one run.
const (
	maxFunctionCalls  = 10_000
	maxLoopIterations = 1_000_000
	maxCallDepth      = 128
)

type Scope struct {
	vars   map[string]Value
	parent *Scope
}

func newScope(parent *Scope) *Scope { return &Scope{map[string]Value{}, parent} }

func (s *Scope) lookup(name string) (Value, bool) {
	for scope := s; scope != nil; scope = scope.parent {
		if value, has := scope.vars[name]; has {
			return value, true
		}
	}
	return nil, false
}

// assign writes to whichever scope owns the name, so a loop body can add
// to a total declared outside it.
func (s *Scope) assign(name string, value Value) {
	for scope := s; scope != nil; scope = scope.parent {
		if _, has := scope.vars[name]; has || scope.parent == nil {
			scope.vars[name] = value
			return
		}
	}
}

// flat gives every name this scope can see, nearest first.
func (s *Scope) flat() map[string]Value {
	out := map[string]Value{}
	for scope := s; scope != nil; scope = scope.parent {
		for name, value := range scope.vars {
			if _, has := out[name]; !has {
				out[name] = value
			}
		}
	}
	return out
}

type breakSignal struct{}
type continueSignal struct{}
type returnSignal struct{ value Value }

// A budgetError ends the run and cannot be caught by try.
type budgetError struct{ Message string }

func (e *budgetError) Error() string { return e.Message }

type Machine struct {
	program   *Program
	functions map[string]*FuncDecl
	records   []*RecordDecl
	active    []string
	calls     int
	loops     int
}

func newMachine(program *Program) *Machine {
	machine := &Machine{program: program, functions: map[string]*FuncDecl{}, records: program.Records}
	for _, function := range program.Functions {
		machine.functions[function.Name] = function
	}
	return machine
}

// A frame is where an expression is being worked out: the names it can
// see, and the functions declared around it.
type frame struct {
	scope *Scope
	funcs map[string]*FuncDecl
	depth int
}

func (f frame) with(scope *Scope) frame { return frame{scope, f.funcs, f.depth} }

func (m *Machine) loopPass() {
	m.loops++
	if m.loops > maxLoopIterations {
		panic(&budgetError{fmt.Sprintf("Execution exceeded max_loop_iterations=%d", maxLoopIterations)})
	}
}

func (m *Machine) eval(expression Expr, at frame) Value {
	switch e := expression.(type) {
	case *Literal:
		return e.Value
	case *VarExpr:
		value, has := at.scope.lookup(e.Name)
		if !has {
			runFail(fmt.Sprintf("Workflow variable %s is unavailable", quoted(e.Name)))
		}
		return value
	case *FieldExpr:
		base := m.eval(e.Target, at)
		record, ok := base.(*Record)
		if !ok {
			runFail(fmt.Sprintf("Cannot read field %s from %s", quoted(e.Name), hostName(base)))
		}
		value, has := record.Fields[e.Name]
		if !has {
			runFail(fmt.Sprintf("Workflow result has no field %s", quoted(e.Name)))
		}
		return value
	case *IndexExpr:
		base := m.eval(e.Target, at)
		index := m.eval(e.Index, at)
		items, ok := base.([]Value)
		if !ok {
			runFail("Cannot index non-array value " + hostName(base))
		}
		whole, isWhole := index.(*big.Int)
		if !isWhole {
			runFail("Array indexes must be integers")
		}
		position := -1
		if whole.IsInt64() {
			n := whole.Int64()
			// A negative index counts from the end, as in the reference.
			if n < 0 {
				n += int64(len(items))
			}
			if n >= 0 && n < int64(len(items)) {
				position = int(n)
			}
		}
		if position < 0 {
			runFail(fmt.Sprintf("Array index %s is out of range", whole.String()))
		}
		return items[position]
	case *ArrayExpr:
		items := make([]Value, len(e.Items))
		for i, item := range e.Items {
			items[i] = m.eval(item, at)
		}
		return items
	case *RecordExpr:
		record := newRecord()
		if e.Base != nil {
			base, ok := m.eval(e.Base, at).(*Record)
			if !ok {
				runFail("Only a record can be copied with ..")
			}
			record = base.copy()
		}
		for _, field := range e.Fields {
			record.set(field.Name, m.eval(field.Value, at))
		}
		return record
	case *MethodExpr:
		return m.callMethod(e, at)
	case *UnaryExpr:
		operand := m.eval(e.Operand, at)
		if e.Op == "not" {
			return !truthy(operand)
		}
		switch n := operand.(type) {
		case *big.Int:
			return new(big.Int).Neg(n)
		case float64:
			return -n
		}
		runFail("Only a number can be negated")
	case *BinaryExpr:
		return m.binary(e, at)
	case *CondExpr:
		if truthy(m.eval(e.Cond, at)) {
			return m.eval(e.Then, at)
		}
		return m.eval(e.Else, at)
	case *MatchExpr:
		subject := m.eval(e.Subject, at)
		for _, arm := range e.Arms {
			if arm.Any || valueEqual(subject, arm.Pattern) {
				return m.eval(arm.Value, at)
			}
		}
		runFail("Non-exhaustive match reached execution")
	case *Assign:
		at.scope.assign(e.Target, m.eval(e.Value, at))
		return nil
	case *Binding:
		at.scope.vars[e.Name] = m.eval(e.Value, at)
		return nil
	case *BlockExpr:
		inner := at.with(newScope(at.scope))
		for _, statement := range e.Stmts {
			m.eval(statement, inner)
		}
		if e.Result == nil {
			return nil
		}
		return m.eval(e.Result, inner)
	case *ForExpr:
		return m.forLoop(e, at)
	case *RepeatExpr:
		return m.repeat(e, at)
	case *FailExpr:
		runFail(showText(m.eval(e.Reason, at)))
	case *TryExpr:
		return m.try(e, at)
	case *BreakExpr:
		panic(breakSignal{})
	case *ContinueExpr:
		panic(continueSignal{})
	case *ReturnExpr:
		panic(returnSignal{m.eval(e.Value, at)})
	case *FuncRefExpr:
		decl := at.funcs[e.Name]
		if decl == nil {
			decl = m.functions[e.Name]
		}
		if decl == nil {
			runFail(fmt.Sprintf("Unknown function %s", quoted(e.Name)))
		}
		return &Closure{decl, at.scope.flat(), at.funcs}
	case *FuncExpr:
		decl := &FuncDecl{Name: "$anonymous", Params: e.Params, Out: e.Out, Return: e.Body, Recurse: 0}
		return &Closure{decl, at.scope.flat(), at.funcs}
	case *CallExpr:
		return m.call(e, at)
	}
	runFail("Workflow contains an unsupported expression")
	return nil
}

// hostName names a value's kind as the reference engine's messages do.
func hostName(v Value) string {
	switch v.(type) {
	case nil:
		return "NoneType"
	case bool:
		return "bool"
	case *big.Int:
		return "int"
	case float64:
		return "float"
	case string:
		return "str"
	case []Value:
		return "list"
	}
	return "dict"
}

func showText(v Value) string {
	switch x := v.(type) {
	case string:
		return x
	case nil:
		return "None"
	case bool:
		if x {
			return "True"
		}
		return "False"
	}
	if isNumber(v) {
		if f, ok := v.(float64); ok && f == float64(int64(f)) {
			return formatFloat(f) + ".0"
		}
		return formatNumber(v)
	}
	return fmt.Sprint(v)
}

func (m *Machine) binary(e *BinaryExpr, at frame) Value {
	left := m.eval(e.Left, at)
	if e.Op == "and" && !truthy(left) {
		return false
	}
	if e.Op == "or" && truthy(left) {
		return true
	}
	right := m.eval(e.Right, at)
	switch e.Op {
	case "and", "or":
		return truthy(right)
	case "==":
		return valueEqual(left, right)
	case "!=":
		return !valueEqual(left, right)
	case "+":
		leftText, leftIsText := left.(string)
		rightText, rightIsText := right.(string)
		switch {
		case leftIsText && rightIsText:
			return leftText + rightText
		case leftIsText && isNumber(right):
			return leftText + formatNumber(right)
		case isNumber(left) && rightIsText:
			return formatNumber(left) + rightText
		}
		if leftList, ok := left.([]Value); ok {
			if rightList, ok := right.([]Value); ok {
				return append(append([]Value{}, leftList...), rightList...)
			}
		}
	}
	if e.Op == "<" || e.Op == "<=" || e.Op == ">" || e.Op == ">=" {
		order := 0
		leftText, leftIsText := left.(string)
		rightText, rightIsText := right.(string)
		switch {
		case isNumber(left) && isNumber(right):
			order = compareNumbers(left, right)
		case leftIsText && rightIsText:
			order = compareText(leftText, rightText)
		default:
			runFail(fmt.Sprintf("'%s' not supported between instances of '%s' and '%s'", e.Op, hostName(left), hostName(right)))
		}
		switch e.Op {
		case "<":
			return order < 0
		case "<=":
			return order <= 0
		case ">":
			return order > 0
		}
		return order >= 0
	}
	if !isNumber(left) || !isNumber(right) {
		runFail(fmt.Sprintf("unsupported operand type(s) for %s: '%s' and '%s'", e.Op, hostName(left), hostName(right)))
	}
	return arithmetic(e.Op, left, right)
}

// compareText orders two texts by their characters' numbers, as the
// reference does, not by any language's alphabet.
func compareText(a, b string) int {
	left, right := []rune(a), []rune(b)
	for i := 0; i < len(left) && i < len(right); i++ {
		if left[i] != right[i] {
			if left[i] < right[i] {
				return -1
			}
			return 1
		}
	}
	switch {
	case len(left) < len(right):
		return -1
	case len(left) > len(right):
		return 1
	}
	return 0
}

// loopBody runs one pass of a loop's body. It says whether the loop must
// stop, and whether the pass was skipped by continue.
func (m *Machine) loopBody(body Expr, at frame) (value Value, stop, skipped bool) {
	defer func() {
		if caught := recover(); caught != nil {
			switch caught.(type) {
			case breakSignal:
				stop = true
			case continueSignal:
				skipped = true
			default:
				panic(caught)
			}
		}
	}()
	return m.eval(body, at), false, false
}

func (m *Machine) forLoop(e *ForExpr, at frame) Value {
	sequence, ok := m.eval(e.Seq, at).([]Value)
	if !ok {
		runFail("for loop requires an array sequence")
	}
	if e.Capped && len(sequence) > e.Limit {
		runFail(fmt.Sprintf("for loop exceeded its limit of %d iterations", e.Limit))
	}
	collected := []Value{}
	inner := frame{nil, at.funcs, at.depth + 1}
	for _, element := range sequence {
		m.loopPass()
		inner.scope = newScope(at.scope)
		inner.scope.vars[e.Item] = element
		if e.Where != nil && !truthy(m.eval(e.Where, inner)) {
			continue
		}
		value, stop, skipped := m.loopBody(e.Body, inner)
		if stop {
			break
		}
		if !skipped {
			collected = append(collected, value)
		}
	}
	return collected
}

func (m *Machine) repeat(e *RepeatExpr, at frame) Value {
	raw := m.eval(e.Count, at)
	if !isNumber(raw) {
		runFail("repeat count must be a number")
	}
	whole, isWhole := asWhole(raw)
	if !isWhole || whole.Sign() < 0 {
		runFail("repeat count must be a whole number, zero or more")
	}
	var result Value
	inner := frame{nil, at.funcs, at.depth + 1}
	one := big.NewInt(1)
	for pass := new(big.Int); pass.Cmp(whole) < 0; pass.Add(pass, one) {
		m.loopPass()
		inner.scope = newScope(at.scope)
		value, stop, skipped := m.loopBody(e.Body, inner)
		if stop {
			break
		}
		if !skipped {
			result = value
		}
	}
	return result
}

func (m *Machine) try(e *TryExpr, at frame) Value {
	attempts := e.Retry + 1
	for {
		attempts--
		value, failure := m.attempt(e.Body, at.with(newScope(at.scope)))
		if failure == nil {
			return value
		}
		if attempts > 0 {
			continue
		}
		if e.Otherwise == nil {
			panic(failure)
		}
		rescue := at.with(newScope(at.scope))
		if e.ErrorName != "" {
			reason := []rune(failure.Message)
			if len(reason) > 500 {
				reason = reason[:500]
			}
			rescue.scope.vars[e.ErrorName] = string(reason)
		}
		return m.eval(e.Otherwise, rescue)
	}
}

// attempt works a body out, and gives the failure if the program failed.
// A spent budget, and break, continue and return, pass straight through.
func (m *Machine) attempt(body Expr, at frame) (value Value, failure *RunError) {
	defer func() {
		if caught := recover(); caught != nil {
			if problem, ok := caught.(*RunError); ok {
				failure = problem
				return
			}
			panic(caught)
		}
	}()
	return m.eval(body, at), nil
}

// resolveUnnamed names a call's unnamed arguments from the callee's
// parameters: one argument takes the one parameter; arguments that are
// same-named variables go by name; otherwise they go by position.
func resolveUnnamed(arguments []Arg, parameters []string) []Arg {
	if len(arguments) == 0 {
		return arguments
	}
	for _, argument := range arguments {
		if argument.Name != "" {
			return arguments
		}
	}
	if len(arguments) == 1 && len(parameters) == 1 {
		return []Arg{{parameters[0], arguments[0].Value}}
	}
	var byName []Arg
	for _, argument := range arguments {
		if name := inferredName(argument.Value); name != "" {
			byName = append(byName, Arg{name, argument.Value})
		}
	}
	if len(byName) != len(arguments) {
		byName = nil
	}
	if byName != nil {
		names := make([]string, len(byName))
		for i, argument := range byName {
			names[i] = argument.Name
		}
		wanted := append([]string{}, parameters...)
		sort.Strings(names)
		sort.Strings(wanted)
		if strings.Join(names, "\x00") == strings.Join(wanted, "\x00") {
			return byName
		}
	}
	if len(arguments) == len(parameters) {
		out := make([]Arg, len(arguments))
		for i, argument := range arguments {
			out[i] = Arg{parameters[i], argument.Value}
		}
		return out
	}
	if byName != nil {
		return byName
	}
	return arguments
}

func paramNames(decl *FuncDecl) []string {
	names := make([]string, len(decl.Params))
	for i, param := range decl.Params {
		names[i] = param.Name
	}
	return names
}

func (m *Machine) call(e *CallExpr, at frame) Value {
	var decl *FuncDecl
	captured := map[string]Value{}
	funcs := at.funcs
	if value, has := at.scope.lookup(e.Name); has {
		closure, ok := value.(*Closure)
		if !ok {
			runFail("Invalid function value")
		}
		decl, captured, funcs = closure.Decl, closure.Captured, closure.Funcs
	} else if nested := at.funcs[e.Name]; nested != nil {
		// A function declared inside the one running sees what it sees now.
		decl, captured = nested, at.scope.flat()
	} else if top := m.functions[e.Name]; top != nil {
		decl, funcs = top, nil
	} else {
		runFail(fmt.Sprintf("Unknown function %s", quoted(e.Name)))
	}
	arguments := map[string]Value{}
	for _, argument := range resolveUnnamed(e.Args, paramNames(decl)) {
		arguments[argument.Name] = m.eval(argument.Value, at)
	}
	return m.invoke(decl, arguments, captured, funcs, at.depth+1)
}

func (m *Machine) invoke(decl *FuncDecl, arguments, captured map[string]Value, funcs map[string]*FuncDecl, depth int) (result Value) {
	limit := decl.Recurse
	if limit == 0 {
		limit = 1
	}
	running := 0
	for _, name := range m.active {
		if name == decl.Name {
			running++
		}
	}
	if decl.Name != "$anonymous" && running >= limit {
		runFail(fmt.Sprintf("Recursion limit %d exceeded for %s", limit, quoted(decl.Name)))
	}
	m.calls++
	if m.calls > maxFunctionCalls {
		panic(&budgetError{fmt.Sprintf("Execution exceeded max_function_calls=%d", maxFunctionCalls)})
	}
	if depth >= maxCallDepth {
		runFail("Function call depth exceeds 128")
	}
	for _, param := range decl.Params {
		if _, given := arguments[param.Name]; !given {
			runFail(fmt.Sprintf("%s is a required property", quoted(param.Name)))
		}
	}
	if len(arguments) != len(decl.Params) {
		runFail(fmt.Sprintf("Function %s was given an argument it does not take", quoted(decl.Name)))
	}
	m.active = append(m.active, decl.Name)
	defer func() { m.active = m.active[:len(m.active)-1] }()

	outer := &Scope{captured, nil}
	local := &Scope{arguments, outer}
	visible := map[string]*FuncDecl{}
	for name, function := range funcs {
		visible[name] = function
	}
	for _, nested := range decl.Nested {
		visible[nested.Name] = nested
	}
	at := frame{local, visible, depth}
	defer func() {
		if caught := recover(); caught != nil {
			if early, ok := caught.(returnSignal); ok {
				result = early.value
				return
			}
			panic(caught)
		}
	}()
	for _, statement := range decl.Body {
		m.eval(statement, at)
	}
	return m.eval(decl.Return, at)
}
