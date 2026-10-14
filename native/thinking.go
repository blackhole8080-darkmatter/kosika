package main

// Thinking steps (`classify`, `extract`, `summarize`, `decide`) and general
// models. As lower_thinking in kosika/thinking.py does, each thinking step
// is made an ordinary model call against a model made for it, which gives
// the answers of the declared model it names; and the records these need,
// which the language supplies, are added to the program.

import (
	"fmt"
	"sort"
	"strings"
)

// A general model, `model NAME`, takes and gives text in these records.
const (
	generalInput  = "Prompt"
	generalOutput = "Reply"
)

var builtInRecordFields = map[string][]Param{
	generalInput:       {{"text", "Text"}},
	generalOutput:      {{"text", "Text"}},
	"Choice":           {{"label", "Text"}, {"confidence", "Number"}},
	"Summary":          {{"text", "Text"}},
	"Decision":         {{"answer", "Bool"}, {"confidence", "Number"}, {"reason", "Text"}},
	"ClassifyRequest":  {{"text", "Text"}, {"choices", "Text[]"}},
	"ExtractRequest":   {{"text", "Text"}},
	"SummarizeRequest": {{"text", "Text"}},
	"DecideRequest":    {{"question", "Text"}, {"given", "Text"}},
}

func thinkingShape(verb, record string) (string, string) {
	switch verb {
	case "classify":
		return "ClassifyRequest", "Choice"
	case "extract":
		return "ExtractRequest", record
	case "summarize":
		return "SummarizeRequest", "Summary"
	}
	return "DecideRequest", "Decision"
}

type thinkingError struct{ message string }

func thinkFail(format string, args ...any) {
	panic(&thinkingError{fmt.Sprintf(format, args...)})
}

func isGeneral(model *ModelDecl) bool {
	return model.Provider == "" && model.Input == generalInput && model.Output == generalOutput
}

// chooseModel is the declared model a thinking step asks.
func chooseModel(program *Program, verb string, step *Step, named string) *ModelDecl {
	var declared []*ModelDecl
	for _, model := range program.Models {
		if model.Provider == "" {
			declared = append(declared, model)
		}
	}
	sortedNames := func(models []*ModelDecl) string {
		var names []string
		for _, model := range models {
			names = append(names, model.Name)
		}
		sort.Strings(names)
		return strings.Join(names, ", ")
	}
	if named != "" {
		for _, model := range declared {
			if model.Name == named {
				return model
			}
		}
		known := sortedNames(declared)
		if known == "" {
			known = "none"
		}
		thinkFail("Step %s uses unknown model %s; declared model(s): %s", pyRepr(step.Name), pyRepr(named), known)
	}
	if len(declared) == 1 {
		return declared[0]
	}
	var defaults []*ModelDecl
	for _, model := range declared {
		if model.Default {
			defaults = append(defaults, model)
		}
	}
	switch {
	case len(defaults) == 1:
		return defaults[0]
	case len(defaults) > 1:
		thinkFail("Step %s needs a model for %s: this program marks several models as default (%s)", pyRepr(step.Name), verb, sortedNames(defaults))
	case len(declared) == 0:
		thinkFail("Step %s needs a model for %s: declare one with `model assistant`", pyRepr(step.Name), verb)
	}
	thinkFail("Step %s needs a model for %s: this program declares several (%s), so add `using NAME` or mark one `default model NAME`", pyRepr(step.Name), verb, sortedNames(declared))
	return nil
}

