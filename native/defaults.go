package main

// Default values, as fill_defaults in kosika/sugar.py has them:
//
//   fn greet(name: Text, loud: Bool = false) -> Text { ... }
//   record Item { name: Text  count: Number = 1 }
//
// A call that leaves out a parameter with a default is given it, and a
// record called like a function, `Item(name: "pen")`, is made by a function
// of the record's name that takes each field, with its default. Only a
// value written out may be a default: a number, text, true, false, null or
// [].

import (
	"fmt"
	"math/big"
)

// parseDefault reads `= value` after a parameter or a field, if it is there.
func (p *Parser) parseDefault() Expr {
	if !p.accept("=") {
		return nil
	}
	token := p.current()
	value := p.expression()
	constant := false
	switch e := value.(type) {
	case *Literal:
		constant = true
	case *UnaryExpr:
		if literal, ok := e.Operand.(*Literal); ok && e.Op == "-" {
			switch literal.Value.(type) {
			case *big.Int, float64:
				constant = true
			}
		}
	case *ArrayExpr:
		constant = len(e.Items) == 0
	}
	if !constant {
		parseFail("A default value at %d:%d must be written out: a number, text, true, false, null or []", token.Line, token.Column)
	}
	return value
}

// parseFunctionParams reads a named function's parameters, each with its
// default if it has one.
func (p *Parser) parseFunctionParams(function *FuncDecl, depth int, owner string) {
	for !p.accept(")") {
		name := p.identifier()
		p.expect(":")
		typeName := p.parseTypeName(0)
		if given := p.parseDefault(); given != nil {
			if owner != "" {
				parseFail("A record's function cannot give a parameter a default value yet")
			}
			if depth > 0 {
				parseFail("A nested function cannot give a parameter a default value yet")
			}
			if function.Defaults == nil {
				function.Defaults = map[string]Expr{}
			}
			function.Defaults[name] = given
		}
		function.Params = append(function.Params, Param{name, typeName})
		if !p.check(")") {
			p.expect(",")
		}
	}
}

func defaultKind(value Expr) string {
	switch e := value.(type) {
	case *ArrayExpr:
		return "[]"
	case *UnaryExpr:
		return "Number"
	case *Literal:
		switch e.Value.(type) {
		case nil:
			return "Null"
		case bool:
			return "Bool"
		case string:
			return "Text"
		}
	}
	return "Number"
}

func defaultFits(kind, typeName string) bool {
	plain := typeName
	if len(plain) > 0 && plain[len(plain)-1] == '?' {
		plain = plain[:len(plain)-1]
	}
	switch kind {
	case "Null":
		return plain != typeName
	case "[]":
		return len(plain) > 2 && plain[len(plain)-2:] == "[]"
	}
	return plain == kind
}

// checkDefaults: a default must be a value of the type it stands for.
func (c *checker) checkDefaults() {
	records := map[string]bool{}
	type owned struct {
		owner    string
		params   []Param
		defaults map[string]Expr
	}
	var all []owned
	for _, record := range c.program.Records {
		records[record.Name] = true
		all = append(all, owned{fmt.Sprintf("Record %s field", pyRepr(record.Name)), record.Fields, record.Defaults})
	}
	for _, function := range c.program.Functions {
		if !records[function.Name] {
			all = append(all, owned{fmt.Sprintf("Function %s parameter", pyRepr(function.Name)), function.Params, function.Defaults})
		}
	}
	for _, item := range all {
		for _, param := range item.params {
			given, ok := item.defaults[param.Name]
			if !ok {
				continue
			}
			kind := defaultKind(given)
			if !defaultFits(kind, param.Type) {
				said := pyRepr(kind)
				if kind == "[]" {
					said = "an empty list"
				}
				checkFail("%s %s is %s, so its default cannot be %s", item.owner, pyRepr(param.Name), pyRepr(param.Type), said)
			}
		}
	}
}

