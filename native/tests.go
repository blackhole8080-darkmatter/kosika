package main

// Running the tests a Kosika file carries.

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math/big"
	"sort"
	"strings"
)

type TestResult struct {
	Name    string `json:"name"`
	Passed  bool   `json:"passed"`
	Message string `json:"message"`
	Line    int    `json:"line"`
}

// showJSON writes a value as the reference engine shows one in a failed
// expectation: as JSON, with a record's fields in the order of their names.
func showJSON(v Value) string {
	var out bytes.Buffer
	writeJSON(&out, v)
	return out.String()
}

func writeJSON(out *bytes.Buffer, v Value) {
	switch x := v.(type) {
	case nil:
		out.WriteString("null")
	case bool:
		if x {
			out.WriteString("true")
		} else {
			out.WriteString("false")
		}
	case *big.Int:
		out.WriteString(x.String())
	case float64:
		text := formatFloat(x)
		if !strings.ContainsAny(text, ".en") {
			text += ".0"
		}
		out.WriteString(text)
	case string:
		encoder := json.NewEncoder(out)
		encoder.SetEscapeHTML(false)
		_ = encoder.Encode(x)
		out.Truncate(out.Len() - 1)
	case []Value:
		out.WriteString("[")
		for i, item := range x {
			if i > 0 {
				out.WriteString(", ")
			}
			writeJSON(out, item)
		}
		out.WriteString("]")
	case *Record:
		keys := append([]string{}, x.Keys...)
		sort.Strings(keys)
		out.WriteString("{")
		for i, key := range keys {
			if i > 0 {
				out.WriteString(", ")
			}
			writeJSON(out, key)
			out.WriteString(": ")
			writeJSON(out, x.Fields[key])
		}
		out.WriteString("}")
	default:
		out.WriteString("\"<function>\"")
	}
}

var comparisons = map[string]bool{"==": true, "!=": true, "<": true, "<=": true, ">": true, ">=": true}

// runStep works one step of a test out, and gives what went wrong, if anything.
func runStep(machine *Machine, step TestStep, at frame, tools, models map[string]*scriptedTool) (problem string) {
	defer func() {
		if caught := recover(); caught != nil {
			switch failure := caught.(type) {
			case *RunError:
				problem = failure.Message
			case *budgetError:
				problem = failure.Message
			case breakSignal, continueSignal, returnSignal:
				problem = "break, continue or return outside a loop or function"
			default:
				panic(caught)
			}
		}
	}()
	if !step.Expect {
		checker := newChecker(machine.program)
		if workflow, args := checker.workflowRun(step); workflow != nil {
			inputs := map[string]Value{}
			for _, arg := range args {
				inputs[arg.Name] = machine.eval(arg.Value, at)
			}
			at.scope.vars[step.Target] = newRun(machine.program, tools, models, workflow).workflow(workflow, inputs, 0)
			return ""
		}
		at.scope.vars[step.Target] = machine.eval(step.Value, at)
		return ""
	}
	if result, isBool := machine.eval(step.Value, at).(bool); isBool && result {
		return ""
	}
	detail := ""
	if binary, ok := step.Value.(*BinaryExpr); ok && comparisons[binary.Op] {
		detail = fmt.Sprintf(" (left is %s, right is %s)", showJSON(machine.eval(binary.Left, at)), showJSON(machine.eval(binary.Right, at)))
	}
	return "expected " + step.Text + detail
}

// RunTests runs every test of a program that asks no real model.
func RunTests(program *Program) []TestResult {
	results := []TestResult{}
	for _, test := range program.Tests {
		if test.Live {
			continue
		}
		results = append(results, runTest(program, test))
	}
	return results
}

func runTest(program *Program, test *TestDecl) TestResult {
	machine := newMachine(program)
	at := frame{newScope(nil), nil, 0}
	failed := func(message string, line int) TestResult {
		return TestResult{test.Name, false, message, line}
	}
	// The answers a test scripts are worked out first, each on its own.
	tools := map[string]*scriptedTool{}
	// Every declared model has a script, empty when the test gives none.
	models := map[string]*scriptedTool{}
	for _, model := range program.Models {
		models[model.Name] = &scriptedTool{name: model.Name}
	}
	for _, answer := range test.Answers {
		scope := newScope(nil)
		if problem := runStep(machine, TestStep{Target: "answers", Value: answer.Value}, frame{scope, nil, 0}, nil, nil); problem != "" {
			return failed(problem, test.Line)
		}
		list, allRecords := scope.vars["answers"].([]Value)
		for _, item := range list {
			if _, ok := item.(*Record); !ok {
				allRecords = false
			}
		}
		if !allRecords {
			return failed(fmt.Sprintf("answers for %s must be a list of records", pyRepr(answer.Name)), test.Line)
		}
		if models[answer.Name] != nil {
			models[answer.Name].answers = list
		} else {
			tools[answer.Name] = &scriptedTool{name: answer.Name, answers: list}
		}
	}
	for _, step := range test.Steps {
		if problem := runStep(machine, step, at, tools, models); problem != "" {
			return failed(problem, step.Line)
		}
	}
	// Models first, in the order they are declared; then tools.
	var unused []*scriptedTool
	for _, model := range program.Models {
		unused = append(unused, models[model.Name])
	}
	for _, answer := range test.Answers {
		if tools[answer.Name] != nil {
			unused = append(unused, tools[answer.Name])
		}
	}
	for _, script := range unused {
		if left := len(script.answers) - script.asked; left > 0 {
			return failed(fmt.Sprintf("%d answer(s) for %s were never asked for", left, pyRepr(script.name)), test.Line)
		}
	}
	return TestResult{Name: test.Name, Passed: true, Line: test.Line}
}

// TestsText is the report `kosika test` prints.
func TestsText(results []TestResult) string {
	var lines []string
	failed := 0
	for _, result := range results {
		word := "PASS"
		if !result.Passed {
			word = "FAIL"
			failed++
		}
		lines = append(lines, word+"  "+result.Name)
		if !result.Passed {
			lines = append(lines, fmt.Sprintf("      line %d: %s", result.Line, result.Message))
		}
	}
	if len(results) == 0 {
		return "No tests found"
	}
	lines = append(lines, fmt.Sprintf("%d passed, %d failed", len(results)-failed, failed))
	return strings.Join(lines, "\n")
}