// LowerThinking gives back the program with every thinking step made a
// model call, or why that cannot be done.
func LowerThinking(program *Program) (lowered *Program, refused string) {
	usesGeneral, thinks := false, false
	for _, model := range program.Models {
		usesGeneral = usesGeneral || isGeneral(model)
	}
	for _, workflow := range program.Workflows {
		for _, step := range workflow.Steps {
			calls := []*StepCall{step.Call, step.Fallback}
			for _, branch := range step.Branches {
				calls = append(calls, branch.Call)
			}
			for _, call := range calls {
				thinks = thinks || (call != nil && call.Kind == "think")
			}
		}
	}
	if !usesGeneral && !thinks {
		return program, ""
	}
	defer func() {
		if caught := recover(); caught != nil {
			failure, ok := caught.(*thinkingError)
			if !ok {
				panic(caught)
			}
			lowered, refused = nil, failure.message
		}
	}()
	needed := map[string]bool{}
	if usesGeneral {
		needed[generalInput], needed[generalOutput] = true, true
	}
	known := map[string]bool{}
	for _, record := range program.Records {
		known[record.Name] = true
	}
	generated := map[string]*ModelDecl{}
	var order []string
	lower := func(step *Step, call *StepCall, named string) *StepCall {
		if call == nil || call.Kind != "think" {
			return call
		}
		if named == "" {
			named = call.Model
		}
		base := chooseModel(program, call.Verb, step, named)
		input, output := thinkingShape(call.Verb, call.Record)
		if call.Verb == "extract" && !known[output] {
			var names []string
			for name := range known {
				names = append(names, name)
			}
			thinkFail("Step %s extracts unknown record %s.%s", pyRepr(step.Name), pyRepr(output), didYouMean(output, names))
		}
		needed[input] = true
		if builtInRecordFields[output] != nil {
			needed[output] = true
		}
		name := base.Name + "/" + call.Verb
		if call.Verb == "extract" {
			name += "/" + output
		}
		if generated[name] == nil {
			made := *base
			made.Name, made.Input, made.Output = name, input, output
			made.Provider, made.Verb, made.Default = base.Name, call.Verb, false
			generated[name] = &made
			order = append(order, name)
		}
		return &StepCall{Kind: "model", Name: name, Args: call.Args, Line: call.Line}
	}
	copied := *program
	copied.Workflows = nil
	for _, workflow := range program.Workflows {
		changedWorkflow := *workflow
		changedWorkflow.Steps = nil
		for _, step := range workflow.Steps {
			changed := *step
			changed.Call = lower(step, step.Call, "")
			changed.Fallback = lower(step, step.Fallback, "")
			if step.Call.Kind == "think" && len(step.Call.More) > 0 {
				// `using a, b` is only for a step that votes, which this
				// engine does not run yet; the reference refuses it alike.
				thinkFail("Step %s needs a model for %s: it lists several, which is only for a step that ends `vote N`", pyRepr(step.Name), step.Call.Verb)
			}
			if step.Fallback != nil && step.Fallback.Kind == "think" && len(step.Fallback.More) > 0 {
				thinkFail("Step %s needs a model for %s: its fallback lists several models", pyRepr(step.Name), step.Fallback.Verb)
			}
			if len(step.Branches) > 0 {
				changed.Branches = make([]Branch, len(step.Branches))
				for i, branch := range step.Branches {
					branch.Call = lower(step, branch.Call, "")
					changed.Branches[i] = branch
				}
				changed.Call = changed.Branches[0].Call
			}
			changedWorkflow.Steps = append(changedWorkflow.Steps, &changed)
		}
		copied.Workflows = append(copied.Workflows, &changedWorkflow)
	}
	declared := map[string]*RecordDecl{}
	for _, record := range program.Records {
		if declared[record.Name] == nil {
			declared[record.Name] = record
		}
	}
	copied.Records = append([]*RecordDecl{}, program.Records...)
	var names []string
	for name := range needed {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		fields := builtInRecordFields[name]
		existing := declared[name]
		if existing == nil {
			copied.Records = append(copied.Records, &RecordDecl{Name: name, Fields: fields})
			continue
		}
		same := len(existing.Fields) == len(fields) && len(existing.Methods) == 0
		for i := 0; same && i < len(fields); i++ {
			same = existing.Fields[i] == fields[i]
		}
		if !same {
			var shape []string
			for _, field := range fields {
				shape = append(shape, field.Name+": "+field.Type)
			}
			thinkFail("Record %s is built in as {%s}; rename the record declared in this program", pyRepr(name), strings.Join(shape, ", "))
		}
	}
	taken := map[string]bool{}
	for _, model := range program.Models {
		taken[model.Name] = true
	}
	copied.Models = append([]*ModelDecl{}, program.Models...)
	for _, name := range order {
		if !taken[name] {
			copied.Models = append(copied.Models, generated[name])
		}
	}
	return &copied, ""
}

// brokenPromise is what a thinking step's answer breaks, beyond its record
// shape: check_answer in kosika/thinking.py.
func brokenPromise(model *ModelDecl, arguments map[string]Value, answer *Record) string {
	if model.Verb == "classify" {
		label := answer.Fields["label"]
		choices, _ := arguments["choices"].([]Value)
		found := false
		for _, choice := range choices {
			found = found || valueEqual(choice, label)
		}
		if !found {
			var shown []string
			for _, choice := range choices {
				shown = append(shown, pyReprValue(choice))
			}
			return fmt.Sprintf("classify answered %s, which is not one of the choices [%s]", pyReprValue(label), strings.Join(shown, ", "))
		}
	}
	if model.Verb == "classify" || model.Verb == "decide" {
		confidence := answer.Fields["confidence"]
		if compareNumbers(confidence, bigInt(0)) < 0 || compareNumbers(confidence, bigInt(1)) > 0 {
			return fmt.Sprintf("%s answered confidence %s, which is not from 0 through 1", model.Verb, pyReprValue(confidence))
		}
	}
	return ""
}

// pyReprValue writes a text or a number as Python's repr does.
func pyReprValue(v Value) string {
	if text, ok := v.(string); ok {
		return pyRepr(text)
	}
	return pyNumber(v)
}
