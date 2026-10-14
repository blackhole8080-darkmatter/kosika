package main

// Running a workflow: its steps in order, each binding what it gives, the
// last one's result being the workflow's. It follows _run_workflow in
// kosika/runtime.py. A tool is reached only through a test's scripted
// answers; this engine reaches no real tool yet.

import (
	"fmt"
	"math"
	"math/big"
	"sort"
	"strings"
)

// A scriptedTool gives a test's answers for a tool, in order.
type scriptedTool struct {
	name    string
	answers []Value
	asked   int
}

// A run is one workflow run from a test, with the workflows it calls.
type run struct {
	machine    *Machine
	checker    *checker
	tools      map[string]*scriptedTool
	models     map[string]*scriptedTool
	toolCalls  int
	maxTools   int
	modelCalls int
	maxModels  int
}

func newRun(program *Program, tools, models map[string]*scriptedTool, workflow *WorkflowDecl) *run {
	limits := map[string]int{"tool_calls": 256, "model_calls": 64}
	// A workflow's budget only ever tightens the limit of whoever runs it.
	for _, item := range workflow.Budget {
		if whole, ok := item.Amount.(*big.Int); ok && whole.IsInt64() {
			if current, counted := limits[item.Kind]; counted && int(whole.Int64()) < current {
				limits[item.Kind] = int(whole.Int64())
			}
		}
	}
	return &run{machine: newMachine(program), checker: newChecker(program), tools: tools, models: models,
		maxTools: limits["tool_calls"], maxModels: limits["model_calls"]}
}

// preflight refuses, before any step runs, a workflow that would use a
// tool that changes the world: a test has no recorded approval.
func (r *run) preflight(workflow *WorkflowDecl) {
	// A call reached only through a match may never be made, so a tool in
	// it is not refused here; the call itself refuses when it is made.
	type pending struct {
		call   *StepCall
		chosen bool
	}
	var queue []pending
	add := func(steps []*Step, chosen bool) {
		for _, step := range steps {
			if len(step.Branches) > 0 {
				for _, branch := range step.Branches {
					queue = append(queue, pending{branch.Call, true})
				}
				continue
			}
			queue = append(queue, pending{step.Call, chosen})
			if step.Fallback != nil {
				queue = append(queue, pending{step.Fallback, chosen})
			}
		}
	}
	add(workflow.Steps, false)
	visited := map[string]bool{workflow.Name + "|false": true}
	for len(queue) > 0 {
		item := queue[0]
		queue = queue[1:]
		switch item.call.Kind {
		case "workflow":
			name := item.call.Name
			key := fmt.Sprintf("%s|%t", name, item.chosen)
			if !visited[key] && !visited[name+"|false"] {
				visited[key] = true
				add(r.checker.workflows[name].Steps, item.chosen)
			}
		case "tool":
			if tool := r.checker.tools[item.call.Name]; tool.Access == "execute" && !item.chosen {
				panic(&budgetError{fmt.Sprintf("Workflow tool %s requires a verified approval record", pyRepr(tool.Name))})
			}
		}
	}
}

func (r *run) workflow(workflow *WorkflowDecl, inputs map[string]Value, depth int) Value {
	if depth > 64 {
		runFail("Workflow call depth exceeds 32")
	}
	wanted := fieldNames(workflow.Params)
	var given []string
	for name := range inputs {
		given = append(given, name)
	}
	if !sameNames(given, wanted) {
		sort.Strings(wanted)
		runFail(fmt.Sprintf("Workflow requires input parameters %s", pyList(wanted)))
	}
	for _, param := range workflow.Params {
		r.validate(inputs[param.Name], param.Type, "$")
	}
	if depth == 0 {
		r.preflight(workflow)
	}
	environment := newScope(nil)
	for name, value := range inputs {
		environment.vars[name] = value
	}
	at := frame{environment, nil, 0}
	var result Value = newRecord()
	for _, step := range workflow.Steps {
		value, failure := r.attempt(step, step.Call, environment, at, depth)
		if failure != nil {
			// `otherwise`: a failure of the work hands the step to its
			// fallback. A spent budget or a missing approval is not one.
			if step.Fallback == nil {
				panic(failure)
			}
			value = r.call(step.Fallback, environment, at, depth, 0)
		}
		result = value
		environment.vars[step.Binding] = result
	}
	r.validate(result, workflow.Out, "$")
	return result
}

// attempt makes a step's call, or the one its match picks, and gives the
// failure of the work instead of panicking with it.
func (r *run) attempt(step *Step, call *StepCall, environment *Scope, at frame, depth int) (value Value, failure *RunError) {
	defer func() {
		if caught := recover(); caught != nil {
			if problem, ok := caught.(*RunError); ok {
				failure = problem
				return
			}
			panic(caught)
		}
	}()
	if len(step.Branches) > 0 && step.Subject != nil {
		chosen := r.machine.eval(step.Subject, at)
		for _, branch := range step.Branches {
			if branch.Any || valueEqual(branch.Pattern, chosen) {
				call = branch.Call
				break
			}
		}
	}
	return r.call(call, environment, at, depth, step.Retries), nil
}

