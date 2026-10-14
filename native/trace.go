package main

// Traces: what a run did, one JSON event a line, as kosika/runtime.py
// writes them (trace version 5), so that the reference's `kosika replay`
// can check a run of this engine, and this engine's `replay` the
// reference's. Each function call is a call event and a return event; the
// calls made working out its arguments are its expression events, and the
// calls made in its body are its return's events.

import (
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

func timestamp() string {
	return time.Now().UTC().Format("2006-01-02T15:04:05.000000+00:00")
}

// event makes one trace event: its name, the time, and its fields.
func event(name string, pairs ...any) *Record {
	record := newRecord()
	record.set("event", name)
	record.set("timestamp", timestamp())
	for i := 0; i+1 < len(pairs); i += 2 {
		record.set(pairs[i].(string), pairs[i+1])
	}
	return record
}

func eventList(events []Value) []Value {
	if events == nil {
		return []Value{}
	}
	return events
}

// tracer is what a machine needs to record its function calls.
type tracer struct {
	events    *[]Value // where the next event goes; nil when not tracing
	checker   *checker
	qualified map[*FuncDecl]string
}

func newTracer(c *checker) *tracer {
	t := &tracer{checker: c, qualified: map[*FuncDecl]string{}}
	for name, decl := range c.functions {
		t.qualified[decl] = name
	}
	return t
}

func (t *tracer) name(decl *FuncDecl) string {
	if name, ok := t.qualified[decl]; ok {
		return name
	}
	return decl.Name
}

// collect works something out with the calls it makes gathered on their
// own: the expression events of the call that follows.
func (m *Machine) collect(work func()) []Value {
	if m.tracing == nil || m.tracing.events == nil {
		work()
		return nil
	}
	saved := m.tracing.events
	gathered := []Value{}
	m.tracing.events = &gathered
	defer func() { m.tracing.events = saved }()
	work()
	return gathered
}

// callTraced is invoke, with the call recorded when the run is traced: a
// call and a return event, put down only when the call returns, as the
// reference puts them down.
func (m *Machine) callTraced(decl *FuncDecl, arguments, captured map[string]Value, funcs map[string]*FuncDecl, depth int, argumentEvents []Value) Value {
	if m.tracing == nil || m.tracing.events == nil {
		return m.invoke(decl, arguments, captured, funcs, depth)
	}
	saved := m.tracing.events
	body := []Value{}
	m.tracing.events = &body
	defer func() { m.tracing.events = saved }()
	result := m.invoke(decl, arguments, captured, funcs, depth)
	call, returned := m.functionEvents(decl, arguments, captured, result, argumentEvents, body)
	*saved = append(*saved, call, returned)
	return result
}

// functionEvents are a call's two events. A nested function is named and
// given its captures as the reference's lowering names and gives them.
func (m *Machine) functionEvents(decl *FuncDecl, arguments, captured map[string]Value, result Value, argumentEvents, body []Value) (*Record, *Record) {
	t := m.tracing
	given := newRecord()
	properties := newRecord()
	var required []Value
	for _, param := range decl.Params {
		given.set(param.Name, arguments[param.Name])
		properties.set(param.Name, t.checker.schemaFor(param.Type))
		required = append(required, param.Name)
	}
	for _, capture := range t.checker.captures[decl] {
		given.set(capture.Shown, captured[capture.Name])
		properties.set(capture.Shown, t.checker.schemaFor(capture.Type))
		required = append(required, capture.Shown)
	}
	if required == nil {
		required = []Value{}
	}
	input := newRecord()
	input.set("type", "object")
	input.set("properties", properties)
	input.set("required", required)
	input.set("additionalProperties", false)
	name := t.name(decl)
	call := event("function_call", "function", name, "arguments", given, "input_schema", input,
		"output_schema", t.checker.schemaFor(decl.Out), "expression_events", eventList(argumentEvents))
	returned := event("function_return", "function", name, "result", result, "events", eventList(body))
	return call, returned
}

// writeTrace writes a run's events, keys in order, one a line.
func writeTrace(path string, events []Value) error {
	var lines []string
	for _, item := range events {
		lines = append(lines, pyJSON(item, true))
	}
	if dir := filepath.Dir(path); dir != "" {
		_ = os.MkdirAll(dir, 0o755)
	}
	temporary := path + ".kosika-trace"
	if err := os.WriteFile(temporary, []byte(strings.Join(lines, "\n")+"\n"), 0o644); err != nil {
		return err
	}
	return os.Rename(temporary, path)
}

// programHash names the program a trace was made from. It is a hash of
// the program's text, not the reference's hash of its tree; a trace only
// needs it to say that a workflow it called is of the same program.
func programHash(source string) string {
	sum := sha256.Sum256([]byte("kosika-native\x00" + source))
	return hex.EncodeToString(sum[:])
}

// execution is the run's limits and what it used, as run_complete records.
func (r *run) execution(started time.Time) *Record {
	limits := newRecord()
	limits.set("deadline_seconds", nil)
	limits.set("max_function_calls", bigInt(maxFunctionCalls))
	limits.set("max_loop_iterations", bigInt(maxLoopIterations))
	limits.set("max_model_calls", bigInt(r.maxModels))
	limits.set("max_steps", bigInt(10_000))
	limits.set("max_tool_calls", bigInt(r.maxTools))
	used := newRecord()
	used.set("function_calls", bigInt(r.machine.calls))
	used.set("loop_iterations", bigInt(r.machine.loops))
	used.set("model_calls", bigInt(r.modelCalls))
	used.set("steps", bigInt(r.steps))
	used.set("tool_calls", bigInt(r.toolCalls))
	record := newRecord()
	record.set("elapsed_seconds", time.Since(started).Seconds())
	record.set("function_stack", []Value{})
	record.set("limits", limits)
	record.set("used", used)
	return record
}

// failureType is the name the reference gives a failure, for a step's
// recovered_from: the type of the Python exception it raised.
func failureType(message string) string {
	switch {
	case strings.HasPrefix(message, "$") || strings.Contains(message, " is a required property"):
		return "SchemaValidationError"
	case strings.HasPrefix(message, "Invalid structured model output"):
		return "RejectedAnswer"
	case strings.HasPrefix(message, "Scripted model provider"), strings.HasPrefix(message, "Model "):
		return "ModelProviderError"
	case strings.HasPrefix(message, "Invalid structured agent output"):
		return "ValueError"
	case strings.HasPrefix(message, "Agent "):
		return "AgentLoopError"
	case strings.HasPrefix(message, "MCP ") || strings.HasPrefix(message, "Pinned ") || strings.HasPrefix(message, "Declared MCP") || strings.HasPrefix(message, "Invalid result from MCP"):
		return "MCPProtocolError"
	case strings.HasPrefix(message, "Mock MCP") || strings.HasPrefix(message, "Scripted tool"):
		return "RuntimeError"
	case strings.Contains(message, "answered"):
		return "ThinkingAnswerError"
	}
	return "Failure"
}

func sortedRecordKeys(record *Record) []string {
	keys := append([]string{}, record.Keys...)
	sort.Strings(keys)
	return keys
}
