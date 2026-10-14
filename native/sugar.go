package main

// A value written where a step's call belongs, after `otherwise` or in a
// match's case, becomes a call of a function that gives it, as
// lower_values in kosika/sugar.py makes it. The function takes whatever
// inputs and earlier steps the value names. Its name joins the workflow,
// the step and where the value stood, and the checker's messages put that
// name back into the writer's words (plainNames).

import (
	"fmt"
	"regexp"
	"sort"
)

// callOutput is the type a step's call gives, or "" when it cannot be read
// off the declarations.
func callOutput(program *Program, call *StepCall) string {
	switch call.Kind {
	case "workflow":
		for _, workflow := range program.Workflows {
			if workflow.Name == call.Name {
				return workflow.Out
			}
		}
	case "function":
		for _, decl := range program.Functions {
			if decl.Name == call.Name {
				return decl.Out
			}
		}
	case "tool":
		for _, tool := range program.Tools {
			if tool.Name == call.Name {
				return tool.Output
			}
		}
	}
	return ""
}

func isValueCall(call *StepCall) bool {
	return call != nil && call.Kind == "function" && call.Name == valueCall
}

// varNames are every name an expression reads.
func varNames(expression Expr, into map[string]bool) {
	var walk func(Expr)
	walk = func(value Expr) {
		switch e := value.(type) {
		case *VarExpr:
			into[e.Name] = true
		case *FieldExpr:
			walk(e.Target)
		case *IndexExpr:
			walk(e.Target)
			walk(e.Index)
		case *CallExpr:
			for _, arg := range e.Args {
				walk(arg.Value)
			}
		case *MethodExpr:
			walk(e.Target)
			for _, arg := range e.Args {
				walk(arg.Value)
			}
		case *BinaryExpr:
			walk(e.Left)
			walk(e.Right)
		case *UnaryExpr:
			walk(e.Operand)
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
		case *BlockExpr:
			for _, statement := range e.Stmts {
				walk(statement)
			}
			if e.Result != nil {
				walk(e.Result)
			}
		case *ReturnExpr:
			walk(e.Value)
		case *FailExpr:
			walk(e.Reason)
		case *TryExpr:
			walk(e.Body)
			if e.Otherwise != nil {
				walk(e.Otherwise)
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
		case *FuncExpr:
			walk(e.Body)
		case *Binding:
			walk(e.Value)
		case *Assign:
			walk(e.Value)
		}
	}
	walk(expression)
}

// LowerValues gives back the program with every value that stands beside
// a call replaced by a call of a function made for it.
func LowerValues(program *Program) *Program {
	var made []*FuncDecl
	lowered := *program
	lowered.Workflows = nil
	for _, workflow := range program.Workflows {
		seen := map[string]string{}
		for _, param := range workflow.Params {
			seen[param.Name] = param.Type
		}
		standIn := func(call *StepCall, name, typeName string) *StepCall {
			if !isValueCall(call) || typeName == "" {
				return call
			}
			value := call.Args[0].Value
			usedNames := map[string]bool{}
			varNames(value, usedNames)
			var used []string
			for name := range usedNames {
				if _, ok := seen[name]; ok {
					used = append(used, name)
				}
			}
			sort.Strings(used)
			decl := &FuncDecl{Name: name, Out: typeName, Return: value, Line: call.Line}
			stand := &StepCall{Kind: "function", Name: name, Line: call.Line}
			for _, item := range used {
				decl.Params = append(decl.Params, Param{item, seen[item]})
				stand.Args = append(stand.Args, Arg{item, &VarExpr{item}})
			}
			made = append(made, decl)
			return stand
		}
		copied := *workflow
		copied.Steps = nil
		for _, step := range workflow.Steps {
			calls := []*StepCall{step.Call}
			if len(step.Branches) > 0 {
				calls = nil
				for _, branch := range step.Branches {
					calls = append(calls, branch.Call)
				}
			}
			known := ""
			for _, call := range calls {
				if !isValueCall(call) {
					if found := callOutput(program, call); found != "" {
						known = found
						break
					}
				}
			}
			prefix := workflow.Name + "$" + step.Name + "$"
			changed := *step
			if len(step.Branches) > 0 {
				changed.Branches = make([]Branch, len(step.Branches))
				for i, branch := range step.Branches {
					branch.Call = standIn(branch.Call, fmt.Sprintf("%scase%d", prefix, i+1), known)
					changed.Branches[i] = branch
				}
				changed.Call = changed.Branches[0].Call
			}
			if step.Fallback != nil {
				changed.Fallback = standIn(step.Fallback, prefix+"otherwise", known)
			}
			copied.Steps = append(copied.Steps, &changed)
			if known != "" {
				seen[step.Name] = known
			}
		}
		lowered.Workflows = append(lowered.Workflows, &copied)
	}
	if len(made) == 0 {
		return program
	}
	lowered.Functions = append(append([]*FuncDecl{}, program.Functions...), made...)
	return &lowered
}

var madeName = regexp.MustCompile(`(?:Step |Function )?'(?:function )?([A-Za-z_]\w*)\$([A-Za-z_]\w*)\$(otherwise|case(\d+))'`)

// plainNames puts a made-up function's name back into the writer's words.
func plainNames(message string) string {
	return madeName.ReplaceAllStringFunc(message, func(found string) string {
		parts := madeName.FindStringSubmatch(found)
		where := "after `otherwise`"
		if parts[3] != "otherwise" {
			where = "in case " + parts[4]
		}
		return fmt.Sprintf("Step %s in workflow %s: the value %s", pyRepr(parts[2]), pyRepr(parts[1]), where)
	})
}