func (r *run) call(call *StepCall, environment *Scope, at frame, depth, retries int) Value {
	args := resolveUnnamed(call.Args, r.checker.stepParams(call))
	arguments := map[string]Value{}
	for _, arg := range args {
		arguments[arg.Name] = r.machine.eval(arg.Value, at)
	}
	switch call.Kind {
	case "function":
		if value, bound := environment.lookup(call.Name); bound {
			closure, ok := value.(*Closure)
			if !ok {
				runFail("Invalid function value")
			}
			return r.machine.invoke(closure.Decl, arguments, closure.Captured, closure.Funcs, 0)
		}
		return r.machine.invoke(r.machine.functions[call.Name], arguments, nil, nil, 0)
	case "workflow":
		return r.workflow(r.checker.workflows[call.Name], arguments, depth+1)
	}
	if call.Kind == "model" {
		return r.model(r.checker.models[call.Name], arguments, retries)
	}
	return r.tool(r.checker.tools[call.Name], arguments)
}

func (r *run) tool(tool *ToolDecl, arguments map[string]Value) Value {
	if r.toolCalls >= r.maxTools {
		panic(&budgetError{fmt.Sprintf("tool calls budget exceeds %d", r.maxTools)})
	}
	r.toolCalls++
	if tool.Access == "execute" {
		// Not a failure of the work: no fallback may answer it.
		panic(&budgetError{fmt.Sprintf("Workflow tool %s requires a verified approval record", pyRepr(tool.Name))})
	}
	input := newRecord()
	for name, value := range arguments {
		input.set(name, value)
	}
	if tool.Input == "" {
		if len(input.Keys) > 0 {
			keys := append([]string{}, input.Keys...)
			sort.Strings(keys)
			runFail(fmt.Sprintf("$: unknown properties %s", pyList(keys)))
		}
	} else {
		r.validate(input, tool.Input, "$")
	}
	var outcome Value
	if scripted := r.tools[tool.Name]; scripted != nil {
		if scripted.asked >= len(scripted.answers) {
			runFail(fmt.Sprintf("Scripted tool %s has no answer left", pyRepr(scripted.name)))
		}
		scripted.asked++
		outcome = scripted.answers[scripted.asked-1]
	} else {
		outcome = mockTool(tool)
	}
	r.validate(outcome, tool.Output, "$")
	return outcome
}

// model asks a model for one answer it accepts: an answer is the next one
// its test scripts, completed and held to the model's output record. A
// refused answer is asked for again, up to `retries` more times, as
// _ask_model in kosika/runtime.py does.
func (r *run) model(model *ModelDecl, arguments map[string]Value, retries int) Value {
	input := newRecord()
	for name, value := range arguments {
		input.set(name, value)
	}
	r.validate(input, model.Input, "$")
	rejected := 0
	for {
		if r.modelCalls >= r.maxModels {
			panic(&budgetError{fmt.Sprintf("model calls budget exceeds %d", r.maxModels)})
		}
		r.modelCalls++
		scripted := r.models[model.Name]
		scripted.asked++
		if scripted.asked > len(scripted.answers) {
			runFail("Scripted model provider has no response left")
		}
		answer := r.fillAbsent(copyValue(scripted.answers[scripted.asked-1]), model.Output)
		problem := r.problem(answer, model.Output, "$")
		if problem == "" {
			return answer
		}
		if rejected >= retries {
			runFail("Invalid structured model output: " + problem)
		}
		rejected++
	}
}

// copyValue is a scripted answer as the model's JSON gives it back: a
// fresh copy, so that completing it changes nothing in the test.
func copyValue(v Value) Value {
	switch x := v.(type) {
	case []Value:
		items := make([]Value, len(x))
		for i, item := range x {
			items[i] = copyValue(item)
		}
		return items
	case *Record:
		record := newRecord()
		for _, key := range x.Keys {
			record.set(key, copyValue(x.Fields[key]))
		}
		return record
	}
	return v
}

// fillAbsent reads a field the model left out as null, where null is
// allowed: fill_absent in kosika/model_steps.py.
func (r *run) fillAbsent(value Value, typeName string) Value {
	if isFunctionType(typeName) {
		return value
	}
	if strings.HasSuffix(typeName, "[]") {
		items, ok := value.([]Value)
		if !ok {
			return value
		}
		for i, item := range items {
			items[i] = r.fillAbsent(item, typeName[:len(typeName)-2])
		}
		return items
	}
	record, ok := value.(*Record)
	if !ok {
		return value
	}
	if strings.HasSuffix(typeName, "?") {
		// An optional record is "this record, or null": look inside it.
		inner := typeName[:len(typeName)-1]
		if r.checker.records[inner] != nil {
			return r.fillAbsent(value, inner)
		}
		return value
	}
	declared := r.checker.records[typeName]
	if declared == nil {
		return value
	}
	for _, field := range declared.Fields {
		if current, has := record.Fields[field.Name]; has {
			record.Fields[field.Name] = r.fillAbsent(current, field.Type)
		} else if isOptional(field.Type) || field.Type == "Null" {
			record.set(field.Name, nil)
		}
	}
	return record
}

