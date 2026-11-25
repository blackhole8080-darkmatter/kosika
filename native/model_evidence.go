package main

// `use_model ... with evidence`: the quote rule a thinking step can ask for,
// on an ordinary model step. As lower_model_evidence in
// kosika/model_evidence.py does, a call that asks for evidence is made a call
// of a model named after the one it asked, plus "/evidence", whose Provider
// is that model. Schema, checking, trace and replay all already read evidence
// from a model declaration, so nothing downstream needs to know the rule was
// asked for on a plain step rather than by a verb.
//
// A program that asks for no evidence is returned as it came, so its hash and
// its trace are exactly what they were.

import (
	"fmt"
)

// evidenceInput is the rule a plain model step's quotes follow: every quote
// must appear in the text of the call's own arguments.
const evidenceInput = "input"

type evidenceRefusal struct{ message string }

// evidenceFail ends the lowering, saying why in words a person can act on.
func evidenceFail(format string, args ...any) {
	panic(&evidenceRefusal{fmt.Sprintf(format, args...)})
}

// stepCalls are every call a step can make: its own, its `otherwise`, and
// each branch of a match.
func stepCalls(step *Step) []*StepCall {
	calls := []*StepCall{step.Call, step.Fallback}
	for _, branch := range step.Branches {
		calls = append(calls, branch.Call)
	}
	return calls
}

// LowerModelEvidence gives back the program with every model call that asked
// for evidence made a call of the model generated for it, or why that cannot
// be done.
func LowerModelEvidence(program *Program) (lowered *Program, refused string) {
	asks := false
	for _, workflow := range program.Workflows {
		for _, step := range workflow.Steps {
			for _, call := range stepCalls(step) {
				asks = asks || (call != nil && call.Kind == "model" && call.Evidence)
			}
		}
	}
	if !asks {
		return program, ""
	}
	defer func() {
		if caught := recover(); caught != nil {
			failure, ok := caught.(*evidenceRefusal)
			if !ok {
				panic(caught)
			}
			lowered, refused = nil, failure.message
		}
	}()
	declared := map[string]*ModelDecl{}
	taken := map[string]bool{}
	for _, model := range program.Models {
		if declared[model.Name] == nil {
			declared[model.Name] = model
		}
		taken[model.Name] = true
	}
	records := map[string]*RecordDecl{}
	for _, record := range program.Records {
		records[record.Name] = record
	}
	generated := map[string]*ModelDecl{}
	var order []string
	lower := func(step *Step, call *StepCall) *StepCall {
		if call == nil || call.Kind != "model" || !call.Evidence {
			return call
		}
		base := declared[call.Name]
		if base == nil {
			// An undeclared model is the checker's failure to report, with its
			// own message; guessing a shape here would hide it.
			return call
		}
		if base.Evidence != "" {
			evidenceFail("Step %s cannot give evidence: model %s already answers with quotes", pyRepr(step.Name), pyRepr(base.Name))
		}
		if base.Plain {
			evidenceFail("Step %s cannot give evidence: model %s answers in plain words, so there is no record to carry a quote", pyRepr(step.Name), pyRepr(base.Name))
		}
		if record := records[base.Output]; record != nil {
			for _, field := range record.Fields {
				if field.Name == evidenceKey {
					evidenceFail("Step %s cannot give evidence: record %s has its own field named %s", pyRepr(step.Name), pyRepr(base.Output), pyRepr(evidenceKey))
				}
			}
		}
		name := base.Name + "/evidence"
		if generated[name] == nil && !taken[name] {
			made := *base
			made.Name, made.Provider, made.Evidence = name, base.Name, evidenceInput
			// The generated model is never the default a thinking step
			// reaches for: it is only ever asked by name.
			made.Default = false
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
			call := lower(step, step.Call)
			fallback := lower(step, step.Fallback)
			same := call == step.Call && fallback == step.Fallback
			changed := *step
			changed.Call, changed.Fallback = call, fallback
			if len(step.Branches) > 0 {
				changed.Branches = make([]Branch, len(step.Branches))
				lifted := same
				for i, branch := range step.Branches {
					branch.Call = lower(step, branch.Call)
					lifted = lifted && branch.Call == step.Branches[i].Call
					changed.Branches[i] = branch
				}
				changed.Call = keptBranch(changed.Branches)
				same = lifted
			}
			if same {
				changedWorkflow.Steps = append(changedWorkflow.Steps, step)
				continue
			}
			changedWorkflow.Steps = append(changedWorkflow.Steps, &changed)
		}
		copied.Workflows = append(copied.Workflows, &changedWorkflow)
	}
	copied.Models = append([]*ModelDecl{}, program.Models...)
	for _, name := range order {
		copied.Models = append(copied.Models, generated[name])
	}
	return &copied, ""
}
