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
	case "model":
		for _, model := range program.Models {
			if model.Name == call.Name {
				return model.Output
			}
		}
	case "agent":
		for _, agent := range program.Agents {
			if agent.Name == call.Name {
				for _, model := range program.Models {
					if model.Name == agent.Model {
						return model.Output
					}
				}
				return ""
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
			if isValueCall(step.Call) && len(step.Branches) == 0 {
				// `count = before + 1`: a step that is a value. Its type is
				// the one written, `tally: Tally = ...`, or else the type of
				// the value itself, or of where a later step hands it on.
				known = step.BindingType
				if known == "" {
					known = valueType(program, step.Call.Args[0].Value, seen)
				}
				if known == "" {
					known = typeFromUse(program, workflow, step.Name)
				}
				if known == "" && step == workflow.Steps[len(workflow.Steps)-1] {
					// The last step is what the workflow returns.
					known = workflow.Out
				}
				changed.Call = standIn(step.Call, prefix+"value", known)
			}
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

// valueType is the type of a value a step is, worked out as the checker
// would, or "" when it cannot be told; the checker then says what to write.
func valueType(program *Program, value Expr, seen map[string]string) (found string) {
	defer func() {
		if recover() != nil {
			found = ""
		}
	}()
	env := newEnv()
	for name, typeName := range seen {
		env.types[name] = typeName
	}
	found = newChecker(program).typeOf(value, env, "value", "")
	if found == "Null" {
		return ""
	}
	return found
}

// typeFromUse is the type a value step must have, read from where a later
// step hands it on whole: `f(tally)`, or a tool's input field.
func typeFromUse(program *Program, workflow *WorkflowDecl, name string) string {
	for _, later := range workflow.Steps {
		calls := []*StepCall{later.Call, later.Fallback}
		for _, branch := range later.Branches {
			calls = append(calls, branch.Call)
		}
		for _, call := range calls {
			if call == nil {
				continue
			}
			for _, arg := range call.Args {
				if used, ok := arg.Value.(*VarExpr); !ok || used.Name != name {
					continue
				}
				switch call.Kind {
				case "function":
					for _, decl := range program.Functions {
						if decl.Name != call.Name {
							continue
						}
						if len(decl.Params) == 1 && len(call.Args) == 1 {
							// A sole argument is not yet named for its parameter.
							return decl.Params[0].Type
						}
						for _, param := range decl.Params {
							if param.Name == arg.Name {
								return param.Type
							}
						}
					}
				case "tool":
					for _, tool := range program.Tools {
						if tool.Name != call.Name {
							continue
						}
						for _, record := range program.Records {
							if record.Name == tool.Input {
								if typeName, ok := fieldType(record.Fields, arg.Name); ok {
									return typeName
								}
							}
						}
					}
				}
			}
		}
	}
	return ""
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