// mockTool is the reference's fallback for a tool no one answers: two
// operations of its first example, and a refusal for anything else.
func mockTool(tool *ToolDecl) Value {
	if tool.Source == "local_mock" && tool.Operation == "get_disk_info" {
		disk := newRecord()
		disk.set("mount", "/")
		disk.set("used_percent", 42.0)
		return disk
	}
	if tool.Source == "local_mock" && tool.Operation == "get_structured_info" {
		metadata := newRecord()
		metadata.set("name", "root-disk")
		info := newRecord()
		info.set("mount", "/")
		info.set("tags", []Value{"disk", "local"})
		info.set("metadata", metadata)
		return info
	}
	runFail(fmt.Sprintf("Mock MCP operation is not allowlisted: %s/%s", tool.Source, tool.Operation))
	return nil
}

// pyTypeName is what Python calls a value's type, for a validation message.
func pyTypeName(v Value) string {
	switch v.(type) {
	case nil:
		return "NoneType"
	case bool:
		return "bool"
	case string:
		return "str"
	case *big.Int:
		return "int"
	case float64:
		return "float"
	case []Value:
		return "list"
	}
	return "dict"
}

// validate holds a value to the schema the reference makes from a type,
// with its messages: kosika/schema.py.
func (r *run) validate(value Value, typeName, path string) {
	if problem := r.problem(value, typeName, path); problem != "" {
		runFail(problem)
	}
}

func (r *run) problem(value Value, typeName, path string) string {
	if isFunctionType(typeName) {
		if _, ok := value.(*Closure); ok {
			return ""
		}
		return fmt.Sprintf("%s: expected object, got %s", path, pyTypeName(value))
	}
	expected := map[string]string{"Text": "string", "Number": "number", "Bool": "boolean", "Null": "null"}
	if kind, primitive := expected[typeName]; primitive {
		matches := false
		switch x := value.(type) {
		case string:
			matches = kind == "string"
		case bool:
			matches = kind == "boolean"
		case nil:
			matches = kind == "null"
		case *big.Int:
			matches = kind == "number"
		case float64:
			matches = kind == "number" && !math.IsInf(x, 0) && !math.IsNaN(x)
		}
		if !matches {
			return fmt.Sprintf("%s: expected %s, got %s", path, kind, pyTypeName(value))
		}
		return ""
	}
	if strings.HasSuffix(typeName, "?") {
		first := r.problem(value, typeName[:len(typeName)-1], path)
		if first == "" {
			return ""
		}
		second := r.problem(value, "Null", path)
		if second == "" {
			return ""
		}
		return fmt.Sprintf("%s: matches no allowed form (%s; %s)", path, first, second)
	}
	if strings.HasSuffix(typeName, "{}") {
		record, ok := value.(*Record)
		if !ok {
			return fmt.Sprintf("%s: expected object, got %s", path, pyTypeName(value))
		}
		for _, key := range record.Keys {
			if problem := r.problem(record.Fields[key], typeName[:len(typeName)-2], path+"."+key); problem != "" {
				return problem
			}
		}
		return ""
	}
	if strings.HasSuffix(typeName, "[]") {
		items, ok := value.([]Value)
		if !ok {
			return fmt.Sprintf("%s: expected array, got %s", path, pyTypeName(value))
		}
		for i, item := range items {
			if problem := r.problem(item, typeName[:len(typeName)-2], fmt.Sprintf("%s[%d]", path, i)); problem != "" {
				return problem
			}
		}
		return ""
	}
	declared := r.checker.records[typeName]
	record, ok := value.(*Record)
	if !ok {
		return fmt.Sprintf("%s: expected object, got %s", path, pyTypeName(value))
	}
	for _, field := range declared.Fields {
		if _, has := record.Fields[field.Name]; !has {
			return fmt.Sprintf("%s: missing required property %s", path, pyRepr(field.Name))
		}
	}
	for _, field := range declared.Fields {
		if problem := r.problem(record.Fields[field.Name], field.Type, path+"."+field.Name); problem != "" {
			return problem
		}
	}
	var unknown []string
	for _, key := range record.Keys {
		if _, known := fieldType(declared.Fields, key); !known {
			unknown = append(unknown, key)
		}
	}
	if len(unknown) > 0 {
		sort.Strings(unknown)
		return fmt.Sprintf("%s: unknown properties %s", path, pyList(unknown))
	}
	return ""
}