// FillDefaults gives each call the parameters it leaves out that have a
// default, and makes each record that is called like a function.
func FillDefaults(program *Program) *Program {
	declared := map[string]bool{}
	for _, function := range program.Functions {
		declared[function.Name] = true
	}
	called := map[string]bool{}
	visitAll(program, func(call *CallExpr) { called[call.Name] = true }, func(call *StepCall) { called[call.Name] = true })
	lowered := *program
	lowered.Functions = append([]*FuncDecl{}, program.Functions...)
	for _, record := range program.Records {
		if !called[record.Name] || declared[record.Name] {
			continue
		}
		made := &FuncDecl{Name: record.Name, Out: record.Name, Defaults: record.Defaults}
		fields := &RecordExpr{}
		for _, field := range record.Fields {
			made.Params = append(made.Params, field)
			fields.Fields = append(fields.Fields, RecordField{field.Name, &VarExpr{field.Name}})
		}
		made.Return = fields
		lowered.Functions = append(lowered.Functions, made)
	}
	functions := map[string]*FuncDecl{}
	for _, function := range lowered.Functions {
		if len(function.Defaults) > 0 {
			functions[function.Name] = function
		}
	}
	if len(functions) == 0 {
		return &lowered
	}
	fill := func(name string, args []Arg) []Arg {
		function := functions[name]
		if function == nil {
			return args
		}
		var names []string
		for _, param := range function.Params {
			names = append(names, param.Name)
		}
		args = resolveUnnamed(args, names)
		allUnnamed := len(args) > 0
		for _, arg := range args {
			allUnnamed = allUnnamed && arg.Name == ""
		}
		if allUnnamed && len(args) < len(names) {
			// Written in order, with the ones that have defaults left off
			// the end: `greet("Ann")` for `greet(name, loud = false)`.
			rest := true
			for _, name := range names[len(args):] {
				_, has := function.Defaults[name]
				rest = rest && has
			}
			if rest {
				named := make([]Arg, len(args))
				for i, arg := range args {
					named[i] = Arg{names[i], arg.Value}
				}
				args = named
			}
		}
		given := map[string]bool{}
		for _, arg := range args {
			if arg.Name == "" {
				return args
			}
			given[arg.Name] = true
		}
		for _, name := range names {
			if value, has := function.Defaults[name]; has && !given[name] {
				args = append(args, Arg{name, value})
			}
		}
		return args
	}
	visitAll(&lowered, func(call *CallExpr) { call.Args = fill(call.Name, call.Args) },
		func(call *StepCall) {
			if call.Kind == "function" {
				call.Args = fill(call.Name, call.Args)
			}
		})
	return &lowered
}

// visitAll calls `expression` on every call in a program's functions,
// workflows and tests, and `step` on every step's call.
func visitAll(program *Program, expression func(*CallExpr), step func(*StepCall)) {
	visit := func(value Expr) {
		if value == nil {
			return
		}
		walkCalls(value, func(found Expr) {
			if call, ok := found.(*CallExpr); ok {
				expression(call)
			}
		})
	}
	var function func(*FuncDecl)
	function = func(decl *FuncDecl) {
		for _, item := range decl.Body {
			visit(item)
		}
		visit(decl.Return)
		for _, nested := range decl.Nested {
			function(nested)
		}
	}
	for _, decl := range program.Functions {
		function(decl)
	}
	for _, workflow := range program.Workflows {
		for _, item := range workflow.Steps {
			calls := []*StepCall{item.Call, item.Fallback}
			for _, branch := range item.Branches {
				calls = append(calls, branch.Call)
			}
			seen := map[*StepCall]bool{}
			for _, call := range calls {
				if call == nil || seen[call] {
					continue
				}
				seen[call] = true
				for _, arg := range call.Args {
					visit(arg.Value)
				}
				if call.Kind == "function" {
					step(call)
				}
			}
			if item.Subject != nil {
				visit(item.Subject)
			}
		}
	}
	for _, test := range program.Tests {
		for _, answer := range test.Answers {
			visit(answer.Value)
		}
		for _, item := range test.Steps {
			visit(item.Value)
		}
	}
}
