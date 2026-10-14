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
func runStep(machine *Machine, step TestStep, at frame) (problem string) {
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
		machine := newMachine(program)
		at := frame{newScope(nil), nil, 0}
		result := TestResult{Name: test.Name, Passed: true, Line: test.Line}
		for _, step := range test.Steps {
			if problem := runStep(machine, step, at); problem != "" {
				result = TestResult{test.Name, false, problem, step.Line}
				break
			}
		}
		results = append(results, result)
	}
	return results
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
